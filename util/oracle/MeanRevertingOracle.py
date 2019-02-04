### The MeanRevertingOracle requires three parameters: a mean fundamental value,
### a mean reversion coefficient, and a shock variance.  It constructs and retains
### a fundamental value time series for each requested symbol, and provides noisy
### observations of those values upon agent request.  The expectation is that
### agents using such an oracle will know the mean-reverting equation and all
### relevant parameters, but will not know the random shocks applied to the
### sequence at each time step.

### Historical dates are effectively meaningless to this oracle.  It is driven by
### the numpy random number seed contained within the experimental config file.
### This oracle uses the nanoseconds portion of the current simulation time as
### discrete "time steps".  A suggestion: to keep wallclock runtime reasonable,
### have the agents operate for only ~1000 nanoseconds, but interpret nanoseconds
### as seconds or minutes.

import datetime as dt
import numpy as np
import pandas as pd
import os, random, sys

from math import sqrt
from util.util import print


class MeanRevertingOracle:

  def __init__(self, mkt_open, mkt_close, symbols):
    # Symbols must be a dictionary of dictionaries with outer keys as symbol names and
    # inner keys: r_bar, kappa, sigma_s.
    self.mkt_open = mkt_open
    self.mkt_close = mkt_close
    self.symbols = symbols
    self.r = {}

    then = dt.datetime.now()

    for symbol in symbols:
      s = symbols[symbol]
      print ("MeanRevertingOracle computing fundamental value series for {}".format(symbol))
      self.r[symbol] = self.generate_fundamental_value_series(symbol=symbol, **s)

    now = dt.datetime.now()

    print ("MeanRevertingOracle initialized for symbols {}".format(symbols))
    print ("MeanRevertingOracle initialization took {}".format(now - then))


  def generate_fundamental_value_series(self, symbol, r_bar, kappa, sigma_s):
    # Generates the fundamental value series for a single stock symbol.  r_bar is the
    # mean fundamental value, kappa is the mean reversion coefficient, and sigma_s
    # is the shock variance.  (Note: NOT STANDARD DEVIATION.)

    # Turn variance into std.
    sigma_s = sqrt(sigma_s)

    # Create the time series into which values will be projected and initialize the first value.
    date_range = pd.date_range(self.mkt_open, self.mkt_close, closed='left', freq='N')

    s = pd.Series(index=date_range)
    r = np.zeros(len(s.index))
    r[0] = r_bar

    # Predetermine the random shocks for all time steps (at once, for computation speed).
    shock = np.random.normal(scale=sigma_s, size=(r.shape[0]))

    # Compute the mean reverting fundamental value series.
    for t in range(1, r.shape[0]):
      r[t] = max(0, (kappa * r_bar) + ( (1 - kappa) * r[t-1] ) + shock[t])

    # Replace the series values with the fundamental value series.  Round and convert to
    # integer cents.
    s[:] = np.round(r)
    s = s.astype(int)
    
    return (s)


  # Return the daily open price for the symbol given.  In the case of the MeanRevertingOracle,
  # this will simply be the first fundamental value, which is also the fundamental mean.
  # We will use the mkt_open time as given, however, even if it disagrees with this.
  def getDailyOpenPrice (self, symbol, mkt_open=None):
  
    # If we did not already know mkt_open, we should remember it.
    if (mkt_open is not None) and (self.mkt_open is None):
      self.mkt_open = mkt_open
  
    print ("Oracle: client requested {} at market open: {}".format(symbol, self.mkt_open))
  
    open = self.r[symbol].loc[self.mkt_open]
    print ("Oracle: market open price was was {}".format(open))
  
    return open


  # Return a noisy observation of the current fundamental value.  While the fundamental
  # value for a given equity at a given time step does not change, multiple agents
  # observing that value will receive different observations.
  #
  # Only the Exchange or other privileged agents should use noisy=False.
  #
  # sigma_n is experimental observation variance.  NOTE: NOT STANDARD DEVIATION.
  def observePrice(self, symbol, currentTime, sigma_n = 1000):
    if currentTime >= self.mkt_close:
      r_t = self.r[symbol].loc[self.mkt_close - pd.Timedelta('1ns')]
    else:
      r_t = self.r[symbol].loc[currentTime]
 
    # Generate a noisy observation of fundamental value at the current time.
    if sigma_n == 0:
      obs = r_t
    else:
      obs = int(round(np.random.normal(loc=r_t, scale=sqrt(sigma_n))))
 
    print ("Oracle: current fundamental value is {} at {}".format(r_t, currentTime))
    print ("Oracle: giving client value observation {}".format(obs))
 
    # Reminder: all simulator prices are specified in integer cents.
    return obs
 
