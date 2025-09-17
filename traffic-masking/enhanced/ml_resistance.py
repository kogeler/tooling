#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# Copyright © 2025 kogeler
# SPDX-License-Identifier: Apache-2.0

"""
Machine Learning resistant traffic generation module
"""

import random
import time
import math
from typing import Dict, List, Tuple, Any, Optional
from enum import Enum
from collections import deque


class MLResistantGenerator:
    """
    Generator that produces traffic patterns resistant to ML-based detection.
    Uses adversarial techniques and real traffic mimicry to evade classification.
    """

    def __init__(self):
        """Initialize ML-resistant generator with traffic patterns and models."""
        self.real_traffic_patterns = self._load_real_traffic_patterns()
        self.pattern_cache = {}
        self.anomaly_rate = 0.05  # 5% anomalous packets like real traffic

        # Adversarial parameters
        self.noise_factor = 0.15  # Amount of noise to add
        self.pattern_switching_rate = 0.1  # Rate of pattern switching

        # Feature obfuscation
        self.feature_history = deque(maxlen=100)
        self.current_pattern = 'mixed'
        self.pattern_counter = 0

        # Statistics for adaptive behavior
        self.packet_count = 0
        self.pattern_usage = {}

    def _load_real_traffic_patterns(self) -> Dict[str, Dict[str, Any]]:
        """
        Load patterns that mimic real traffic characteristics.
        These patterns are based on empirical observations of real traffic.
        """
        return {
            'web': {
                'sizes': [64, 128, 256, 512, 576, 1024, 1400],
                'size_weights': [0.1, 0.1, 0.15, 0.2, 0.15, 0.15, 0.15],
                'intervals': [0.001, 0.005, 0.01, 0.02, 0.05, 0.1],
                'interval_weights': [0.2, 0.25, 0.2, 0.15, 0.15, 0.05],
                'burst_probability': 0.3,
                'idle_probability': 0.2,
                'session_length': (50, 500),
                'features': {
                    'avg_packet_size': 680,
                    'size_variance': 400,
                    'timing_regularity': 0.3
                }
            },
            'video': {
                'sizes': [1200, 1300, 1350, 1380, 1400],
                'size_weights': [0.15, 0.2, 0.3, 0.2, 0.15],
                'intervals': [0.008, 0.016, 0.033, 0.040],
                'interval_weights': [0.25, 0.35, 0.25, 0.15],
                'burst_probability': 0.1,
                'idle_probability': 0.05,
                'session_length': (200, 2000),
                'features': {
                    'avg_packet_size': 1340,
                    'size_variance': 80,
                    'timing_regularity': 0.8
                }
            },
            'voip': {
                'sizes': [20, 40, 60, 80, 160],
                'size_weights': [0.1, 0.2, 0.3, 0.3, 0.1],
                'intervals': [0.020, 0.020, 0.020],
                'interval_weights': [0.95, 0.025, 0.025],
                'burst_probability': 0.02,
                'idle_probability': 0.01,
                'session_length': (500, 5000),
                'features': {
                    'avg_packet_size': 80,
                    'size_variance': 40,
                    'timing_regularity': 0.95
                }
            },
            'gaming': {
                'sizes': [40, 80, 120, 200, 400, 800],
                'size_weights': [0.25, 0.3, 0.2, 0.15, 0.08, 0.02],
                'intervals': [0.016, 0.033, 0.050],
                'interval_weights': [0.5, 0.35, 0.15],
                'burst_probability': 0.4,
                'idle_probability': 0.1,
                'session_length': (100, 1000),
                'features': {
                    'avg_packet_size': 150,
                    'size_variance': 120,
                    'timing_regularity': 0.6
                }
            },
            'file': {
                'sizes': [1400, 1400, 1400, 1400],
                'size_weights': [0.9, 0.05, 0.03, 0.02],
                'intervals': [0.001, 0.002, 0.003],
                'interval_weights': [0.6, 0.3, 0.1],
                'burst_probability': 0.8,
                'idle_probability': 0.3,
                'session_length': (50, 1000),
                'features': {
                    'avg_packet_size': 1380,
                    'size_variance': 50,
                    'timing_regularity': 0.4
                }
            },
            'mixed': {
                'sizes': [64, 128, 256, 512, 1024, 1400],
                'size_weights': [0.15, 0.15, 0.2, 0.2, 0.15, 0.15],
                'intervals': [0.001, 0.01, 0.02, 0.05, 0.1],
                'interval_weights': [0.2, 0.25, 0.25, 0.2, 0.1],
                'burst_probability': 0.25,
                'idle_probability': 0.15,
                'session_length': (100, 1000),
                'features': {
                    'avg_packet_size': 600,
                    'size_variance': 500,
                    'timing_regularity': 0.5
                }
            }
        }

    def generate_adversarial_packet(self, profile: str, timing_model: Any = None) -> Tuple[int, float]:
        """
        Generate packet that evades ML detection using adversarial techniques.

        Args:
            profile: Traffic profile name
            timing_model: Optional timing model for realistic delays

        Returns:
            Tuple of (packet_size, interval)
        """
        self.packet_count += 1

        # Get base pattern
        pattern = self.real_traffic_patterns.get(
            profile,
            self.real_traffic_patterns['mixed']
        )

        # Decide if this should be an anomaly
        if random.random() < self.anomaly_rate:
            size, interval = self._generate_anomaly(pattern)
        else:
            # Normal packet with adversarial perturbations
            size, interval = self._generate_adversarial_normal(pattern)
            # Ensure normal packets don't look like anomalies
            size = max(65, min(1399, size))  # Avoid tiny/jumbo
            interval = min(0.99, interval)  # Avoid idle periods

        # Apply feature obfuscation
        size, interval = self._obfuscate_features(size, interval, pattern)

        # Apply timing model if provided
        if timing_model:
            network_load = self._estimate_network_load()
            interval = timing_model.get_delay(size, network_load)

        # Update statistics
        self._update_feature_history(size, interval)

        return size, interval

    def _generate_anomaly(self, pattern: Dict[str, Any]) -> Tuple[int, float]:
        """
        Generate anomalous packet that exists in real traffic.
        These anomalies help confuse ML classifiers.
        """
        # Weight anomaly types to reduce extreme cases
        anomaly_types = ['tiny', 'jumbo', 'burst', 'idle', 'fragment', 'duplicate']
        anomaly_weights = [0.1, 0.1, 0.3, 0.1, 0.2, 0.2]  # Reduce tiny/jumbo/idle
        anomaly_type = random.choices(anomaly_types, weights=anomaly_weights)[0]

        if anomaly_type == 'tiny':
            # Tiny control packets
            size = random.randint(1, 64)
            interval = random.choice([0.001, 0.01, 0.1])
        elif anomaly_type == 'jumbo':
            # Maximum size packets
            size = 1400
            interval = random.uniform(0.001, 0.005)
        elif anomaly_type == 'burst':
            # Burst traffic
            size = random.choice(pattern['sizes'])
            interval = random.uniform(0.0001, 0.001)
        elif anomaly_type == 'idle':
            # Long idle period
            size = random.randint(64, 128)
            interval = random.uniform(1.0, 5.0)
        elif anomaly_type == 'fragment':
            # Fragmented packet
            size = random.randint(500, 700)
            interval = 0.0001
        else:  # duplicate
            # Duplicate/retransmission
            if self.feature_history:
                last = self.feature_history[-1]
                size = last.get('size', 512)
                interval = 0.2  # Retransmission timeout
            else:
                size = random.choice(pattern['sizes'])
                interval = 0.2

        return size, interval

    def _generate_adversarial_normal(self, pattern: Dict[str, Any]) -> Tuple[int, float]:
        """
        Generate normal packet with adversarial perturbations.
        """
        # Select base values using weighted random
        size = random.choices(
            pattern['sizes'],
            weights=pattern['size_weights']
        )[0]

        base_interval = random.choices(
            pattern['intervals'],
            weights=pattern['interval_weights']
        )[0]

        # Apply adversarial perturbations
        size = self._apply_adversarial_noise(size, 'size')
        interval = self._apply_adversarial_noise(base_interval, 'interval')

        # Apply burst or idle based on profile (but less extreme)
        if random.random() < pattern['burst_probability'] * 0.5:  # Reduce burst frequency
            interval *= random.uniform(0.1, 0.3)  # Less extreme burst
        elif random.random() < pattern['idle_probability'] * 0.3:  # Reduce idle frequency
            interval *= random.uniform(2, 5)  # Less extreme idle
        else:
            interval *= random.uniform(0.8, 1.2)  # Normal variation

        return size, interval

    def _apply_adversarial_noise(self, value: float, value_type: str) -> float:
        """
        Apply adversarial noise to confuse ML classifiers.
        """
        if value_type == 'size':
            # Add Gaussian noise
            noise = random.gauss(0, value * self.noise_factor)
            value = int(value + noise)

            # Occasionally shift to boundary values (adversarial examples)
            if random.random() < 0.05:
                boundaries = [64, 128, 256, 512, 1024, 1400]
                closest = min(boundaries, key=lambda x: abs(x - value))
                # Shift slightly off boundary to confuse classifiers
                value = closest + random.randint(-10, 10)

            value = max(1, min(1400, value))

        elif value_type == 'interval':
            # Log-normal noise for intervals
            log_value = math.log(max(0.0001, value))
            noise = random.gauss(0, self.noise_factor)
            value = math.exp(log_value + noise)

            # Occasionally use exact protocol timings (confusing)
            if random.random() < 0.05:
                protocol_timings = [0.001, 0.008, 0.016, 0.020, 0.033, 0.040]
                value = random.choice(protocol_timings) * random.uniform(0.99, 1.01)

        return value

    def _obfuscate_features(self, size: int, interval: float, pattern: Dict[str, Any]) -> Tuple[int, float]:
        """
        Obfuscate statistical features that ML models use.
        """
        if not self.feature_history:
            return size, interval

        # Calculate current features
        recent_sizes = [f['size'] for f in list(self.feature_history)[-20:]]
        if recent_sizes:
            current_avg = sum(recent_sizes) / len(recent_sizes)
            target_avg = pattern['features']['avg_packet_size']

            # Adjust size to move average towards target
            if current_avg > target_avg * 1.2:
                # We're too high, bias towards smaller packets
                size = int(size * random.uniform(0.6, 0.9))
            elif current_avg < target_avg * 0.8:
                # We're too low, bias towards larger packets
                size = int(size * random.uniform(1.1, 1.4))

            # Add controlled variance
            target_variance = pattern['features']['size_variance']
            current_variance = math.sqrt(
                sum((s - current_avg)**2 for s in recent_sizes) / len(recent_sizes)
            ) if len(recent_sizes) > 1 else target_variance

            if current_variance < target_variance * 0.8:
                # Add more variance
                size = int(size + random.gauss(0, target_variance * 0.5))

        # Ensure bounds
        size = max(1, min(1400, size))
        interval = max(0.0001, interval)

        return size, interval

    def add_protocol_artifacts(self, packets: List[Dict[str, Any]], protocol: str) -> List[Dict[str, Any]]:
        """
        Add protocol-specific artifacts to traffic.

        Args:
            packets: List of packet dictionaries
            protocol: Protocol name

        Returns:
            Enhanced packet list with artifacts
        """
        enhanced = []

        for i, pkt in enumerate(packets):
            enhanced.append(pkt)

            # TCP-like artifacts
            if protocol in ['web', 'file']:
                # SYN/ACK patterns at session start
                if i < 3:
                    enhanced.append({
                        'size': random.randint(40, 60),
                        'time': pkt['time'] + 0.001,
                        'type': 'handshake'
                    })

                # Retransmissions
                if random.random() < 0.001:
                    enhanced.append({
                        'size': pkt['size'],
                        'time': pkt['time'] + random.uniform(0.2, 1.0),
                        'type': 'retransmission'
                    })

                # ACK packets
                if random.random() < 0.1:
                    enhanced.append({
                        'size': random.randint(40, 60),
                        'time': pkt['time'] + 0.001,
                        'type': 'ack'
                    })

            # QUIC-like artifacts
            if protocol in ['web', 'video']:
                # QUIC ACK frames
                if random.random() < 0.15:
                    enhanced.append({
                        'size': random.randint(20, 80),
                        'time': pkt['time'] + 0.001,
                        'type': 'quic_ack'
                    })

                # Connection migration
                if random.random() < 0.001:
                    enhanced.append({
                        'size': random.randint(100, 200),
                        'time': pkt['time'] + 0.01,
                        'type': 'migration'
                    })

            # RTP/RTCP for media
            if protocol in ['video', 'voip']:
                # RTCP reports
                if i > 0 and i % 100 == 0:
                    enhanced.append({
                        'size': random.randint(70, 90),
                        'time': pkt['time'] + random.uniform(0.01, 0.1),
                        'type': 'rtcp'
                    })

                # FEC packets
                if random.random() < 0.05:
                    enhanced.append({
                        'size': pkt.get('size', 100) // 2,
                        'time': pkt['time'] + 0.001,
                        'type': 'fec'
                    })

            # Gaming-specific
            if protocol == 'gaming':
                # State updates
                if random.random() < 0.2:
                    enhanced.append({
                        'size': random.randint(100, 300),
                        'time': pkt['time'] + 0.001,
                        'type': 'state_update'
                    })

                # Ping/keepalive
                if i > 0 and i % 30 == 0:
                    enhanced.append({
                        'size': random.randint(20, 40),
                        'time': pkt['time'] + 0.001,
                        'type': 'ping'
                    })

        return enhanced

    def generate_session(self, profile: str, duration: float) -> List[Dict[str, Any]]:
        """
        Generate a complete session with realistic patterns.

        Args:
            profile: Traffic profile
            duration: Session duration in seconds

        Returns:
            List of packets with timing
        """
        pattern = self.real_traffic_patterns.get(profile, self.real_traffic_patterns['mixed'])
        packets = []
        current_time = 0.0

        # Session phases
        phases = ['start', 'active', 'idle', 'active', 'end']
        phase_durations = self._calculate_phase_durations(duration, len(phases))

        for phase, phase_duration in zip(phases, phase_durations):
            phase_packets = self._generate_phase_traffic(
                phase, profile, pattern, phase_duration, current_time
            )
            packets.extend(phase_packets)
            if phase_packets:
                current_time = phase_packets[-1]['time']

        # Add protocol artifacts
        packets = self.add_protocol_artifacts(packets, profile)

        # Sort by time
        packets.sort(key=lambda x: x['time'])

        return packets

    def _calculate_phase_durations(self, total_duration: float, num_phases: int) -> List[float]:
        """Calculate durations for each phase of a session."""
        # Distribute duration with some randomness
        base_duration = total_duration / num_phases
        durations = []

        remaining = total_duration
        for i in range(num_phases - 1):
            duration = base_duration * random.uniform(0.5, 1.5)
            duration = min(duration, remaining * 0.8)
            durations.append(duration)
            remaining -= duration

        durations.append(remaining)
        return durations

    def _generate_phase_traffic(self, phase: str, profile: str, pattern: Dict[str, Any],
                                duration: float, start_time: float) -> List[Dict[str, Any]]:
        """Generate traffic for a specific phase."""
        packets = []
        current_time = start_time
        end_time = start_time + duration

        while current_time < end_time:
            size, interval = self.generate_adversarial_packet(profile)

            # Adjust for phase
            if phase == 'start':
                # Handshake and initial burst
                interval *= 0.5
                if len(packets) < 10:
                    size = random.randint(40, 200)
            elif phase == 'idle':
                # Sparse keepalive traffic
                interval *= random.uniform(10, 50)
                size = random.randint(40, 100)
            elif phase == 'end':
                # Closing sequence
                interval *= random.uniform(1, 3)
                if len(packets) > 5:
                    break

            packets.append({
                'size': size,
                'time': current_time,
                'phase': phase
            })

            current_time += interval

        return packets

    def _estimate_network_load(self) -> float:
        """Estimate current network load from recent history."""
        if not self.feature_history:
            return 0.5

        recent = list(self.feature_history)[-50:]
        if len(recent) < 2:
            return 0.5

        # Calculate packet rate
        time_span = recent[-1].get('time', 1) - recent[0].get('time', 0)
        if time_span <= 0:
            return 0.5

        packet_rate = len(recent) / time_span

        # Estimate load (normalize to 0-1)
        # Assume 1000 pps = full load
        load = min(1.0, packet_rate / 1000)

        # Add some randomness
        load += random.gauss(0, 0.1)

        return max(0.0, min(1.0, load))

    def _update_feature_history(self, size: int, interval: float):
        """Update feature history for statistics."""
        self.feature_history.append({
            'size': size,
            'interval': interval,
            'time': time.time()
        })

        # Update pattern usage
        if self.current_pattern not in self.pattern_usage:
            self.pattern_usage[self.current_pattern] = 0
        self.pattern_usage[self.current_pattern] += 1

    def get_statistics(self) -> Dict[str, Any]:
        """Get generator statistics."""
        stats = {
            'packet_count': self.packet_count,
            'current_pattern': self.current_pattern,
            'anomaly_rate': self.anomaly_rate,
            'noise_factor': self.noise_factor,
            'pattern_usage': dict(self.pattern_usage)
        }

        if self.feature_history:
            recent = list(self.feature_history)[-100:]
            sizes = [f['size'] for f in recent]
            intervals = [f['interval'] for f in recent if 'interval' in f]

            if sizes:
                stats['avg_size'] = sum(sizes) / len(sizes)
                stats['size_variance'] = math.sqrt(
                    sum((s - stats['avg_size'])**2 for s in sizes) / len(sizes)
                ) if len(sizes) > 1 else 0

            if intervals:
                stats['avg_interval'] = sum(intervals) / len(intervals)
                stats['interval_variance'] = math.sqrt(
                    sum((i - stats['avg_interval'])**2 for i in intervals) / len(intervals)
                ) if len(intervals) > 1 else 0

        return stats

    def reset(self):
        """Reset generator state."""
        self.feature_history.clear()
        self.pattern_cache.clear()
        self.packet_count = 0
        self.pattern_usage.clear()
        self.current_pattern = 'mixed'
        self.pattern_counter = 0
