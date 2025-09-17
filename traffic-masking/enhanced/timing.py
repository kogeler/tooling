#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# Copyright © 2025 kogeler
# SPDX-License-Identifier: Apache-2.0

"""
Adaptive timing model for realistic network delay simulation
"""

import random
import time
from collections import deque
from typing import Optional, Dict, Any


class AdaptiveTimingModel:
    """
    Adaptive timing model based on realistic network behavior.
    Simulates network conditions including congestion, jitter, packet loss, and retransmission.
    """

    def __init__(self, base_rtt: float = 0.02, jitter_factor: float = 0.3):
        """
        Initialize timing model.

        Args:
            base_rtt: Base round-trip time in seconds
            jitter_factor: Factor for jitter calculation (0.0-1.0)
        """
        self.base_rtt = base_rtt
        self.jitter_factor = min(1.0, max(0.0, jitter_factor))
        self.history = deque(maxlen=100)
        self.congestion_level = 0.0
        self.packet_loss_rate = 0.001  # 0.1% baseline

        # Network state tracking
        self.bandwidth_estimate = 10 * 1024 * 1024  # 10 Mbps default
        self.queue_depth = 0.0
        self.max_queue_depth = 50  # packets

        # RTT tracking
        self.min_rtt = base_rtt
        self.max_rtt = base_rtt * 10
        self.smooth_rtt = base_rtt
        self.rtt_variance = base_rtt * 0.1

        # Congestion control state
        self.cwnd = 10  # Congestion window
        self.ssthresh = 65535  # Slow start threshold
        self.in_slow_start = True

        # Statistics
        self.total_packets = 0
        self.lost_packets = 0
        self.retransmitted_packets = 0

    def get_delay(self, packet_size: int, network_load: float = 0.5) -> float:
        """
        Calculate realistic delay considering packet size and network load.

        Args:
            packet_size: Size of the packet in bytes
            network_load: Current network load (0.0-1.0)

        Returns:
            Delay in seconds
        """
        self.total_packets += 1
        network_load = min(1.0, max(0.0, network_load))

        # Calculate transmission delay based on packet size
        transmission_delay = packet_size / self.bandwidth_estimate

        # Simulate network congestion with realistic dynamics
        self._update_congestion(network_load)

        # Calculate propagation delay with congestion
        propagation_delay = self.base_rtt * (1.0 + self.congestion_level * 3.0)

        # Calculate queueing delay using M/M/1 queue model
        queue_delay = self._calculate_queue_delay(network_load)

        # Calculate jitter with temporal correlation
        jitter = self._calculate_correlated_jitter()

        # Simulate packet loss and retransmission
        if self._should_drop_packet(network_load):
            self.lost_packets += 1
            self.retransmitted_packets += 1
            # Retransmission timeout (RTO) calculation
            rto = self._calculate_rto()
            return rto

        # Total delay calculation
        total_delay = transmission_delay + propagation_delay + queue_delay + jitter

        # Update RTT estimates
        self._update_rtt_estimate(total_delay)

        return max(0.0001, total_delay)

    def _update_congestion(self, network_load: float):
        """Update congestion level with realistic network dynamics."""
        # Random congestion events
        if random.random() < 0.05:  # 5% chance of congestion spike
            congestion_spike = random.uniform(0.1, 0.3) * network_load
            self.congestion_level = min(1.0, self.congestion_level + congestion_spike)
        else:
            # Gradual congestion recovery
            recovery_rate = 0.01 * (1.0 - network_load)
            self.congestion_level = max(0.0, self.congestion_level - recovery_rate)

        # Update congestion window (TCP-like behavior)
        if self.congestion_level > 0.5:
            # Congestion detected, reduce window
            self.cwnd = max(1, self.cwnd // 2)
            self.ssthresh = self.cwnd
            self.in_slow_start = False
        elif self.in_slow_start:
            # Slow start phase
            self.cwnd = min(self.cwnd + 1, self.ssthresh)
            if self.cwnd >= self.ssthresh:
                self.in_slow_start = False
        else:
            # Congestion avoidance
            self.cwnd += 1.0 / self.cwnd

    def _calculate_queue_delay(self, network_load: float) -> float:
        """
        Calculate queuing delay using M/M/1 queue model.

        Args:
            network_load: Current network utilization (0.0-1.0)

        Returns:
            Queue delay in seconds
        """
        # Update queue depth based on load
        arrival_rate = network_load * 100  # packets per second
        service_rate = 100  # packets per second capacity

        if arrival_rate < service_rate:
            # M/M/1 queue average delay
            utilization = arrival_rate / service_rate
            avg_queue_size = utilization / (1.0 - utilization)
            self.queue_depth = min(self.max_queue_depth, avg_queue_size)
        else:
            # Queue overflow scenario
            self.queue_depth = self.max_queue_depth

        # Queue delay based on Little's Law
        queue_delay = (self.queue_depth / service_rate) * random.uniform(0.5, 1.5)

        return queue_delay

    def _calculate_correlated_jitter(self) -> float:
        """
        Calculate jitter with temporal correlation for realistic behavior.

        Returns:
            Jitter value in seconds
        """
        if self.history:
            # Use exponentially weighted moving average for correlation
            prev_jitter = self.history[-1]
            correlation_factor = 0.7  # 70% correlation with previous value

            # New jitter component
            new_jitter = random.gauss(0, self.base_rtt * self.jitter_factor)

            # Combine with correlation
            jitter = prev_jitter * correlation_factor + new_jitter * (1.0 - correlation_factor)

            # Add occasional jitter spikes (network events)
            if random.random() < 0.02:  # 2% chance of spike
                spike = random.uniform(2, 5) * self.base_rtt * self.jitter_factor
                jitter += spike * random.choice([-1, 1])
        else:
            jitter = random.gauss(0, self.base_rtt * self.jitter_factor)

        self.history.append(jitter)
        return jitter

    def _should_drop_packet(self, network_load: float) -> bool:
        """
        Determine if packet should be dropped based on network conditions.

        Args:
            network_load: Current network load (0.0-1.0)

        Returns:
            True if packet should be dropped
        """
        # Calculate dynamic loss rate based on load and congestion
        base_loss = self.packet_loss_rate

        # Increase loss rate with congestion
        congestion_loss = self.congestion_level * 0.05  # Up to 5% additional loss

        # Load-dependent loss (queue overflow)
        if network_load > 0.9:
            load_loss = (network_load - 0.9) * 0.1  # Up to 1% additional
        else:
            load_loss = 0.0

        # Burst loss simulation
        burst_loss = 0.0
        if random.random() < 0.001:  # 0.1% chance of burst loss event
            burst_loss = 0.1  # 10% loss during burst

        total_loss_rate = min(0.2, base_loss + congestion_loss + load_loss + burst_loss)

        return random.random() < total_loss_rate

    def _calculate_rto(self) -> float:
        """
        Calculate retransmission timeout using TCP-like algorithm.

        Returns:
            RTO in seconds
        """
        # Jacobson's algorithm for RTO calculation
        rto = self.smooth_rtt + 4 * self.rtt_variance

        # Apply backoff for multiple retransmissions
        backoff_factor = min(64, 2 ** (self.retransmitted_packets % 6))
        rto *= backoff_factor

        # Bound RTO
        min_rto = self.base_rtt * 2
        max_rto = 60.0  # 60 seconds max

        return min(max_rto, max(min_rto, rto))

    def _update_rtt_estimate(self, measured_rtt: float):
        """
        Update RTT estimates using exponentially weighted moving average.

        Args:
            measured_rtt: Measured round-trip time
        """
        alpha = 0.125  # TCP standard
        beta = 0.25    # TCP standard

        # Update smooth RTT
        self.smooth_rtt = (1 - alpha) * self.smooth_rtt + alpha * measured_rtt

        # Update RTT variance
        deviation = abs(measured_rtt - self.smooth_rtt)
        self.rtt_variance = (1 - beta) * self.rtt_variance + beta * deviation

        # Track min/max
        self.min_rtt = min(self.min_rtt, measured_rtt)
        self.max_rtt = max(self.max_rtt, measured_rtt)

    def update_network_conditions(self, rtt_sample: Optional[float] = None,
                                 bandwidth_sample: Optional[float] = None,
                                 loss_rate_sample: Optional[float] = None):
        """
        Update model based on observed network conditions.

        Args:
            rtt_sample: Observed RTT in seconds
            bandwidth_sample: Observed bandwidth in bytes/second
            loss_rate_sample: Observed packet loss rate (0.0-1.0)
        """
        if rtt_sample is not None and rtt_sample > 0:
            # Exponential moving average update
            alpha = 0.2
            self.base_rtt = self.base_rtt * (1 - alpha) + rtt_sample * alpha
            self._update_rtt_estimate(rtt_sample)

        if bandwidth_sample is not None and bandwidth_sample > 0:
            # Update bandwidth estimate
            alpha = 0.1
            self.bandwidth_estimate = self.bandwidth_estimate * (1 - alpha) + bandwidth_sample * alpha

        if loss_rate_sample is not None:
            # Update loss rate
            alpha = 0.1
            self.packet_loss_rate = self.packet_loss_rate * (1 - alpha) + loss_rate_sample * alpha

    def get_statistics(self) -> Dict[str, Any]:
        """
        Get current timing model statistics.

        Returns:
            Dictionary with statistics
        """
        return {
            'base_rtt': self.base_rtt,
            'smooth_rtt': self.smooth_rtt,
            'min_rtt': self.min_rtt,
            'max_rtt': self.max_rtt,
            'rtt_variance': self.rtt_variance,
            'congestion_level': self.congestion_level,
            'packet_loss_rate': self.packet_loss_rate,
            'total_packets': self.total_packets,
            'lost_packets': self.lost_packets,
            'retransmitted_packets': self.retransmitted_packets,
            'loss_percentage': (self.lost_packets / max(1, self.total_packets)) * 100,
            'bandwidth_estimate_mbps': self.bandwidth_estimate * 8 / (1024 * 1024),
            'cwnd': self.cwnd,
            'queue_depth': self.queue_depth
        }

    def reset_statistics(self):
        """Reset packet statistics while keeping network state."""
        self.total_packets = 0
        self.lost_packets = 0
        self.retransmitted_packets = 0
