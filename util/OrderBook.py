# Basic class for an order book for one symbol, in the style of the major US Stock Exchanges.
# List of bid prices (index zero is best bid), each with a list of LimitOrders.
# List of ask prices (index zero is best ask), each with a list of LimitOrders.
import sys

from message.Message import Message
from util.order.LimitOrder import LimitOrder
from util.util import print

from copy import deepcopy
from agent.FinancialAgent import dollarize

class OrderBook:

  # An OrderBook requires an owning agent object, which it will use to send messages
  # outbound via the simulator Kernel (notifications of order creation, rejection,
  # cancellation, execution, etc).
  def __init__ (self, owner, symbol):
    self.owner = owner
    self.symbol = symbol
    self.bids = []
    self.asks = []
    self.last_trade = None

    # Create an empty list of dictionaries to log the full order book depth (price and volume) each time it changes.
    self.book_log = []
    self.quotes_seen = set()


  def handleLimitOrder (self, order):
    # Matches a limit order or adds it to the order book.  Handles partial matches piecewise,
    # consuming all possible shares at the best price before moving on, without regard to
    # order size "fit" or minimizing number of transactions.  Sends one notification per
    # match.
    if order.symbol != self.symbol:
      print ("{} order discarded.  Does not match OrderBook symbol: {}".format(order.symbol, self.symbol))
      return

    if (order.quantity <= 0) or (int(order.quantity) != order.quantity):
      print ("{} order discarded.  Quantity ({}) must be a positive integer.".format(order.symbol, order.quantity))
      return

    matching = True

    self.prettyPrint()

    executed = []

    while matching:
      matched_order = deepcopy(self.executeOrder(order))

      if matched_order:
        # Decrement quantity on new order and notify traders of execution.
        filled_order = deepcopy(order)
        filled_order.quantity = matched_order.quantity
        filled_order.fill_price = matched_order.fill_price

        order.quantity -= filled_order.quantity

        print ("MATCHED: new order {} vs old order {}".format(filled_order, matched_order))
        print ("SENT: notifications of order execution to agents {} and {} for orders {} and {}".format(
               filled_order.agent_id, matched_order.agent_id, filled_order.order_id, matched_order.order_id))

        self.owner.sendMessage(order.agent_id, Message({ "msg": "ORDER_EXECUTED", "order": filled_order }))
        self.owner.sendMessage(matched_order.agent_id, Message({ "msg": "ORDER_EXECUTED", "order": matched_order }))

        # Accumulate the volume and average share price of the currently executing inbound trade.
        executed.append( ( filled_order.quantity, filled_order.fill_price ) )

        if order.quantity <= 0:
          matching = False

      else:
        # No matching order was found, so the new order enters the order book.  Notify the agent.
        self.enterOrder(deepcopy(order))

        print ("ACCEPTED: new order {}".format(order))
        print ("SENT: notifications of order acceptance to agent {} for order {}".format(
               order.agent_id, order.order_id))

        self.owner.sendMessage(order.agent_id, Message({ "msg": "ORDER_ACCEPTED", "order": order }))

        matching = False

    if not matching:
      # Now that we are done executing or accepting this order, log the new best bid and ask.
      if self.bids:
        self.owner.logEvent('BEST_BID', "{},{},{}".format(self.symbol,
                                  dollarize(self.bids[0][0].limit_price),
                                  sum([o.quantity for o in self.bids[0]])))

      if self.asks:
        self.owner.logEvent('BEST_ASK', "{},{},{}".format(self.symbol,
                                dollarize(self.asks[0][0].limit_price),
                                sum([o.quantity for o in self.asks[0]])))

      # Also log the last trade (total share quantity, average share price).
      if executed:
        trade_qty = 0
        trade_price = 0
        for q, p in executed:
          print ("Executed: {} @ {}".format(q, dollarize(p)))
          trade_qty += q
          trade_price += (p*q)

        avg_price = int(round(trade_price / trade_qty))
        print ("Avg: {} @ ${:0.4f}".format(trade_qty, avg_price))
        self.owner.logEvent('LAST_TRADE', "{},${:0.4f}".format(trade_qty, avg_price))

        self.last_trade = avg_price

      # Finally, log the full depth of the order book.
      row = { 'QuoteTime' : self.owner.currentTime }
      for quote in self.quotes_seen:
        row[quote] = 0
      for quote, volume in self.getInsideBids():
        row[quote] = -volume
        self.quotes_seen.add(quote)
      for quote, volume in self.getInsideAsks():
        if quote in row:
          if row[quote] != 0:
            print ("WARNING: THIS IS A REAL PROBLEM: an order book contains bids and asks at the same quote price!", override=True)
        row[quote] = volume
        self.quotes_seen.add(quote)
      self.book_log.append(row)

    self.prettyPrint()


  def executeOrder (self, order):
    # Finds a single best match for this order, without regard for quantity.
    # Returns the matched order or None if no match found.  DOES remove,
    # or decrement quantity from, the matched order from the order book
    # (i.e. executes at least a partial trade, if possible).

    # Track which (if any) existing order was matched with the current order.
    if order.is_buy_order:
      book = self.asks
    else:
      book = self.bids

    # TODO: Simplify?  It is ever possible to actually select an execution match
    # other than the best bid or best ask?  We may not need these execute loops.

    # First, examine the correct side of the order book for a match.
    if not book:
      # No orders on this side.
      return None
    elif not self.isMatch(order, book[0][0]):
      # There were orders on the right side, but the prices do not overlap.
      # Or: bid could not match with best ask, or vice versa.
      # Or: bid offer is below the lowest asking price, or vice versa.
      return None
    else:
      # There are orders on the right side, and the new order's price does fall
      # somewhere within them.  Find the best-price matching order.

      # Current matching is best price then FIFO (at same price).
      # Note that o is a LIST of all orders (oldest at index 0) at this same price.
      for i, o in enumerate(book):
        # The first time we find an order that can match, we take it.
        if self.isMatch(order, o[0]):
          # The matched order might be only partially filled.
          # (i.e. new order is smaller)
          if order.quantity >= o[0].quantity:
            # Consumed entire matched order.
            matched_order = book[i].pop(0)

            # If the matched price now has no orders, remove it completely.
            if not book[i]:
              del book[i]

          else:
            # Consumed only part of matched order.
            matched_order = deepcopy(book[i][0])
            matched_order.quantity = order.quantity

            book[i][0].quantity -= matched_order.quantity

          # When two limit orders are matched, they execute at the price that
          # was being "advertised" in the order book.
          matched_order.fill_price = matched_order.limit_price

          # Return (only the executed portion of) the matched order.
          return matched_order

      # No matching order found.
      return None


  def isMatch (self, order, o):
    # Returns True if order 'o' can be matched against input 'order'.
    if order.is_buy_order == o.is_buy_order:
      print ("WARNING: isMatch() called on orders of same type: {} vs {}".format(order, o),
             override=True)
      return False

    if order.is_buy_order and (order.limit_price >= o.limit_price):
      return True

    if not order.is_buy_order and (order.limit_price <= o.limit_price):
      return True
    
    return False


  def enterOrder (self, order):
    # Enters a limit order into the OrderBook in the appropriate location.
    # This does not test for matching/executing orders -- this function
    # should only be called after a failed match/execution attempt.

    if order.is_buy_order:
      book = self.bids
    else:
      book = self.asks

    if not book:
      # There were no orders on this side of the book.
      book.append([order])
    elif not self.isBetterPrice(order, book[-1][0]) and not self.isEqualPrice(order, book[-1][0]):
      # There were orders on this side, but this order is worse than all of them.
      # (New lowest bid or highest ask.)
      book.append([order])
    else:
      # There are orders on this side.  Insert this order in the correct position in the list.
      # Note that o is a LIST of all orders (oldest at index 0) at this same price.
      for i, o in enumerate(book):
        if self.isBetterPrice(order, o[0]):
          book.insert(i, [order])
          break
        elif self.isEqualPrice(order, o[0]):
          book[i].append(order)
          break


  def cancelOrder (self, order):
    # Attempts to cancel (the remaining, unexecuted portion of) a trade in the order book.
    # By definition, this pretty much has to be a limit order.  If the order cannot be found
    # in the order book (probably because it was already fully executed), presently there is
    # no message back to the agent.  This should possibly change to some kind of failed
    # cancellation message.  (?)  Otherwise, the agent receives ORDER_CANCELLED with the
    # order as the message body, with the cancelled quantity correctly represented as the
    # number of shares that had not already been executed.

    if order.is_buy_order:
      book = self.bids
    else:
      book = self.asks

    # If there are no orders on this side of the book, there is nothing to do.
    if not book: return

    # There are orders on this side.  Find the price level of the order to cancel,
    # then find the exact order and cancel it.
    # Note that o is a LIST of all orders (oldest at index 0) at this same price.
    for i, o in enumerate(book):
      if self.isEqualPrice(order, o[0]):
        # This is the correct price level.
        for ci, co in enumerate(book[i]):
          if order.order_id == co.order_id:
            # Cancel this order.
            cancelled_order = book[i].pop(ci)

            # If the cancelled price now has no orders, remove it completely.
            if not book[i]:
              del book[i]

            print ("CANCELLED: order {}".format(order))
            print ("SENT: notifications of order cancellation to agent {} for order {}".format(
                   cancelled_order.agent_id, cancelled_order.order_id))

            self.owner.sendMessage(order.agent_id, Message({ "msg": "ORDER_CANCELLED", "order": cancelled_order }))

            # We found the order and cancelled it, so stop looking.
            return


  # Get the inside bid price(s) and share volume available at each price, to a limit
  # of "depth".  (i.e. inside price, inside 2 prices)  Returns a list of tuples:
  # list index is best bids (0 is best); each tuple is (price, total shares).
  def getInsideBids (self, depth=sys.maxsize):
    book = []
    for i in range(min(depth, len(self.bids))):
      qty = 0
      price = self.bids[i][0].limit_price
      for o in self.bids[i]:
        qty += o.quantity
      book.append( (price, qty) )

    return book


  # As above, except for ask price(s).
  def getInsideAsks (self, depth=sys.maxsize):
    book = []
    for i in range(min(depth, len(self.asks))):
      qty = 0
      price = self.asks[i][0].limit_price
      for o in self.asks[i]:
        qty += o.quantity
      book.append( (price, qty) )

    return book


  # These could be moved to the LimitOrder class.  We could even operator overload them
  # into >, <, ==, etc.
  def isBetterPrice (self, order, o):
    # Returns True if order has a 'better' price than o.  (That is, a higher bid
    # or a lower ask.)  Must be same order type.
    if order.is_buy_order != o.is_buy_order:
      print ("WARNING: isBetterPrice() called on orders of different type: {} vs {}".format(order, o),
             override=True)
      return False

    if order.is_buy_order and (order.limit_price > o.limit_price):
      return True

    if not order.is_buy_order and (order.limit_price < o.limit_price):
      return True

    return False


  def isEqualPrice (self, order, o):
    return order.limit_price == o.limit_price


  # Print a nicely-formatted view of the current order book.
  def prettyPrint (self, silent=False):
    # Start at the highest ask price and move down.  Then switch to the highest bid price and move down.
    # Show the total volume at each price.  If silent is True, return the accumulated string and print nothing.

    book = "{} order book as of {}\n".format(self.symbol, self.owner.currentTime)
    book += "Last trades: simulated {:0.2f}, historical {:0.2f}\n".format(self.last_trade,
           self.owner.oracle.observePrice(self.symbol, self.owner.currentTime, sigma_n = 0) / 100)

    book += "{:10s}{:10s}{:10s}\n".format('BID','PRICE','ASK')
    book += "{:10s}{:10s}{:10s}\n".format('---','-----','---')

    for quote, volume in self.getInsideAsks()[-1::-1]:
      book += "{:10s}{:10s}{:10s}\n".format("", "{:0.2f}".format(quote/100), "{:d}".format(volume))

    for quote, volume in self.getInsideBids():
      book += "{:10s}{:10s}{:10s}\n".format("{:d}".format(volume), "{:0.2f}".format(quote/100), "")

    if silent: return book

    print (book)

