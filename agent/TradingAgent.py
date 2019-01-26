from agent.FinancialAgent import FinancialAgent
from agent.ExchangeAgent import ExchangeAgent
from message.Message import Message
from util.order.LimitOrder import LimitOrder
from util.util import print

from copy import deepcopy
import numpy as np
import pandas as pd
import random
import sys

class TradingAgent(FinancialAgent):

  def __init__(self, id, name, startingCash):
    # Base class init.
    super().__init__(id, name)

    # We don't yet know when the exchange opens or closes.
    self.mkt_open = None
    self.mkt_close = None

    # Store startingCash in case we want to refer to it for performance stats.
    # It should NOT be modified.  Use the 'CASH' key in self.holdings.
    # 'CASH' is always in cents!  Note that agents are limited by their starting
    # cash, currently without leverage.  Taking short positions is permitted,
    # but does NOT increase the amount of at-risk capital allowed.
    self.startingCash = startingCash

    # TradingAgent has constants to support simulated market orders.
    self.MKT_BUY = sys.maxsize
    self.MKT_SELL = 0

    # The base TradingAgent will track its holdings and outstanding orders.
    # Holdings is a dictionary of symbol -> shares.  CASH is a special symbol
    # worth one dollar per share.  Orders is a dictionary of active, open orders
    # (not cancelled, not fully executed) keyed by order_id.
    self.holdings = { 'CASH' : startingCash }
    self.orders = {}

    # The base TradingAgent also tracks last known prices for every symbol
    # for which it has received as QUERY_LAST_TRADE message.  Subclass
    # agents may use or ignore this as they wish.  Note that the subclass
    # agent must request pricing when it wants it.  This agent does NOT
    # automatically generate such requests, though it has a helper function
    # that can be used to make it happen.
    self.last_trade = {}
    self.daily_close_price = {}

    self.known_bids = {}
    self.known_asks = {}

    # For special logging at the first moment the simulator kernel begins
    # running (which is well after agent init), it is useful to keep a simple
    # boolean flag.
    self.first_wake = True

    # Remember whether we have already passed the exchange close time, as far
    # as we know.
    self.mkt_closed = False

    # TMP
    self.book = ''


  # Simulation lifecycle messages.

  def kernelStarting(self, startTime):
    # self.kernel is set in Agent.kernelInitializing()

    # Find an exchange with which we can place orders.  It is guaranteed
    # to exist by now (if there is one).
    self.exchangeID = self.kernel.findAgentByType(ExchangeAgent)

    print ("Agent {} requested agent of type Agent.ExchangeAgent.  Given Agent ID: {}".format(
           self.id, self.exchangeID))

    # Request a wake-up call as in the base Agent.
    super().kernelStarting(startTime)


  def kernelStopping (self):
    # Always call parent method to be safe.
    super().kernelStopping()

    # Print end of day holdings.
    self.logEvent('FINAL_HOLDINGS', self.fmtHoldings(self.holdings))

    # Mark to market.
    # We may want a separate mark to market function (to use anytime) eventually.
    cash = self.markToMarket(self.holdings)

    self.logEvent('ENDING_CASH', self.dollarize(cash))
    print ("Final holdings for {}: {}.  Marked to market: {}".format(self.name, self.fmtHoldings(self.holdings),
                                                                     self.dollarize(cash)), override=True)
    
    # TODO: Record final results for presentation/debugging.  This is probably bad.
    mytype = str(type(self)).split('.')[-1].split("'")[0]

    if mytype in self.kernel.meanResultByAgentType:
      self.kernel.meanResultByAgentType[mytype] += cash
      self.kernel.agentCountByType[mytype] += 1
    else:
      self.kernel.meanResultByAgentType[mytype] = cash
      self.kernel.agentCountByType[mytype] = 1


  # Simulation participation messages.

  def wakeup (self, currentTime):
    super().wakeup(currentTime)

    if self.first_wake:
      # Log initial holdings.
      #self.logEvent('HOLDINGS_UPDATED', self.fmtHoldings(self.holdings))
      self.logEvent('HOLDINGS_UPDATED', self.holdings)
      self.first_wake = False

    if self.mkt_open is None:
      # Ask our exchange when it opens and closes.
      self.sendMessage(self.exchangeID, Message({ "msg" : "WHEN_MKT_OPEN", "sender": self.id }))
      self.sendMessage(self.exchangeID, Message({ "msg" : "WHEN_MKT_CLOSE", "sender": self.id }))

    # New for MomentumAgent.
    return (self.mkt_open and self.mkt_close) and not self.mkt_closed


  def receiveMessage (self, currentTime, msg):
    super().receiveMessage(currentTime, msg)

    # Do we know the market hours?
    had_mkt_hours = self.mkt_open is not None and self.mkt_close is not None

    # Record market open or close times.
    if msg.body['msg'] == "WHEN_MKT_OPEN":
      self.mkt_open = msg.body['data']

      print ("Recorded market open: {}".format(self.kernel.fmtTime(self.mkt_open)))

    elif msg.body['msg'] == "WHEN_MKT_CLOSE":
      self.mkt_close = msg.body['data']

      print ("Recorded market close: {}".format(self.kernel.fmtTime(self.mkt_close)))

    elif msg.body['msg'] == "ORDER_EXECUTED":
      # Call the orderExecuted method, which subclasses should extend.  This parent
      # class could implement default "portfolio tracking" or "returns tracking"
      # behavior.
      order = msg.body['order']

      self.orderExecuted(order)

    elif msg.body['msg'] == "ORDER_ACCEPTED":
      # Call the orderAccepted method, which subclasses should extend.
      order = msg.body['order']

      self.orderAccepted(order)

    elif msg.body['msg'] == "ORDER_CANCELLED":
      # Call the orderCancelled method, which subclasses should extend.
      order = msg.body['order']

      self.orderCancelled(order)

    elif msg.body['msg'] == "MKT_CLOSED":
      # We've tried to ask the exchange for something after it closed.  Remember this
      # so we stop asking for things that can't happen.

      self.marketClosed()

    elif msg.body['msg'] == 'QUERY_LAST_TRADE':
      # Call the queryLastTrade method, which subclasses may extend.
      # Also note if the market is closed.
      if msg.body['mkt_closed']: self.mkt_closed = True

      self.queryLastTrade(msg.body['symbol'], msg.body['data'])

    elif msg.body['msg'] == 'QUERY_SPREAD':
      # Call the querySpread method, which subclasses may extend.
      if msg.body['mkt_closed']: self.mkt_closed = True

      self.querySpread(msg.body['symbol'], msg.body['data'], msg.body['bids'], msg.body['asks'], msg.body['book'])


    # Now do we know the market hours?
    have_mkt_hours = self.mkt_open is not None and self.mkt_close is not None

    # Once we know the market open and close times, schedule a wakeup call for market open.
    # Only do this once, when we first have both items.
    if have_mkt_hours and not had_mkt_hours:
      # Agents are asked to generate a wake offset from the market open time.  We structure
      # this as a subclass request so each agent can supply an appropriate offset relative
      # to its trading frequency.
      ns_offset = self.getWakeFrequency()

      self.setWakeup(self.mkt_open + ns_offset)


  # Used by any Trading Agent subclass to query the last trade price for a symbol.
  # This activity is not logged.
  def getLastTrade (self, symbol):
    self.sendMessage(self.exchangeID, Message({ "msg" : "QUERY_LAST_TRADE", "sender": self.id,
                                                "symbol" : symbol })) 


  # Used by any Trading Agent subclass to query the current spread for a symbol.
  # This activity is not logged.
  def getCurrentSpread (self, symbol, depth=1):
    self.sendMessage(self.exchangeID, Message({ "msg" : "QUERY_SPREAD", "sender": self.id,
                                                "symbol" : symbol, "depth" : depth })) 


  # Used by any Trading Agent subclass to place a limit order.  Parameters expect:
  # string (valid symbol), int (positive share quantity), bool (True == BUY), float (x.xx price).
  def placeLimitOrder (self, symbol, quantity, is_buy_order, limit_price):
    order = LimitOrder(self.id, self.currentTime, symbol, quantity, is_buy_order, limit_price)

    if quantity > 0:
      # Test if this order can be permitted given our at-risk limits.
      new_holdings = self.holdings.copy()

      q = order.quantity if order.is_buy_order else -order.quantity

      if order.symbol in new_holdings: new_holdings[order.symbol] += q
      else: new_holdings[order.symbol] = q

      # Compute before and after at-risk capital.
      at_risk = self.markToMarket(self.holdings) - self.holdings['CASH']
      new_at_risk = self.markToMarket(new_holdings) - new_holdings['CASH']

      # If at_risk is lower, always allow.  Otherwise, new_at_risk must be below starting cash.
      if (new_at_risk > at_risk) and (new_at_risk > self.startingCash):
        print ("TradingAgent ignored limit order due to at-risk constraints: {}\n{}".format(order, self.fmtHoldings(self.holdings)))
        return

      self.orders[order.order_id] = deepcopy(order)
      self.sendMessage(self.exchangeID, Message({ "msg" : "LIMIT_ORDER", "sender": self.id,
                                                  "order" : order })) 

      # Log this activity.
      self.logEvent('ORDER_SUBMITTED', order)
    else:
      print ("TradingAgent ignored limit order of quantity zero: {}".format(order))


  # Used by any Trading Agent subclass to cancel any order.  The order must currently
  # appear in the agent's open orders list.
  def cancelOrder (self, order):
    self.sendMessage(self.exchangeID, Message({ "msg" : "CANCEL_ORDER", "sender": self.id,
                                                "order" : order })) 

    # Log this activity.
    self.logEvent('CANCEL_SUBMITTED', order)


  # Handles ORDER_EXECUTED messages from an exchange agent.  Subclasses may wish to extend,
  # but should still call parent method for basic portfolio/returns tracking.
  def orderExecuted (self, order):
    print ("Received notification of execution for: {}".format(order))

    # Log this activity.
    self.logEvent('ORDER_EXECUTED', order)

    # At the very least, we must update CASH and holdings at execution time.
    qty = order.quantity if order.is_buy_order else -1 * order.quantity
    sym = order.symbol

    if sym in self.holdings:
      self.holdings[sym] += qty
    else:
      self.holdings[sym] = qty

    if self.holdings[sym] == 0: del self.holdings[sym]

    # As with everything else, CASH holdings are in CENTS.
    self.holdings['CASH'] -= (qty * order.fill_price)
    
    # If this original order is now fully executed, remove it from the open orders list.
    # Otherwise, decrement by the quantity filled just now.  It is _possible_ that due
    # to timing issues, it might not be in the order list (i.e. we issued a cancellation
    # but it was executed first, or something).
    if order.order_id in self.orders:
      o = self.orders[order.order_id]

      if order.quantity >= o.quantity: del self.orders[order.order_id]
      else: o.quantity -= order.quantity

    else:
      print ("Execution received for order not in orders list: {}".format(order))

    print ("After execution, agent open orders: {}".format(self.orders))

    # After execution, log holdings.
    #self.logEvent('HOLDINGS_UPDATED', self.fmtHoldings(self.holdings))
    self.logEvent('HOLDINGS_UPDATED', self.holdings)


  # Handles ORDER_ACCEPTED messages from an exchange agent.  Subclasses may wish to extend.
  def orderAccepted (self, order):
    print ("Received notification of acceptance for: {}".format(order))

    # Log this activity.
    self.logEvent('ORDER_ACCEPTED', order)

    # We may later wish to add a status to the open orders so an agent can tell whether
    # a given order has been accepted or not (instead of needing to override this method).


  # Handles ORDER_CANCELLED messages from an exchange agent.  Subclasses may wish to extend.
  def orderCancelled (self, order):
    print ("Received notification of cancellation for: {}".format(order))

    # Log this activity.
    self.logEvent('ORDER_CANCELLED', order)

    # Remove the cancelled order from the open orders list.  We may of course wish to have
    # additional logic here later, so agents can easily "look for" cancelled orders.  Of
    # course they can just override this method.
    if order.order_id in self.orders:
      del self.orders[order.order_id]
    else:
      print ("Cancellation received for order not in orders list: {}".format(order))


  # Handles MKT_CLOSED messages from an exchange agent.  Subclasses may wish to extend.
  def marketClosed (self):
    print ("Received notification of market closure.")

    # Log this activity.
    self.logEvent('MKT_CLOSED')

    # Remember that this has happened.
    self.mkt_closed = True


  # Handles QUERY_LAST_TRADE messages from an exchange agent.
  def queryLastTrade (self, symbol, price):
    self.last_trade[symbol] = int(round(price * 100))

    print ("Received last trade price of {} for {}.".format(self.last_trade[symbol], symbol))

    if self.mkt_closed:
      # Note this as the final price of the day.
      self.daily_close_price[symbol] = self.last_trade[symbol]

      print ("Received daily close price of {} for {}.".format(self.last_trade[symbol], symbol))


  # Handles QUERY_SPREAD messages from an exchange agent.
  def querySpread (self, symbol, price, bids, asks, book):
    # The spread message now also includes last price for free.
    self.queryLastTrade(symbol, price)

    self.known_bids[symbol] = bids
    self.known_asks[symbol] = asks

    if bids: best_bid, best_bid_qty = (self.dollarize(bids[0][0]), bids[0][1])
    else: best_bid, best_bid_qty = ('No bids', 0)

    if asks: best_ask, best_ask_qty = (self.dollarize(asks[0][0]), asks[0][1])
    else: best_ask, best_ask_qty = ('No asks', 0)

    print ("Received spread of {} @ {} / {} @ {} for {}".format(best_bid_qty, best_bid, best_ask_qty, best_ask, symbol))

    self.logEvent("BID_DEPTH", bids)
    self.logEvent("ASK_DEPTH", asks)
    self.logEvent("IMBALANCE", [sum([x[1] for x in bids]), sum([x[1] for x in asks])])

    self.book = book


  # Utility functions that perform calculations from available knowledge, but implement no
  # particular strategy.


  # Extract the current known best bid and ask.  This does NOT request new information.
  def getKnownBidAsk (self, symbol) :
    bid = self.known_bids[symbol][0][0] if self.known_bids[symbol] else None
    ask = self.known_asks[symbol][0][0] if self.known_asks[symbol] else None

    bid_vol = self.known_bids[symbol][0][1] if self.known_bids[symbol] else 0
    ask_vol = self.known_asks[symbol][0][1] if self.known_asks[symbol] else 0

    return bid, bid_vol, ask, ask_vol


  # Extract the current bid and ask liquidity within a certain proportion of the
  # inside bid and ask.  (i.e. within=0.01 means to report total BID shares
  # within 1% of the best bid price, and total ASK shares within 1% of the best
  # ask price)
  #
  # Returns bid_liquidity, ask_liquidity.  Note that this is from the order book
  # perspective, not the agent perspective.  (The agent would be selling into the
  # bid liquidity, etc.)
  def getKnownLiquidity (self, symbol, within=0.00):
    bid_liq = self.getBookLiquidity(self.known_bids[symbol], within)
    ask_liq = self.getBookLiquidity(self.known_asks[symbol], within)

    print ("Bid/ask liq: {}, {}".format(bid_liq, ask_liq))
    print ("Known bids: {}".format(self.known_bids[self.symbol]))
    print ("Known asks: {}".format(self.known_asks[self.symbol]))

    return bid_liq, ask_liq


  # Helper function for the above.  Checks one side of the known order book.
  def getBookLiquidity (self, book, within):
    liq = 0
    for i, (price, shares) in enumerate(book):
      if i == 0:
        best = price

      # Is this price within "within" proportion of the best price?
      if abs(best - price) <= int(round(best * within)):
        print ("Within {} of {}: {} with {} shares".format(within, best, price, shares))
        liq += shares

    return liq


  # Marks holdings to market (including cash).
  def markToMarket (self, holdings):
    cash = holdings['CASH']

    for symbol, shares in holdings.items():
      if symbol == 'CASH': continue

      value = self.last_trade[symbol] * shares
      cash += value

      self.logEvent('MARK_TO_MARKET', "{} {} @ {} == {}".format(shares, symbol,
                    self.dollarize(self.last_trade[symbol]), self.dollarize(value)))

    self.logEvent('MARKED_TO_MARKET', self.dollarize(cash))

    return cash


  # Gets holdings.  Returns zero for any symbol not held.
  def getHoldings (self, symbol):
    if symbol in self.holdings: return self.holdings[symbol]
    return 0


  # Prints holdings.  Standard dictionary->string representation is almost fine, but it is
  # less confusing to see the CASH holdings in dollars and cents, instead of just integer
  # cents.  We could change to a Holdings object that knows to print CASH "special".
  def fmtHoldings (self, holdings):
    h = ''
    for k,v in sorted(holdings.items()):
      if k == 'CASH': continue
      h += "{}: {}, ".format(k,v)

    # There must always be a CASH entry.
    h += "{}: {}".format('CASH', self.dollarize(holdings['CASH']))
    h = '{ ' + h + ' }'
    return h


  pass
