#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# Copyright © 2025 kogeler
# SPDX-License-Identifier: Apache-2.0

"""
Correlation breaker for disrupting statistical patterns in traffic
"""

import random
import math
from collections import deque
from typing import Dict, List, Optional, Tuple, Any
from enum import Enum


class TrafficProfile(Enum):
    """Traffic profile enumeration"""
    WEB_BROWSING = "web"
    VIDEO_STREAMING = "video"
    VOIP_CALL = "voip"
    FILE_TRANSFER = "file"
    GAMING = "gaming"
    MIXED = "mixed"


class CorrelationBreaker:
    """
    Breaks statistical correlations in traffic using Markov chains and
    autocorrelation techniques to generate realistic packet size patterns.
    """

    def __init__(self):
        """Initialize correlation breaker with Markov models."""
        self.markov_chains = self._build_all_markov_models()
        self.current_state = 'medium'
        self.last_size = 512
        self.size_history = deque(maxlen=20)
        self.interval_history = deque(maxlen=20)

        # Autocorrelation parameters
        self.autocorr_coefficient = 0.3  # 30% correlation with history
        self.burst_mode = False
        self.burst_remaining = 0

        # Profile-specific state
        self.current_profile = TrafficProfile.MIXED
        self.profile_switch_counter = 0

        # Statistical tracking
        self.size_distribution = {'small': 0, 'medium': 0, 'large': 0}
        self.total_packets = 0

    def _build_all_markov_models(self) -> Dict[str, Dict[str, Dict[str, float]]]:
        """Build Markov models for different traffic profiles."""
        return {
            'default': {
                'small': {'small': 0.35, 'medium': 0.45, 'large': 0.20},
                'medium': {'small': 0.25, 'medium': 0.50, 'large': 0.25},
                'large': {'small': 0.20, 'medium': 0.45, 'large': 0.35}
            },
            'web': {
                'small': {'small': 0.40, 'medium': 0.40, 'large': 0.20},
                'medium': {'small': 0.30, 'medium': 0.40, 'large': 0.30},
                'large': {'small': 0.35, 'medium': 0.35, 'large': 0.30}
            },
            'video': {
                'small': {'small': 0.10, 'medium': 0.20, 'large': 0.70},
                'medium': {'small': 0.10, 'medium': 0.30, 'large': 0.60},
                'large': {'small': 0.05, 'medium': 0.15, 'large': 0.80}
            },
            'voip': {
                'small': {'small': 0.80, 'medium': 0.15, 'large': 0.05},
                'medium': {'small': 0.70, 'medium': 0.25, 'large': 0.05},
                'large': {'small': 0.60, 'medium': 0.30, 'large': 0.10}
            },
            'file': {
                'small': {'small': 0.10, 'medium': 0.10, 'large': 0.80},
                'medium': {'small': 0.10, 'medium': 0.20, 'large': 0.70},
                'large': {'small': 0.05, 'medium': 0.10, 'large': 0.85}
            },
            'gaming': {
                'small': {'small': 0.60, 'medium': 0.30, 'large': 0.10},
                'medium': {'small': 0.45, 'medium': 0.40, 'large': 0.15},
                'large': {'small': 0.40, 'medium': 0.40, 'large': 0.20}
            }
        }

    def _get_markov_chain(self, profile: Optional[TrafficProfile] = None) -> Dict[str, Dict[str, float]]:
        """Get appropriate Markov chain for the profile."""
        if profile is None:
            profile = self.current_profile

        profile_map = {
            TrafficProfile.WEB_BROWSING: 'web',
            TrafficProfile.VIDEO_STREAMING: 'video',
            TrafficProfile.VOIP_CALL: 'voip',
            TrafficProfile.FILE_TRANSFER: 'file',
            TrafficProfile.GAMING: 'gaming',
            TrafficProfile.MIXED: 'default'
        }

        chain_name = profile_map.get(profile, 'default')
        return self.markov_chains.get(chain_name, self.markov_chains['default'])

    def get_correlated_size(self, base_size: int, profile: Optional[TrafficProfile] = None) -> int:
        """
        Generate correlated packet size using Markov chains and autocorrelation.

        Args:
            base_size: Base packet size suggestion
            profile: Traffic profile to use

        Returns:
            Correlated packet size
        """
        self.total_packets += 1

        if profile:
            self.current_profile = profile

        # Determine current state based on base size
        state = self._classify_size_state(base_size, profile)

        # Get Markov chain for current profile
        markov_chain = self._get_markov_chain(profile)

        # Markov chain transition
        transitions = markov_chain.get(state, markov_chain['medium'])
        next_state = random.choices(
            list(transitions.keys()),
            weights=list(transitions.values())
        )[0]

        # Get size range for the state
        size_range = self._get_size_range(next_state, profile)

        # Generate new size with autocorrelation
        new_size = self._apply_autocorrelation(size_range, base_size)

        # Apply burst mode if active
        if self.burst_mode:
            new_size = self._apply_burst_mode(new_size)

        # Check for burst mode activation
        if not self.burst_mode and random.random() < self._get_burst_probability(profile):
            self._activate_burst_mode(profile)

        # Update history
        self.size_history.append(new_size)
        self.last_size = new_size
        self.current_state = next_state

        # Update statistics
        self.size_distribution[next_state] = self.size_distribution.get(next_state, 0) + 1

        return new_size

    def _classify_size_state(self, size: int, profile: Optional[TrafficProfile]) -> str:
        """Classify size into state category."""
        if profile == TrafficProfile.VOIP_CALL:
            if size < 80:
                return 'small'
            elif size < 160:
                return 'medium'
            else:
                return 'large'
        elif profile == TrafficProfile.VIDEO_STREAMING:
            if size < 1000:
                return 'small'
            elif size < 1300:
                return 'medium'
            else:
                return 'large'
        elif profile == TrafficProfile.GAMING:
            if size < 100:
                return 'small'
            elif size < 300:
                return 'medium'
            else:
                return 'large'
        else:  # Default classification
            if size < 400:
                return 'small'
            elif size < 1000:
                return 'medium'
            else:
                return 'large'

    def _get_size_range(self, state: str, profile: Optional[TrafficProfile]) -> Tuple[int, int]:
        """Get size range for a given state and profile."""
        ranges = {
            TrafficProfile.VOIP_CALL: {
                'small': (20, 80),
                'medium': (80, 160),
                'large': (160, 320)
            },
            TrafficProfile.VIDEO_STREAMING: {
                'small': (800, 1000),
                'medium': (1000, 1300),
                'large': (1300, 1400)
            },
            TrafficProfile.GAMING: {
                'small': (40, 100),
                'medium': (100, 300),
                'large': (300, 600)
            },
            TrafficProfile.WEB_BROWSING: {
                'small': (64, 400),
                'medium': (400, 1000),
                'large': (1000, 1400)
            },
            TrafficProfile.FILE_TRANSFER: {
                'small': (500, 800),
                'medium': (800, 1200),
                'large': (1200, 1400)
            },
            TrafficProfile.MIXED: {
                'small': (64, 400),
                'medium': (400, 1000),
                'large': (1000, 1400)
            }
        }

        profile_ranges = ranges.get(profile if profile else TrafficProfile.MIXED, ranges[TrafficProfile.MIXED])
        return profile_ranges.get(state, (64, 1400))

    def _apply_autocorrelation(self, size_range: Tuple[int, int], base_size: int) -> int:
        """Apply autocorrelation with historical data."""
        min_size, max_size = size_range

        # Generate uncorrelated size
        new_size = random.randint(min_size, max_size)

        # Apply autocorrelation if we have history
        if self.size_history:
            # Calculate weighted average of history
            history_weights = [0.5 ** i for i in range(len(self.size_history))]
            history_weights.reverse()

            weighted_sum = sum(w * s for w, s in zip(history_weights, self.size_history))
            weight_total = sum(history_weights)

            if weight_total > 0:
                avg_history = weighted_sum / weight_total
            else:
                avg_history = new_size

            # Combine with autocorrelation coefficient
            correlated = int(avg_history * self.autocorr_coefficient + new_size * (1 - self.autocorr_coefficient))

            # Add small random perturbation
            perturbation = random.randint(-20, 20)
            correlated += perturbation

            # Ensure within bounds
            new_size = max(min_size, min(max_size, correlated))

        return new_size

    def _get_burst_probability(self, profile: Optional[TrafficProfile]) -> float:
        """Get burst probability for a given profile."""
        burst_probs = {
            TrafficProfile.WEB_BROWSING: 0.15,
            TrafficProfile.VIDEO_STREAMING: 0.05,
            TrafficProfile.VOIP_CALL: 0.02,
            TrafficProfile.FILE_TRANSFER: 0.20,
            TrafficProfile.GAMING: 0.10,
            TrafficProfile.MIXED: 0.08
        }
        return burst_probs.get(profile if profile else TrafficProfile.MIXED, 0.08)

    def _activate_burst_mode(self, profile: Optional[TrafficProfile]):
        """Activate burst mode."""
        self.burst_mode = True

        # Determine burst length based on profile
        burst_lengths = {
            TrafficProfile.WEB_BROWSING: (5, 20),
            TrafficProfile.VIDEO_STREAMING: (10, 30),
            TrafficProfile.VOIP_CALL: (2, 5),
            TrafficProfile.FILE_TRANSFER: (20, 100),
            TrafficProfile.GAMING: (3, 10),
            TrafficProfile.MIXED: (5, 25)
        }

        min_burst, max_burst = burst_lengths.get(profile if profile else TrafficProfile.MIXED, (5, 20))
        self.burst_remaining = random.randint(min_burst, max_burst)

    def _apply_burst_mode(self, size: int) -> int:
        """Apply burst mode modifications."""
        if self.burst_remaining > 0:
            # Increase size during burst
            burst_factor = random.uniform(1.2, 1.8)
            size = int(size * burst_factor)
            size = min(1400, size)  # Cap at MTU

            self.burst_remaining -= 1

            if self.burst_remaining <= 0:
                self.burst_mode = False

        return size

    def get_correlated_interval(self, base_interval: float, profile: Optional[TrafficProfile] = None) -> float:
        """
        Generate correlated inter-packet interval.

        Args:
            base_interval: Base interval suggestion in seconds
            profile: Traffic profile to use

        Returns:
            Correlated interval in seconds
        """
        # Profile-specific interval patterns
        if profile == TrafficProfile.VOIP_CALL:
            # VoIP has very regular intervals
            interval = base_interval * random.uniform(0.98, 1.02)
        elif profile == TrafficProfile.VIDEO_STREAMING:
            # Video has frame-based intervals
            frame_intervals = [0.008, 0.016, 0.033, 0.040]  # Common frame rates
            closest = min(frame_intervals, key=lambda x: abs(x - base_interval))
            interval = closest * random.uniform(0.95, 1.05)
        elif profile == TrafficProfile.GAMING:
            # Gaming has tick-based intervals
            tick_rates = [0.016, 0.033, 0.050]  # 60Hz, 30Hz, 20Hz
            closest = min(tick_rates, key=lambda x: abs(x - base_interval))
            interval = closest * random.uniform(0.90, 1.10)
        else:
            # General correlation with history
            if self.interval_history:
                avg_history = sum(self.interval_history) / len(self.interval_history)
                interval = avg_history * 0.4 + base_interval * 0.6
                interval *= random.uniform(0.85, 1.15)
            else:
                interval = base_interval * random.uniform(0.8, 1.2)

        # Add occasional outliers (realistic network behavior)
        if random.random() < 0.02:  # 2% outliers
            if random.random() < 0.5:
                interval *= random.uniform(0.1, 0.5)  # Very short
            else:
                interval *= random.uniform(2.0, 5.0)  # Very long

        self.interval_history.append(interval)
        return max(0.0001, interval)

    def add_cross_correlation(self, sizes: List[int], intervals: List[float]) -> Tuple[List[int], List[float]]:
        """
        Add cross-correlation between packet sizes and intervals.

        Args:
            sizes: List of packet sizes
            intervals: List of inter-packet intervals

        Returns:
            Tuple of correlated sizes and intervals
        """
        if len(sizes) != len(intervals):
            return sizes, intervals

        correlated_sizes = []
        correlated_intervals = []

        for i, (size, interval) in enumerate(zip(sizes, intervals)):
            # Large packets often have longer intervals (processing time)
            if size > 1200:
                interval *= random.uniform(1.1, 1.3)
            elif size < 100:
                interval *= random.uniform(0.8, 0.95)

            # Consecutive small packets might be fragments
            if i > 0 and sizes[i-1] < 100 and size < 100:
                interval *= 0.5  # Fragments arrive quickly

            correlated_sizes.append(size)
            correlated_intervals.append(interval)

        return correlated_sizes, correlated_intervals

    def get_statistics(self) -> Dict[str, Any]:
        """Get correlation statistics."""
        stats = {
            'current_state': self.current_state,
            'burst_mode': self.burst_mode,
            'total_packets': self.total_packets,
            'autocorr_coefficient': self.autocorr_coefficient
        }

        # Add size distribution percentages
        if self.total_packets > 0:
            for state in ['small', 'medium', 'large']:
                count = self.size_distribution.get(state, 0)
                stats[f'{state}_percentage'] = (count / self.total_packets) * 100

        # Add history statistics
        if self.size_history:
            stats['avg_recent_size'] = sum(self.size_history) / len(self.size_history)
            stats['stddev_recent_size'] = math.sqrt(
                sum((x - stats['avg_recent_size'])**2 for x in self.size_history) / len(self.size_history)
            )

        if self.interval_history:
            stats['avg_recent_interval'] = sum(self.interval_history) / len(self.interval_history)

        return stats

    def reset(self):
        """Reset correlation breaker state."""
        self.current_state = 'medium'
        self.last_size = 512
        self.size_history.clear()
        self.interval_history.clear()
        self.burst_mode = False
        self.burst_remaining = 0
        self.size_distribution = {'small': 0, 'medium': 0, 'large': 0}
        self.total_packets = 0
