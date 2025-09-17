#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# Copyright © 2025 kogeler
# SPDX-License-Identifier: Apache-2.0

"""
Protocol state machine for realistic traffic pattern generation
"""

import random
import time
from typing import Dict, List, Any, Optional, Tuple
from enum import Enum
from collections import deque, defaultdict


class ProtocolType(Enum):
    """Supported protocol types"""
    TLS = "tls"
    QUIC = "quic"
    WEBRTC = "webrtc"
    SSH = "ssh"
    HTTP2 = "http2"
    HTTP3 = "http3"
    GENERIC = "generic"


class ConnectionState(Enum):
    """Common connection states"""
    INIT = "init"
    HANDSHAKE = "handshake"
    ESTABLISHED = "established"
    DATA = "data"
    CLOSING = "closing"
    CLOSED = "closed"


class ProtocolStateMachine:
    """
    Protocol state machine for generating realistic traffic patterns.
    Simulates protocol-specific state transitions and behaviors.
    """

    def __init__(self, protocol: str):
        """
        Initialize state machine for a specific protocol.

        Args:
            protocol: Protocol name (tls, quic, webrtc, ssh, http2, http3, generic)
        """
        self.protocol = self._validate_protocol(protocol)
        self.current_state = 'init'
        self.state_transitions = self._build_transitions(protocol)
        self.state_characteristics = self._build_characteristics(protocol)

        # State tracking
        self.state_history = deque(maxlen=100)
        self.state_timers = {}
        self.state_counts = defaultdict(int)

        # Protocol-specific parameters
        self.handshake_complete = False
        self.data_transferred = 0
        self.connection_start = time.time()
        self.last_state_change = time.time()

        # Session parameters
        self.session_id = random.randint(0, 2**32 - 1)
        self.rtt_estimate = 0.02  # 20ms default
        self.congestion_window = 10

    def _validate_protocol(self, protocol: str) -> str:
        """Validate and normalize protocol name."""
        try:
            return ProtocolType(protocol.lower()).value
        except (ValueError, AttributeError):
            return ProtocolType.GENERIC.value

    def _build_transitions(self, protocol: str) -> Dict[str, Dict[str, float]]:
        """
        Build state transition probabilities for the protocol.

        Returns:
            Dictionary mapping states to transition probabilities
        """
        transitions = {
            'tls': {
                'init': {'handshake': 0.95, 'closed': 0.05},
                'handshake': {
                    'handshake': 0.2,  # Multiple handshake messages
                    'data': 0.75,
                    'closing': 0.03,
                    'closed': 0.02
                },
                'data': {
                    'data': 0.94,
                    'closing': 0.05,
                    'closed': 0.01
                },
                'closing': {'closed': 0.9, 'data': 0.1},
                'closed': {'init': 0.8, 'closed': 0.2}
            },
            'quic': {
                'init': {'initial': 0.95, 'closed': 0.05},
                'initial': {
                    'handshake': 0.9,
                    'retry': 0.05,
                    'closed': 0.05
                },
                'retry': {'initial': 0.8, 'closed': 0.2},
                'handshake': {
                    'handshake': 0.3,
                    'application': 0.65,
                    'closing': 0.03,
                    'closed': 0.02
                },
                'application': {
                    'application': 0.96,
                    'closing': 0.03,
                    'closed': 0.01
                },
                'closing': {'closed': 0.95, 'application': 0.05},
                'closed': {'init': 0.7, 'closed': 0.3}
            },
            'webrtc': {
                'init': {'stun': 0.95, 'closed': 0.05},
                'stun': {
                    'stun': 0.3,  # Multiple STUN requests
                    'turn': 0.2,
                    'dtls': 0.45,
                    'closed': 0.05
                },
                'turn': {
                    'turn': 0.2,
                    'dtls': 0.75,
                    'closed': 0.05
                },
                'dtls': {
                    'dtls': 0.2,
                    'srtp': 0.75,
                    'closed': 0.05
                },
                'srtp': {
                    'srtp': 0.90,
                    'rtcp': 0.08,
                    'closing': 0.01,
                    'closed': 0.01
                },
                'rtcp': {
                    'srtp': 0.95,
                    'rtcp': 0.03,
                    'closing': 0.01,
                    'closed': 0.01
                },
                'closing': {'closed': 0.9, 'srtp': 0.1},
                'closed': {'init': 0.6, 'closed': 0.4}
            },
            'ssh': {
                'init': {'handshake': 0.95, 'closed': 0.05},
                'handshake': {
                    'auth': 0.9,
                    'closed': 0.1
                },
                'auth': {
                    'auth': 0.2,  # Multiple auth attempts
                    'session': 0.75,
                    'closed': 0.05
                },
                'session': {
                    'session': 0.93,
                    'channel': 0.05,
                    'closing': 0.01,
                    'closed': 0.01
                },
                'channel': {
                    'session': 0.8,
                    'channel': 0.15,
                    'closing': 0.04,
                    'closed': 0.01
                },
                'closing': {'closed': 0.95, 'session': 0.05},
                'closed': {'init': 0.5, 'closed': 0.5}
            },
            'http2': {
                'init': {'connection': 0.95, 'closed': 0.05},
                'connection': {
                    'settings': 0.9,
                    'closed': 0.1
                },
                'settings': {
                    'stream': 0.85,
                    'settings': 0.1,
                    'closed': 0.05
                },
                'stream': {
                    'stream': 0.7,
                    'data': 0.25,
                    'push': 0.03,
                    'closing': 0.01,
                    'closed': 0.01
                },
                'data': {
                    'data': 0.8,
                    'stream': 0.15,
                    'closing': 0.04,
                    'closed': 0.01
                },
                'push': {
                    'data': 0.7,
                    'stream': 0.25,
                    'closing': 0.04,
                    'closed': 0.01
                },
                'closing': {'closed': 0.9, 'stream': 0.1},
                'closed': {'init': 0.6, 'closed': 0.4}
            },
            'http3': {
                'init': {'quic_handshake': 0.95, 'closed': 0.05},
                'quic_handshake': {
                    'settings': 0.85,
                    'quic_handshake': 0.1,
                    'closed': 0.05
                },
                'settings': {
                    'stream': 0.8,
                    'settings': 0.15,
                    'closed': 0.05
                },
                'stream': {
                    'stream': 0.6,
                    'data': 0.35,
                    'closing': 0.04,
                    'closed': 0.01
                },
                'data': {
                    'data': 0.75,
                    'stream': 0.2,
                    'closing': 0.04,
                    'closed': 0.01
                },
                'closing': {'closed': 0.95, 'stream': 0.05},
                'closed': {'init': 0.7, 'closed': 0.3}
            },
            'generic': {
                'init': {'connecting': 0.9, 'idle': 0.05, 'closed': 0.05},
                'connecting': {
                    'active': 0.85,
                    'idle': 0.1,
                    'closed': 0.05
                },
                'active': {
                    'active': 0.75,
                    'burst': 0.15,
                    'idle': 0.08,
                    'closing': 0.01,
                    'closed': 0.01
                },
                'burst': {
                    'active': 0.7,
                    'burst': 0.2,
                    'idle': 0.08,
                    'closing': 0.01,
                    'closed': 0.01
                },
                'idle': {
                    'active': 0.6,
                    'idle': 0.35,
                    'closing': 0.03,
                    'closed': 0.02
                },
                'closing': {'closed': 0.9, 'active': 0.1},
                'closed': {'init': 0.5, 'closed': 0.5}
            }
        }

        return transitions.get(protocol, transitions['generic'])

    def _build_characteristics(self, protocol: str) -> Dict[str, Dict[str, Any]]:
        """
        Build state characteristics for the protocol.

        Returns:
            Dictionary mapping states to their characteristics
        """
        characteristics = {
            'tls': {
                'init': {
                    'size_range': (0, 0),
                    'interval': 0.0,
                    'burst': False,
                    'bidirectional': False
                },
                'handshake': {
                    'size_range': (100, 2000),
                    'interval': 0.005,
                    'burst': True,
                    'bidirectional': True,
                    'pattern': 'request_response'
                },
                'data': {
                    'size_range': (64, 16384),
                    'interval': 0.02,
                    'burst': False,
                    'bidirectional': True,
                    'pattern': 'stream'
                },
                'closing': {
                    'size_range': (21, 31),  # Alert message
                    'interval': 0.001,
                    'burst': False,
                    'bidirectional': True
                },
                'closed': {
                    'size_range': (0, 0),
                    'interval': 1.0,
                    'burst': False,
                    'bidirectional': False
                }
            },
            'quic': {
                'initial': {
                    'size_range': (1200, 1400),
                    'interval': 0.001,
                    'burst': True,
                    'bidirectional': True,
                    'pattern': 'datagram'
                },
                'handshake': {
                    'size_range': (500, 1400),
                    'interval': 0.003,
                    'burst': True,
                    'bidirectional': True,
                    'pattern': 'request_response'
                },
                'application': {
                    'size_range': (100, 1400),
                    'interval': 0.015,
                    'burst': False,
                    'bidirectional': True,
                    'pattern': 'stream'
                },
                'retry': {
                    'size_range': (100, 200),
                    'interval': 0.1,
                    'burst': False,
                    'bidirectional': False
                }
            },
            'webrtc': {
                'stun': {
                    'size_range': (20, 200),
                    'interval': 0.1,
                    'burst': False,
                    'bidirectional': True,
                    'pattern': 'request_response'
                },
                'turn': {
                    'size_range': (50, 300),
                    'interval': 0.05,
                    'burst': False,
                    'bidirectional': True,
                    'pattern': 'request_response'
                },
                'dtls': {
                    'size_range': (100, 1000),
                    'interval': 0.01,
                    'burst': True,
                    'bidirectional': True,
                    'pattern': 'handshake'
                },
                'srtp': {
                    'size_range': (100, 200),
                    'interval': 0.02,
                    'burst': False,
                    'bidirectional': True,
                    'pattern': 'rtp_stream'
                },
                'rtcp': {
                    'size_range': (70, 90),
                    'interval': 1.0,
                    'burst': False,
                    'bidirectional': True,
                    'pattern': 'periodic'
                }
            },
            'ssh': {
                'handshake': {
                    'size_range': (50, 500),
                    'interval': 0.01,
                    'burst': True,
                    'bidirectional': True,
                    'pattern': 'negotiation'
                },
                'auth': {
                    'size_range': (100, 1000),
                    'interval': 0.02,
                    'burst': False,
                    'bidirectional': True,
                    'pattern': 'challenge_response'
                },
                'session': {
                    'size_range': (32, 1400),
                    'interval': 0.05,
                    'burst': False,
                    'bidirectional': True,
                    'pattern': 'interactive'
                },
                'channel': {
                    'size_range': (100, 1400),
                    'interval': 0.02,
                    'burst': True,
                    'bidirectional': True,
                    'pattern': 'multiplexed'
                }
            },
            'generic': {
                'connecting': {
                    'size_range': (64, 500),
                    'interval': 0.01,
                    'burst': True,
                    'bidirectional': True
                },
                'active': {
                    'size_range': (200, 1200),
                    'interval': 0.02,
                    'burst': False,
                    'bidirectional': True
                },
                'burst': {
                    'size_range': (1000, 1400),
                    'interval': 0.001,
                    'burst': True,
                    'bidirectional': False
                },
                'idle': {
                    'size_range': (64, 128),
                    'interval': 1.0,
                    'burst': False,
                    'bidirectional': False
                }
            }
        }

        # Get protocol-specific or default to generic
        proto_chars = characteristics.get(protocol, characteristics['generic'])

        # Add default values for any missing states
        default_char = {
            'size_range': (64, 1400),
            'interval': 0.1,
            'burst': False,
            'bidirectional': True
        }

        return defaultdict(lambda: default_char, proto_chars)

    def next_state(self) -> str:
        """
        Transition to the next state based on probabilities.

        Returns:
            New state name
        """
        # Record state change time
        self.last_state_change = time.time()

        # Get transitions for current state
        if self.current_state not in self.state_transitions:
            self.current_state = 'init'

        transitions = self.state_transitions[self.current_state]

        # Apply protocol-specific logic
        transitions = self._apply_protocol_logic(transitions)

        # Choose next state
        states = list(transitions.keys())
        weights = list(transitions.values())

        # Normalize weights if needed
        weight_sum = sum(weights)
        if weight_sum > 0:
            weights = [w / weight_sum for w in weights]
        else:
            weights = [1.0 / len(states)] * len(states)

        self.current_state = random.choices(states, weights=weights)[0]

        # Update tracking
        self.state_history.append({
            'state': self.current_state,
            'timestamp': time.time()
        })
        self.state_counts[self.current_state] += 1

        # Update protocol state
        self._update_protocol_state()

        return self.current_state

    def _apply_protocol_logic(self, transitions: Dict[str, float]) -> Dict[str, float]:
        """
        Apply protocol-specific logic to modify transition probabilities.

        Args:
            transitions: Base transition probabilities

        Returns:
            Modified transition probabilities
        """
        modified = dict(transitions)

        # Connection age effects
        connection_age = time.time() - self.connection_start

        if self.protocol == 'tls':
            # TLS renegotiation
            if self.current_state == 'data' and connection_age > 300:  # 5 minutes
                modified['handshake'] = 0.1  # Renegotiation probability

        elif self.protocol == 'quic':
            # QUIC connection migration
            if self.current_state == 'application' and random.random() < 0.001:
                modified['handshake'] = 0.05

        elif self.protocol == 'webrtc':
            # ICE restart
            if self.current_state == 'srtp' and connection_age > 600:  # 10 minutes
                modified['stun'] = 0.02

        elif self.protocol == 'ssh':
            # SSH rekeying
            if self.current_state == 'session' and self.data_transferred > 1024 * 1024 * 1024:  # 1GB
                modified['handshake'] = 0.1

        return modified

    def _update_protocol_state(self):
        """Update protocol-specific state variables."""
        if self.current_state in ['handshake', 'auth', 'dtls']:
            self.handshake_complete = False
        elif self.current_state in ['data', 'application', 'session', 'srtp']:
            self.handshake_complete = True

        # Estimate data transfer
        if self.current_state in ['data', 'application', 'session', 'srtp', 'stream']:
            char = self.get_state_characteristics()
            avg_size = sum(char['size_range']) / 2
            self.data_transferred += avg_size

    def get_state_characteristics(self) -> Dict[str, Any]:
        """
        Get characteristics for the current state.

        Returns:
            Dictionary of state characteristics
        """
        return self.state_characteristics[self.current_state].copy()

    def generate_packet_params(self) -> Tuple[int, float]:
        """
        Generate packet parameters based on current state.

        Returns:
            Tuple of (packet_size, interval)
        """
        char = self.get_state_characteristics()

        # Generate size
        min_size, max_size = char['size_range']
        if char.get('burst', False):
            # Burst mode - bias towards larger packets
            size = int(random.triangular(min_size, max_size, max_size))
        else:
            # Normal distribution
            size = random.randint(min_size, max_size)

        # Generate interval
        base_interval = char['interval']

        # Apply pattern-specific timing
        pattern = char.get('pattern', 'default')

        if pattern == 'request_response':
            # Alternating fast/slow
            if self.state_counts[self.current_state] % 2 == 0:
                interval = base_interval * 0.1
            else:
                interval = base_interval * 2.0

        elif pattern == 'stream':
            # Steady stream with occasional variations
            interval = base_interval * random.uniform(0.8, 1.2)

        elif pattern == 'rtp_stream':
            # Very regular timing
            interval = base_interval * random.uniform(0.98, 1.02)

        elif pattern == 'periodic':
            # Fixed intervals
            interval = base_interval

        elif pattern == 'interactive':
            # Human-like delays
            interval = base_interval * random.lognormvariate(0, 0.5)
            interval = max(0.001, min(5.0, interval))

        else:
            # Default with jitter
            interval = base_interval * random.uniform(0.5, 1.5)

        # Apply RTT effects
        if char.get('bidirectional', False):
            interval = max(interval, self.rtt_estimate * 0.5)

        return size, interval

    def should_terminate(self) -> bool:
        """
        Determine if the connection should terminate.

        Returns:
            True if connection should terminate
        """
        # Check if in closed state
        if self.current_state == 'closed':
            return random.random() < 0.5

        # Age-based termination
        connection_age = time.time() - self.connection_start

        # Protocol-specific lifetimes
        max_lifetimes = {
            'tls': 3600,      # 1 hour
            'quic': 7200,     # 2 hours
            'webrtc': 10800,  # 3 hours
            'ssh': 14400,     # 4 hours
            'http2': 1800,    # 30 minutes
            'http3': 1800,    # 30 minutes
            'generic': 3600   # 1 hour
        }

        max_lifetime = max_lifetimes.get(self.protocol, 3600)

        if connection_age > max_lifetime:
            return random.random() < 0.1  # 10% chance per check

        # Data-based termination
        if self.data_transferred > 10 * 1024 * 1024 * 1024:  # 10GB
            return random.random() < 0.05

        return False

    def get_session_phase(self) -> str:
        """
        Get the current session phase.

        Returns:
            Phase name (startup, active, closing, closed)
        """
        handshake_states = ['init', 'handshake', 'auth', 'stun', 'turn', 'dtls',
                           'initial', 'connection', 'settings', 'quic_handshake']
        active_states = ['data', 'application', 'session', 'srtp', 'stream',
                        'active', 'burst', 'channel']
        closing_states = ['closing']
        closed_states = ['closed']

        if self.current_state in handshake_states:
            return 'startup'
        elif self.current_state in active_states:
            return 'active'
        elif self.current_state in closing_states:
            return 'closing'
        elif self.current_state in closed_states:
            return 'closed'
        else:
            return 'unknown'

    def simulate_packet_loss(self, loss_rate: float = 0.001) -> bool:
        """
        Simulate packet loss for the current state.

        Args:
            loss_rate: Base packet loss rate

        Returns:
            True if packet should be dropped
        """
        # Adjust loss rate based on state
        if self.current_state in ['handshake', 'auth', 'initial']:
            # Lower loss during critical phases
            adjusted_rate = loss_rate * 0.5
        elif self.current_state in ['closing', 'closed']:
            # Higher loss during closing
            adjusted_rate = loss_rate * 2.0
        else:
            adjusted_rate = loss_rate

        return random.random() < adjusted_rate

    def get_statistics(self) -> Dict[str, Any]:
        """
        Get state machine statistics.

        Returns:
            Dictionary of statistics
        """
        stats = {
            'protocol': self.protocol,
            'current_state': self.current_state,
            'session_id': self.session_id,
            'handshake_complete': self.handshake_complete,
            'data_transferred': self.data_transferred,
            'data_transferred_mb': self.data_transferred / (1024 * 1024),
            'connection_age': time.time() - self.connection_start,
            'time_in_current_state': time.time() - self.last_state_change,
            'session_phase': self.get_session_phase(),
            'state_counts': dict(self.state_counts),
            'total_state_changes': sum(self.state_counts.values()),
            'rtt_estimate': self.rtt_estimate,
            'congestion_window': self.congestion_window
        }

        # Add state distribution
        total_changes = stats['total_state_changes']
        if total_changes > 0:
            stats['state_distribution'] = {
                state: count / total_changes
                for state, count in self.state_counts.items()
            }

        # Add recent history
        if self.state_history:
            recent = list(self.state_history)[-10:]
            stats['recent_states'] = [h['state'] for h in recent]

        return stats

    def reset(self):
        """Reset state machine to initial state."""
        self.current_state = 'init'
        self.state_history.clear()
        self.state_timers.clear()
        self.state_counts.clear()
        self.handshake_complete = False
        self.data_transferred = 0
        self.connection_start = time.time()
        self.last_state_change = time.time()
        self.session_id = random.randint(0, 2**32 - 1)
