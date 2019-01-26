from agent.Agent import Agent
import sys
import traceback

# The FinancialAgent class contains attributes and methods that should be available
# to all agent types (traders, exchanges, etc) in a financial market simulation.
class FinancialAgent(Agent):

  def __init__(self, id, name):
    # Base class init.
    super().__init__(id, name)

  # Used by any subclass to dollarize an int-cents price for printing.
  def dollarize (self, cents):
    return dollarize(cents)

  pass


# Dollarizes int-cents prices for printing.  Defined outside the class for
# utility access by non-agent classes.

def dollarize(cents):
  if type(cents) is list:
    return ( [ dollarize(x) for x in cents ] )
  elif type(cents) is int:
    return "${:0.2f}".format(cents / 100)
  else:
    # If cents is already a float, there is an error somewhere.
    print ("ERROR: dollarize(cents) called without int or list of ints: {}".format(cents))
    traceback.print_stack()
    sys.exit()
