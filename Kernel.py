import numpy as np
import pandas as pd

import datetime, os, queue, sys
from message.Message import Message, MessageType

from util.util import print

class Kernel:

  def __init__(self, kernel_name):
    # kernel_name is for human readers only.
    self.name = kernel_name
    print ("Kernel initialized: {}".format(self.name))

    self.messages = queue.PriorityQueue()

    # currentTime is None until after kernelStarting() event completes
    # for all agents.  This is a pd.Timestamp that includes the date.
    self.currentTime = None

    # Timestamp at which the Kernel was created.  Primarily used to
    # create a unique log directory for this run.
    self.kernelWallClockStart = pd.Timestamp('now')

    # TODO: This is financial, and so probably should not be here...
    self.meanResultByAgentType = {}
    self.agentCountByType = {}


  def runner(self, agents = [], startTime = None, stopTime = None,
             num_simulations = 1, defaultComputationDelay = 1,
             defaultLatency = 1, agentLatency = None, latencyNoise = [ 1.0 ],
             seed = None, oracle = None, log_dir = None):

    # agents must be a list of agents for the simulation,
    #        based on class agent.Agent
    self.agents = agents
    self.startTime = startTime
    self.stopTime = stopTime
    self.seed = seed
    self.oracle = oracle

    if log_dir:
      self.log_dir = log_dir
    else:
      self.log_dir = str(int(self.kernelWallClockStart.timestamp()))

    # The kernel maintains a current time for each agent to allow
    # simulation of per-agent computation delays.  The agent's time
    # is pushed forward (see below) each time it awakens, and it
    # cannot receive new messages/wakeups until the global time
    # reaches the agent's time.  (i.e. it cannot act again while
    # it is still "in the future")

    # This also nicely enforces agents being unable to act before
    # the simulation startTime.
    self.agentCurrentTimes = [self.startTime] * len(agents)

    # agentComputationDelays is in nanoseconds, starts with a default
    # value from config, and can be changed by any agent at any time
    # (for itself only).  It represents the time penalty applied to
    # an agent each time it is awakened  (wakeup or recvMsg).  The
    # penalty applies _after_ the agent acts, before it may act again.
    # TODO: this might someday change to pd.Timedelta objects.
    self.agentComputationDelays = [defaultComputationDelay] * len(agents)

    # If agentLatency is not defined, define it using the defaultLatency.
    # This matrix defines the communication delay between every pair of
    # agents.
    if agentLatency is None:
      self.agentLatency = [[defaultLatency] * len(agents)] * len(agents)
    else:
      self.agentLatency = agentLatency

    # There is a noise model for latency, intended to be a one-sided
    # distribution with the peak at zero.  By default there is no noise
    # (100% chance to add zero ns extra delay).  Format is a list with
    # list index = ns extra delay, value = probability of this delay.
    self.latencyNoise = latencyNoise

    # The kernel maintains an accumulating additional delay parameter
    # for the current agent.  This is applied to each message sent
    # and upon return from wakeup/receiveMessage, in addition to the
    # agent's standard computation delay.  However, it never carries
    # over to future wakeup/receiveMessage calls.  It is useful for
    # staggering of sent messages.
    self.currentAgentAdditionalDelay = 0

    print ("Kernel started: {}".format(self.name))
    print ("Simulation started!")

    for sim in range(num_simulations):
      print ("Starting sim {}".format(sim))

      # Event notification for kernel init (agents should not try to
      # communicate with other agents, as order is unknown).  Agents
      # should initialize any internal resources that may be needed
      # to communicate with other agents during agent.kernelStarting().
      # Kernel passes self-reference for agents to retain, so they can
      # communicate with the kernel in the future (as it does not have
      # an agentID).
      print ("\n--- Agent.kernelInitializing() ---")
      for agent in self.agents:
        agent.kernelInitializing(self)

      # Event notification for kernel start (agents may set up
      # communications or references to other agents, as all agents
      # are guaranteed to exist now).  Agents should obtain references
      # to other agents they require for proper operation (exchanges,
      # brokers, subscription services...).
      print ("\n--- Agent.kernelStarting() ---")
      for agent in self.agents:
        agent.kernelStarting(self.startTime)

      # Set the kernel to its startTime.
      self.currentTime = self.startTime
      print ("\n--- Kernel Clock started ---")
      print ("Kernel.currentTime is now {}".format(self.currentTime))

      # Start processing the Event Queue.
      print ("\n--- Kernel Event Queue begins ---")
      print ("Kernel will start processing messages. ",
             "Queue length: {}".format(len(self.messages.queue)))

      # Track starting wall clock time and total message count for stats at the end.
      eventQueueWallClockStart = pd.Timestamp('now')
      ttl_messages = 0

      # Process messages.
      while not self.messages.empty() and self.currentTime and (self.currentTime <= self.stopTime):
        self.currentTime, event = self.messages.get()
        msg_recipient, msg_type, msg = event

        # Periodically print the simulation time and total messages, even if muted.
        if ttl_messages % 100000 == 0:
          print ("\n--- Simulation time: {}, messages processed: {}, wallclock elapsed: {} ---\n".format(
                         self.fmtTime(self.currentTime), ttl_messages, pd.Timestamp('now') - eventQueueWallClockStart),
                 override=True)

        print ("\n--- Kernel Event Queue pop ---")
        print ("Kernel handling {} message for agent {} at time {}".format(
               msg_type, msg_recipient, self.fmtTime(self.currentTime)))

        ttl_messages += 1

        # In between messages, always reset the currentAgentAdditionalDelay.
        self.currentAgentAdditionalDelay = 0

        # Dispatch message to agent.
        if msg_type == MessageType.WAKEUP:

          # Who requested this wakeup call?
          agent = msg_recipient

          # Test to see if the agent is already in the future.  If so,
          # delay the wakeup until the agent can act again.
          if self.agentCurrentTimes[agent] > self.currentTime:
            # Push the wakeup call back into the PQ with a new time.
            self.messages.put((self.agentCurrentTimes[agent],
                              (msg_recipient, msg_type, msg)))
            print ("Agent in future: wakeup requeued for {}".format(
                 self.fmtTime(self.agentCurrentTimes[agent])))
            continue
            
          # Set agent's current time to global current time for start
          # of processing.
          self.agentCurrentTimes[agent] = self.currentTime

          # Wake the agent.
          agents[agent].wakeup(self.currentTime)

          # Delay the agent by its computation delay plus any transient additional delay requested.
          self.agentCurrentTimes[agent] += pd.Timedelta(self.agentComputationDelays[agent] +
                                                        self.currentAgentAdditionalDelay)

          print ("After wakeup return, agent {} delayed from {} to {}".format(
                 agent, self.fmtTime(self.currentTime), self.fmtTime(self.agentCurrentTimes[agent])))

        elif msg_type == MessageType.MESSAGE:

          # Who is receiving this message?
          agent = msg_recipient

          # Test to see if the agent is already in the future.  If so,
          # delay the message until the agent can act again.
          if self.agentCurrentTimes[agent] > self.currentTime:
            # Push the message back into the PQ with a new time.
            self.messages.put((self.agentCurrentTimes[agent],
                              (msg_recipient, msg_type, msg)))
            print ("Agent in future: message requeued for {}".format(
                 self.fmtTime(self.agentCurrentTimes[agent])))
            #print ("TMP: delayed message was: {}".format(msg))
            continue

          # Set agent's current time to global current time for start
          # of processing.
          self.agentCurrentTimes[agent] = self.currentTime

          # Deliver the message.
          agents[agent].receiveMessage(self.currentTime, msg)

          # Delay the agent by its computation delay plus any transient additional delay requested.
          self.agentCurrentTimes[agent] += pd.Timedelta(self.agentComputationDelays[agent] +
                                                        self.currentAgentAdditionalDelay)

          print ("After receiveMessage return, agent {} delayed from {} to {}".format(
                 agent, self.fmtTime(self.currentTime), self.fmtTime(self.agentCurrentTimes[agent])))

        else:
          raise ValueError("Unknown message type found in queue",
                           "currentTime:", self.currentTime,
                           "messageType:", self.msg.type)

      if self.messages.empty():
        print ("\n--- Kernel Event Queue empty ---")

      if self.currentTime and (self.currentTime > self.stopTime):
        print ("\n--- Kernel Stop Time surpassed ---")

      # Record wall clock stop time and elapsed time for stats at the end.
      eventQueueWallClockStop = pd.Timestamp('now')

      eventQueueWallClockElapsed = eventQueueWallClockStop - eventQueueWallClockStart

      # Event notification for kernel end (agents may communicate with
      # other agents, as all agents are still guaranteed to exist).
      # Agents should not destroy resources they may need to respond
      # to final communications from other agents.
      print ("\n--- Agent.kernelStopping() ---")
      for agent in agents:
        agent.kernelStopping()

      # Event notification for kernel termination (agents should not
      # attempt communication with other agents, as order of termination
      # is unknown).  Agents should clean up all used resources as the
      # simulation program may not actually terminate if num_simulations > 1.
      print ("\n--- Agent.kernelTerminating() ---")
      for agent in agents:
        agent.kernelTerminating()

      print ("Event Queue elapsed: {}, messages: {}, messages per second: {:0.1f}".format(
             eventQueueWallClockElapsed, ttl_messages, 
             ttl_messages / (eventQueueWallClockElapsed / (np.timedelta64(1, 's')))),
             override=True)
      print ("Ending sim {}".format(sim))

    print ("Mean ending value by agent type:", override=True)
    for a in self.meanResultByAgentType:
      value = self.meanResultByAgentType[a]
      count = self.agentCountByType[a]
      print ("{}: {:d}".format(a, int(round(value / count))), override=True)

    print ("Simulation ending!", override=True)


  def sendMessage(self, sender = None, recipient = None, msg = None, delay = 0):
    # Called by an agent to send a message to another agent.  The kernel
    # supplies its own currentTime (i.e. "now") to prevent possible
    # abuse by agents.  The kernel will handle computational delay penalties
    # and/or network latency.  The message must derive from the message.Message class.
    # The optional delay parameter represents an agent's request for ADDITIONAL
    # delay (beyond the Kernel's mandatory computation + latency delays) to represent
    # parallel pipeline processing delays (that do not make the agent "busy" and
    # unable to respond to new messages).
    
    if sender is None:
      raise ValueError("sendMessage() called without valid sender ID",
                       "sender:", sender, "recipient:", recipient,
                       "msg:", msg)

    if recipient is None:
      raise ValueError("sendMessage() called without valid recipient ID",
                       "sender:", sender, "recipient:", recipient,
                       "msg:", msg)

    if msg is None:
      raise ValueError("sendMessage() called with message == None",
                       "sender:", sender, "recipient:", recipient,
                       "msg:", msg)

    # Apply the agent's current computation delay to effectively "send" the message
    # at the END of the agent's current computation period when it is done "thinking".
    # NOTE: sending multiple messages on a single wake will transmit all at the same
    # time, at the end of computation.  To avoid this, use Agent.delay() to accumulate
    # a temporary delay (current cycle only) that will also stagger messages.

    # The optional pipeline delay parameter DOES push the send time forward, since it
    # represents "thinking" time before the message would be sent.  We don't use this
    # for much yet, but it could be important later.

    # This means message delay (before latency) is the agent's standard computation delay
    # PLUS any accumulated delay for this wake cycle PLUS any one-time requested delay
    # for this specific message only.
    sentTime = self.currentTime + pd.Timedelta(self.agentComputationDelays[sender] + 
                                               self.currentAgentAdditionalDelay + delay)

    # Apply communication delay per the agentLatency matrix [sender][recipient].
    latency = self.agentLatency[sender][recipient]
    noise = np.random.choice(len(self.latencyNoise), 1, self.latencyNoise)[0]
    deliverAt = sentTime + pd.Timedelta(latency + noise)

    self.messages.put((deliverAt, (recipient, MessageType.MESSAGE, msg)))

    print ("Kernel applied latency {}, noise {}, accumulated delay {}, one-time delay {} on sendMessage from: {} to {}, scheduled for {}".format(
           latency, noise, self.currentAgentAdditionalDelay, delay, self.agents[sender].name, self.agents[recipient].name, self.fmtTime(deliverAt)))
    print ("Message queued: {}".format(msg))


  def setWakeup(self, sender = None, requestedTime = None):
    # Called by an agent to receive a "wakeup call" from the kernel
    # at some requested future time.  Defaults to the next possible
    # timestamp.  Wakeup time cannot be the current time or a past time.
    # Sender is required and should be the ID of the agent making the call.
    # The agent is responsible for maintaining any required state; the
    # kernel will not supply any parameters to the wakeup() call.

    if requestedTime is None:
        requestedTime = self.currentTime + pd.TimeDelta(1)

    if sender is None:
      raise ValueError("setWakeup() called without valid sender ID",
                       "sender:", sender, "requestedTime:", requestedTime)

    if self.currentTime and (requestedTime < self.currentTime):
      raise ValueError("setWakeup() called with requested time not in future",
                       "currentTime:", self.currentTime,
                       "requestedTime:", requestedTime)

    print ("Kernel adding wakeup for agent {} at time {}".format(
           sender, self.fmtTime(requestedTime)))

    self.messages.put((requestedTime,
                      (sender, MessageType.WAKEUP, None)))


  def getAgentComputeDelay(self, sender = None):
    # Allows an agent to query its current computation delay.
    return self.agentComputationDelays[sender]


  def setAgentComputeDelay(self, sender = None, requestedDelay = None):
    # Called by an agent to update its computation delay.  This does
    # not initiate a global delay, nor an immediate delay for the
    # agent.  Rather it sets the new default delay for the calling
    # agent.  The delay will be applied upon every return from wakeup
    # or recvMsg.  Note that this delay IS applied to any messages
    # sent by the agent during the current wake cycle (simulating the
    # messages popping out at the end of its "thinking" time).

    # Note that we DO permit a computation delay of zero, but this should
    # really only be used for special or massively parallel agents.

    # requestedDelay should be in whole nanoseconds.
    if not type(requestedDelay) is int:
      raise ValueError("Requested computation delay must be whole nanoseconds.",
                       "requestedDelay:", requestedDelay)

    # requestedDelay must be non-negative.
    if not requestedDelay >= 0:
      raise ValueError("Requested computation delay must be non-negative nanoseconds.",
                       "requestedDelay:", requestedDelay)

    self.agentComputationDelays[sender] = requestedDelay



  def delayAgent(self, sender = None, additionalDelay = None):
    # Called by an agent to accumulate temporary delay for the current wake cycle.
    # This will apply the total delay (at time of sendMessage) to each message,
    # and will modify the agent's next available time slot.  These happen on top
    # of the agent's compute delay BUT DO NOT ALTER IT.  (i.e. effects are transient)
    # Mostly useful for staggering outbound messages.

    # additionalDelay should be in whole nanoseconds.
    if not type(additionalDelay) is int:
      raise ValueError("Additional delay must be whole nanoseconds.",
                       "additionalDelay:", additionalDelay)

    # additionalDelay must be non-negative.
    if not additionalDelay >= 0:
      raise ValueError("Additional delay must be non-negative nanoseconds.",
                       "additionalDelay:", additionalDelay)

    self.currentAgentAdditionalDelay += additionalDelay



  def findAgentByType(self, type = None):
    # Called to request an arbitrary agent ID that matches the class or base class
    # passed as "type".  For example, any ExchangeAgent, or any NasdaqExchangeAgent.
    # This method is rather expensive, so the results should be cached by the caller!

    for agent in self.agents:
      if isinstance(agent, type):
        return agent.id


  def writeLog (self, sender, dfLog, filename=None):
    # Called by any agent, usually at the very end of the simulation just before
    # kernel shutdown, to write to disk any log dataframe it has been accumulating
    # during simulation.  The format can be decided by the agent, although changes
    # will require a special tool to read and parse the logs.  The Kernel places
    # the log in a unique directory per run, with one filename per agent, also
    # decided by the Kernel using agent type, id, etc.

    # If there are too many agents, placing all these files in a directory might
    # be unfortunate.  Also if there are too many agents, or if the logs are too
    # large, memory could become an issue.  In this case, we might have to take
    # a speed hit to write logs incrementally.

    # If filename is not None, it will be used as the filename.  Otherwise,
    # the Kernel will construct a filename based on the name of the Agent
    # requesting log archival.

    path = os.path.join(".", "log", self.log_dir)

    if filename:
      file = "{}.bz2".format(filename)
    else:
      file = "{}.bz2".format(self.agents[sender].name.replace(" ",""))

    if not os.path.exists(path):
      os.makedirs(path)

    dfLog.to_pickle(os.path.join(path, file), compression='bz2')

 
  @staticmethod
  def fmtTime(simulationTime):
    # The Kernel class knows how to pretty-print time.  It is assumed simulationTime
    # is in nanoseconds since midnight.  Note this is a static method which can be
    # called either on the class or an instance.

    # Try just returning the pd.Timestamp now.
    return (simulationTime)

    ns = simulationTime
    hr = int(ns / (1000000000 * 60 * 60))
    ns -= (hr * 1000000000 * 60 * 60)
    m = int(ns / (1000000000 * 60))
    ns -= (m * 1000000000 * 60)
    s = int(ns / 1000000000)
    ns = int(ns - (s * 1000000000))

    return "{:02d}:{:02d}:{:02d}.{:09d}".format(hr, m, s, ns)

