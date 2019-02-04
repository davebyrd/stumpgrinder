from agent.TradingAgent import TradingAgent
from message.Message import Message
from util.util import print

from math import sqrt
import numpy as np
import pandas as pd
import sys

class ZeroIntelligenceAgent(TradingAgent):

  def __init__(self, id, name, symbol, startingCash=100000, sigma_n=1000, 
                     r_bar=100000, kappa=0.05, sigma_s=100000, q_max=10,
                     sigma_pv=5000000, R_min = 0, R_max = 250, eta = 1.0,
                     lambda_a = 0.005):

    # Base class init.
    super().__init__(id, name, startingCash)

    # Store important parameters particular to the ZI agent.
    self.symbol = symbol
    self.sigma_n = sigma_n
    self.r_bar = r_bar
    self.kappa = kappa
    self.sigma_s = sigma_s
    self.q_max = q_max
    self.sigma_pv = sigma_pv
    self.R_min = R_min
    self.R_max = R_max
    self.eta = eta
    self.lambda_a = lambda_a

    # The agent uses this to track whether it has begun its strategy or is still
    # handling pre-market tasks.
    self.trading = False

    # The agent begins in its "complete" state, not waiting for
    # any special event or condition.
    self.state = 'AWAITING_WAKEUP'

    # The agent maintains two priors: r_t and sigma_t.
    self.r_t = r_bar
    self.sigma_t = 0

    # The agent must track its previous wake time, so it knows how many time
    # units have passed.
    self.prev_wake_time = None

    # The agent has a private value for each incremental unit.
    self.theta = [int(x) for x in sorted(
                  np.round(np.random.normal(loc=0, scale=sqrt(sigma_pv), size=(q_max*2))).tolist(),
                  reverse=True)]


  def kernelStarting(self, startTime):
    # self.kernel is set in Agent.kernelInitializing()
    # self.exchangeID is set in TradingAgent.kernelStarting()

    super().kernelStarting(startTime)

    self.oracle = self.kernel.oracle


  def wakeup (self, currentTime):
    # Parent class handles discovery of exchange times and market_open wakeup call.
    super().wakeup(currentTime)

    if not self.mkt_open or not self.mkt_close:
      # TradingAgent handles discovery of exchange times.
      return
    else:
      if not self.trading:
        self.trading = True

        # Time to start trading!
        print ("{} is ready to start trading now.".format(self.name))


    # Steady state wakeup behavior starts here.

    # If we've been told the market has closed for the day, we will only request
    # final price information, then stop.
    if self.mkt_closed and (self.symbol in self.daily_close_price):
      # Market is closed and we already got the daily close price.
      return


    # Schedule a wakeup for the next time this agent should arrive at the market
    # (following the conclusion of its current activity cycle).
    # We do this early in case some of our expected message responses don't arrive.

    # Agents should arrive according to a Poisson process.  This is equivalent to
    # each agent independently sampling its next arrival time from an exponential
    # distribution in alternate Beta formation with Beta = 1 / lambda, where lambda
    # is the mean arrival rate of the Poisson process.
    delta_time = np.random.exponential(scale = 1.0 / self.lambda_a)
    self.setWakeup(currentTime + pd.Timedelta('{}ns'.format(int(round(delta_time)))))
 

    # If the market has closed and we haven't obtained the daily close price yet,
    # do that before we cease activity for the day.  Don't do any other behavior
    # after market close.
    if self.mkt_closed and (not self.symbol in self.daily_close_price):
      self.getCurrentSpread(self.symbol)
      self.state = 'AWAITING_SPREAD'
      return


    # Issue cancel requests for any open orders.  Don't wait for confirmation, as presently
    # the only reason it could fail is that the order already executed.  (But requests won't
    # be generated for those, anyway, unless something strange has happened.)
    self.cancelOrders()


    # The ZI agent doesn't try to maintain a zero position, so there is no need to exit positions
    # as some "active trading" agents might.  It might exit a position based on its order logic,
    # but this will be as a natural consequence of its beliefs.


    # In order to use the SRG "strategic threshold" parameter (eta), the ZI agent needs the current
    # spread (inside bid/ask quote).  It would not otherwise need any trade/quote information.
    self.getCurrentSpread(self.symbol)
    self.state = 'AWAITING_SPREAD'



  def placeOrder (self):
    # Called when it is time for the agent to determine a limit price and place an order.


    # The ZI agent obtains a new noisy observation of the current fundamental value
    # and uses this to update its internal estimates in a Bayesian manner.
    obs_t = self.oracle.observePrice(self.symbol, self.currentTime, sigma_n = self.sigma_n)

    print ("{} observed {} at {}".format(self.name, obs_t, self.currentTime))


    # Flip a coin to decide if we will buy or sell a unit at this time.
    q = self.getHoldings(self.symbol)

    if q >= self.q_max:
      buy = False
      print ("Long holdings limit: ZI will SELL")
    elif q <= -self.q_max:
      buy = True
      print ("Short holdings limit: ZI will BUY")
    else:
      buy = bool(np.random.randint(0,2))
      print ("Coin flip: ZI agent will {}".format("BUY" if buy else "SELL"))


    # Update internal estimates of the current fundamental value and our error of same.

    # If this is our first estimate, treat the previous wake time as "market open".
    if self.prev_wake_time is None: self.prev_wake_time = self.mkt_open

    # First, obtain an intermediate estimate of the fundamental value by advancing
    # time from the previous wake time to the current time, performing mean
    # reversion at each time step.

    # delta must be integer time steps since last wake
    delta = (self.currentTime - self.prev_wake_time) / np.timedelta64(1, 'ns')

    # Update r estimate for time advancement.
    r_tprime  = (1 - (1 - self.kappa) ** delta) * self.r_bar
    r_tprime += ((1 - self.kappa) ** delta) * self.r_t

    # Update sigma estimate for time advancement.
    sigma_tprime  = ((1 - self.kappa) ** (2*delta)) * self.sigma_t
    sigma_tprime += ((1 - (1 - self.kappa)**(2*delta)) / (1 - (1 - self.kappa)**2)) * self.sigma_s

    # Apply the new observation, with "confidence" in the observation inversely proportional
    # to the observation noise, and "confidence" in the previous estimate inversely proportional
    # to the shock variance.
    self.r_t  = (self.sigma_n / (self.sigma_n + sigma_tprime)) * r_tprime
    self.r_t += (sigma_tprime / (self.sigma_n + sigma_tprime)) * obs_t

    self.sigma_t = (self.sigma_n * self.sigma_t) / (self.sigma_n + self.sigma_t)

    # Now having a best estimate of the fundamental at time t, we can make our best estimate
    # of the final fundamental (for time T) as of current time t.  Delta is now the number
    # of time steps remaining until the simulated exchange closes.
    delta = (self.mkt_close - self.currentTime) / np.timedelta64(1, 'ns')
    r_T  = (1 - (1 - self.kappa) ** delta) * self.r_bar
    r_T += ((1 - self.kappa) ** delta) * r_tprime

    # Our final fundamental estimate should be quantized to whole units of value.
    r_T = int(round(r_T))

    # Finally (for the final fundamental estimation section) remember the current
    # time as the previous wake time.
    self.prev_wake_time = self.currentTime

    print ("{} estimates r_T = {} as of {}".format(self.name, r_T, self.currentTime))


    # Determine the agent's total valuation.
    q += (self.q_max - 1)
    theta = self.theta[q+1 if buy else q]
    v = r_T + theta

    print ("{} total unit valuation is {} (theta = {})".format(self.name, v, theta))


    # Select a requested surplus for this trade.
    R = np.random.randint(self.R_min, self.R_max+1)


    # Determine the limit price.
    p = v - R if buy else v + R


    # Either place the constructed order, or if the agent could secure (eta * R) surplus
    # immediately by taking the inside bid/ask, do that instead.
    bid, bid_vol, ask, ask_vol = self.getKnownBidAsk(self.symbol)
    if buy and ask_vol > 0:
      R_ask = v - ask
      if R_ask >= (self.eta * R): p = ask
    elif (not buy) and bid_vol > 0:
      R_bid = bid - v
      if R_bid >= (self.eta * R): p = bid
      

    # Place the order.
    self.placeLimitOrder(self.symbol, 1, buy, p)



  def receiveMessage (self, currentTime, msg):
    # Parent class schedules market open wakeup call once market open/close times are known.
    super().receiveMessage(currentTime, msg)

    # We have been awakened by something other than our scheduled wakeup.
    # If our internal state indicates we were waiting for a particular event,
    # check if we can transition to a new state.

    if self.state == 'AWAITING_SPREAD':
      # We were waiting to receive the current spread/book.  Since we don't currently
      # track timestamps on retained information, we rely on actually seeing a
      # QUERY_SPREAD response message.

      if msg.body['msg'] == 'QUERY_SPREAD':
        # This is what we were waiting for.

        # But if the market is now closed, don't advance to placing orders.
        if self.mkt_closed: return

        # We now have the information needed to place a limit order with the eta
        # strategic threshold parameter.
        self.placeOrder()
        self.state = 'AWAITING_WAKEUP'


  # Internal state and logic specific to this agent subclass.

  # Cancel all open orders.
  # Return value: did we issue any cancellation requests?
  def cancelOrders (self):
    if not self.orders: return False

    for id, order in self.orders.items():
      self.cancelOrder(order)

    return True


  # Do we have non-CASH positions?
  def havePositions (self):
    return len(self.holdings) > 1 or \
           (len(self.holdings) == 1 and 'CASH' not in self.holdings)


  def getWakeFrequency (self):
    return pd.Timedelta(np.random.randint(low = 0, high = 100), unit='ns')

