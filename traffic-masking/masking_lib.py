#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# Copyright © 2025 kogeler
# SPDX-License-Identifier: Apache-2.0

"""
Enhanced masking library for server and client with advanced obfuscation features

Provides:
- Protocol mimicry profiles (web, video, voip, file-transfer, gaming, mixed)
- Dynamic obfuscation (padding, timing jitter, fragmentation, pseudo-headers)
- Statistical analysis (entropy estimation, periodicity hints)
- Enhanced features when available (adaptive timing, correlation breaking, ML resistance)
- Common utilities for server/client
"""

from __future__ import annotations

import os
import struct
import random
import socket
import hashlib
import time
import math
from enum import Enum
from dataclasses import dataclass
from collections import deque
from typing import Iterator, List, Optional, Sequence, Tuple, Dict, Any, Union, Callable

import time  # Add time import for rate control

try:
    import numpy as np
except Exception:
    np = None  # Minimal fallback

# Try to import enhanced modules
try:
    from enhanced.timing import AdaptiveTimingModel
    from enhanced.correlation import CorrelationBreaker
    from enhanced.ml_resistance import MLResistantGenerator
    from enhanced.entropy import EntropyEnhancer
    from enhanced.state_machine import ProtocolStateMachine
    ENHANCED_AVAILABLE = True
except ImportError:
    ENHANCED_AVAILABLE = False
    # Fallback implementations will be provided

__all__ = [
    "TrafficProfile",
    "PatternStep",
    "ProtocolMimicry",
    "DynamicObfuscator",
    "StatisticalAnalyzer",
    "stream_generator",
    "ObfuscationConfig",
    "parse_profile",
    "build_obfuscator",
    "init_udp_socket",
    "send_fragments",
]


class TrafficProfile(Enum):
    WEB_BROWSING = "web"
    VIDEO_STREAMING = "video"
    VOIP_CALL = "voip"
    FILE_TRANSFER = "file"
    GAMING = "gaming"
    MIXED = "mixed"


@dataclass(frozen=True)
class PatternStep:
    size: int          # bytes (payload before obfuscation)
    delay: float       # seconds (inter-packet delay target)


class ProtocolMimicry:
    """Generate sequences of PatternStep for different protocol-like behaviors."""

    def __init__(self):
        """Initialize with enhanced features if available."""
        self.enhanced = ENHANCED_AVAILABLE
        if self.enhanced:
            try:
                self.correlation_breaker = CorrelationBreaker()
                self.ml_resistant = MLResistantGenerator()
                self.timing_model = AdaptiveTimingModel()
            except:
                self.enhanced = False

    @staticmethod
    def web_browsing_session() -> List[PatternStep]:
        steps: List[PatternStep] = []
        # Initial page HTML/CSS/JS fetch bursts
        for _ in range(random.randint(6, 14)):
            steps.append(PatternStep(size=random.randint(300, 1800), delay=random.uniform(0.005, 0.03)))
        # Assets (images, fonts)
        for _ in range(random.randint(8, 22)):
            steps.append(PatternStep(size=random.randint(800, 4000), delay=random.uniform(0.01, 0.06)))
        # Reading pause
        steps.append(PatternStep(size=0, delay=random.uniform(1.2, 6.0)))
        # Background AJAX/pings
        for _ in range(random.randint(4, 10)):
            steps.append(PatternStep(size=random.randint(80, 400), delay=random.uniform(0.3, 1.5)))
        return steps

    @staticmethod
    def video_streaming_session(quality: Optional[str] = None) -> List[PatternStep]:
        bitrates_kbps = {"360p": 1000, "480p": 2500, "720p": 5000, "1080p": 8000}
        if quality not in bitrates_kbps:
            quality = random.choice(list(bitrates_kbps.keys()))
        bps = bitrates_kbps[quality] * 1024 // 8  # bytes/sec
        steps: List[PatternStep] = []
        # Startup buffering (~1s)
        for _ in range(100):
            size = int(bps / 100 * random.uniform(0.9, 1.2))
            steps.append(PatternStep(size=max(200, size), delay=0.01))
        # Steady state (~10s)
        for _ in range(1000):
            size = int(bps / 100 * random.uniform(0.95, 1.05))
            steps.append(PatternStep(size=max(100, size), delay=0.01))
        # Occasional keyframe-like bursts
        for _ in range(random.randint(5, 15)):
            steps.append(PatternStep(size=int(bps * random.uniform(0.05, 0.15)), delay=0.02))
        return steps

    @staticmethod
    def voip_call(codec: Optional[str] = None) -> List[PatternStep]:
        codecs = {"g711": {"size": 160, "interval": 0.02},
                  "g729": {"size": 20, "interval": 0.02},
                  "opus": {"size": random.randint(40, 120), "interval": 0.02}}
        if codec not in codecs:
            codec = random.choice(list(codecs.keys()))
        c = codecs[codec]
        steps: List[PatternStep] = []
        for _ in range(3000):
            steps.append(PatternStep(size=max(10, int(c["size"] * random.uniform(0.9, 1.1))),
                                     delay=c["interval"] * random.uniform(0.98, 1.02)))
            if random.random() < 0.005:
                steps.append(PatternStep(size=random.randint(60, 120), delay=0.0))
        return steps

    @staticmethod
    def file_transfer_session(target_mbps: float = 10.0) -> List[PatternStep]:
        bps = max(0.5, target_mbps) * 1024 * 1024 / 8  # bytes/sec
        mtu_pay = random.randint(1100, 1400)
        interval = mtu_pay / bps
        steps: List[PatternStep] = []
        for _ in range(2000):
            steps.append(PatternStep(size=int(mtu_pay * random.uniform(0.92, 1.0)),
                                     delay=max(0.0005, interval * random.uniform(0.9, 1.1))))
        for _ in range(random.randint(5, 15)):
            steps.append(PatternStep(size=0, delay=random.uniform(0.01, 0.2)))
        return steps

    @staticmethod
    def gaming_session() -> List[PatternStep]:
        steps: List[PatternStep] = []
        for _ in range(4000):
            steps.append(PatternStep(size=random.randint(40, 220), delay=random.uniform(0.01, 0.05)))
            if random.random() < 0.02:
                steps.append(PatternStep(size=random.randint(400, 1200), delay=0.001))
        return steps

    @staticmethod
    def mixed_session() -> List[PatternStep]:
        choices = [ProtocolMimicry.web_browsing_session,
                   ProtocolMimicry.video_streaming_session,
                   ProtocolMimicry.voip_call,
                   ProtocolMimicry.file_transfer_session,
                   ProtocolMimicry.gaming_session]
        steps: List[PatternStep] = []
        for _ in range(random.randint(3, 6)):
            steps.extend(random.choice(choices)())
        return steps

    @staticmethod
    def for_profile(profile: TrafficProfile) -> List[PatternStep]:
        return {
            TrafficProfile.WEB_BROWSING: ProtocolMimicry.web_browsing_session,
            TrafficProfile.VIDEO_STREAMING: ProtocolMimicry.video_streaming_session,
            TrafficProfile.VOIP_CALL: ProtocolMimicry.voip_call,
            TrafficProfile.FILE_TRANSFER: ProtocolMimicry.file_transfer_session,
            TrafficProfile.GAMING: ProtocolMimicry.gaming_session,
            TrafficProfile.MIXED: ProtocolMimicry.mixed_session,
        }[profile]()


class DynamicObfuscator:
    """
    Obfuscates packets by:
    - Padding
    - Pseudo-headers (RTP/QUIC-like)
    - Timing jitter
    - MTU fragmentation
    """

    def __init__(
        self,
        padding_strategy: str = "random",  # random | fixed_buckets | progressive | none
        timing_jitter: float = 0.002,      # seconds stddev for jitter
        mtu: int = 1200,
        header_mode: str = "none",         # none | rtp | quic
        fixed_buckets: Optional[Sequence[int]] = None,
    ):
        self.padding_strategy = padding_strategy
        self.timing_jitter = max(0.0, float(timing_jitter))
        self.mtu = max(256, int(mtu))
        self.header_mode = header_mode
        self.fixed_buckets = tuple(fixed_buckets) if fixed_buckets else (128, 256, 512, 1024, 1280, 1400)
        # RTP-like state
        self._rtp_seq = random.randint(0, 65535)
        self._rtp_ssrc = random.getrandbits(32)
        self._rtp_ts_base = random.getrandbits(32)
        # QUIC-like PN
        self._quic_pn = random.randint(0, 2**32 - 1)

        # Enhanced features if available
        self.enhanced = ENHANCED_AVAILABLE
        if self.enhanced:
            try:
                self.timing_model = AdaptiveTimingModel()
                self.entropy_enhancer = EntropyEnhancer()
            except:
                self.enhanced = False

    def obfuscate(self, payload: bytes, profile: Optional[TrafficProfile] = None, base_delay: float = 0.0) -> Tuple[List[bytes], float]:
        pkt = self._apply_header(payload, profile)
        pkt = self._apply_padding(pkt, profile)
        fragments = self._fragment(pkt, self.mtu)

        # Use base delay with small jitter for consistent throughput
        if base_delay > 0:
            # Add small jitter (10% of base delay max)
            jitter = random.gauss(0.0, min(self.timing_jitter, base_delay * 0.1))
            delay = max(0.0001, base_delay + jitter)
        else:
            # Fallback to timing model if available
            if self.enhanced and hasattr(self, 'timing_model'):
                delay = self.timing_model.get_delay(len(payload), network_load=0.5)
                # Cap the delay to maintain throughput
                delay = min(delay, 0.01)
            else:
                jitter = random.gauss(0.0, self.timing_jitter)
                delay = max(0.0001, base_delay + jitter)

        return fragments, delay

    def _apply_padding(self, packet: bytes, profile: Optional[TrafficProfile]) -> bytes:
        if self.padding_strategy == "none":
            return packet
        if self.padding_strategy == "random":
            max_pad = max(16, min(120, int(len(packet) * 0.07)))
            pad_len = random.randint(0, max_pad)
            return packet + os.urandom(pad_len)
        if self.padding_strategy == "progressive":
            factor = random.uniform(0.0, 0.20)
            pad_len = int(len(packet) * factor)
            if pad_len <= 0:
                return packet
            return packet + os.urandom(pad_len)
        if self.padding_strategy == "fixed_buckets":
            target = None
            for b in self.fixed_buckets:
                if len(packet) <= b:
                    target = b
                    break
            if target is None:
                target = min(max(self.fixed_buckets), self.mtu)
            pad_len = max(0, target - len(packet))
            if pad_len == 0:
                return packet
            return packet + os.urandom(pad_len)
        return packet

    def _apply_header(self, payload: bytes, profile: Optional[TrafficProfile]) -> bytes:
        if self.header_mode == "none":
            return payload
        if self.header_mode == "rtp":
            return self._rtp_like(payload, profile)
        if self.header_mode == "quic":
            return self._quic_like(payload)
        return payload

    def _rtp_like(self, payload: bytes, profile: Optional[TrafficProfile]) -> bytes:
        # Very rough RTP-like header (12 bytes)
        version, padding, extension, csrc_count = 2, 0, 0, 0
        marker = 1 if random.random() < 0.02 else 0
        payload_type = {
            TrafficProfile.VOIP_CALL: 111,
            TrafficProfile.VIDEO_STREAMING: 96,
        }.get(profile, random.randint(96, 127))
        b0 = (version << 6) | (padding << 5) | (extension << 4) | (csrc_count & 0x0F)
        b1 = ((marker & 0x01) << 7) | (payload_type & 0x7F)
        self._rtp_seq = (self._rtp_seq + 1) & 0xFFFF
        ts_step = random.randint(800, 2000)
        self._rtp_ts_base = (self._rtp_ts_base + ts_step) & 0xFFFFFFFF
        header = struct.pack("!BBHII", b0, b1, self._rtp_seq, self._rtp_ts_base, self._rtp_ssrc)
        return header + payload

    def _quic_like(self, payload: bytes) -> bytes:
        flags = 0xC0 | (random.randint(0, 3) << 4)
        dcid_len = random.choice([8, 12, 16])
        scid_len = random.choice([0, 8, 12])
        dcid = os.urandom(dcid_len)
        scid = os.urandom(scid_len)
        pn_len = random.choice([1, 2, 3, 4])
        self._quic_pn = (self._quic_pn + 1) & 0xFFFFFFFF
        pn_mask = (1 << (pn_len * 8)) - 1
        pn_val = self._quic_pn & pn_mask
        pn_bytes = pn_val.to_bytes(pn_len, "big")
        header = bytes([flags, dcid_len]) + dcid + bytes([scid_len]) + scid + pn_bytes
        return header + payload

    @staticmethod
    def fragment(packet: bytes, mtu: int) -> List[bytes]:
        mtu = max(256, int(mtu))
        frags: List[bytes] = []
        for i in range(0, len(packet), mtu):
            frags.append(packet[i : i + mtu])
        return frags

    def _fragment(self, packet: bytes, mtu: int) -> List[bytes]:
        return self.fragment(packet, mtu)


class StatisticalAnalyzer:
    """Simple statistical checks: entropy and periodicity hints."""

    @staticmethod
    def entropy_bits_per_byte(data: bytes) -> float:
        if not data:
            return 0.0
        if np is None:
            # Crude fallback — return mid-high entropy value
            return 7.0
        arr = np.frombuffer(data, dtype=np.uint8)
        counts = np.bincount(arr, minlength=256)
        p = counts[counts > 0].astype(np.float64)
        p /= p.sum()
        entropy = float(-np.sum(p * np.log2(p)))
        return max(0.0, min(8.0, entropy))

    @staticmethod
    def entropy_normalized(data: bytes) -> float:
        return StatisticalAnalyzer.entropy_bits_per_byte(data) / 8.0

    @staticmethod
    def detect_periodicity(packet_sizes: Sequence[int], packet_times: Sequence[float]) -> Dict[str, Any]:
        if np is None or len(packet_sizes) < 5 or len(packet_times) < 5:
            return {"sizes_cv": None, "intervals_cv": None, "interval_peak_ms": None}
        sizes = np.array(packet_sizes, dtype=np.float64)
        intervals = np.diff(np.array(packet_times, dtype=np.float64))
        intervals = intervals[intervals > 0]
        result: Dict[str, Any] = {"sizes_cv": None, "intervals_cv": None, "interval_peak_ms": None}
        if sizes.size > 1:
            mean_s = float(np.mean(sizes))
            std_s = float(np.std(sizes))
            result["sizes_cv"] = None if mean_s == 0 else std_s / mean_s
        if intervals.size > 1:
            mean_i = float(np.mean(intervals))
            std_i = float(np.std(intervals))
            result["intervals_cv"] = None if mean_i == 0 else std_i / mean_i
            hist, edges = np.histogram(intervals, bins=20)
            peak_idx = int(np.argmax(hist))
            peak_center = (edges[peak_idx] + edges[peak_idx + 1]) / 2.0
            result["interval_peak_ms"] = peak_center * 1000.0
        return result


def _generate_payload(size: int, entropy: float = 1.0) -> bytes:
    size = max(0, int(size))
    if size == 0:
        return b""

    # For performance, use simple random generation by default
    # Enhanced entropy is expensive and should be used sparingly
    if ENHANCED_AVAILABLE and size > 1000 and random.random() < 0.1:  # Use enhanced only 10% of time for large packets
        try:
            enhancer = EntropyEnhancer()
            return enhancer.generate_realistic_encrypted_payload(size, content_type='mixed')
        except:
            pass

    # Fast path for high entropy (most common case)
    if entropy >= 0.95:
        return os.urandom(size)

    # Optimized generation for lower entropy
    if entropy < 0.5:
        # Low entropy - mostly repeated bytes
        base_byte = random.randint(0, 255)
        data = bytearray([base_byte] * size)
        # Add some variation
        for _ in range(int(size * entropy)):
            data[random.randint(0, size-1)] = random.randint(0, 255)
        return bytes(data)
    else:
        # Medium to high entropy - mix of random and patterns
        return os.urandom(size)


def stream_generator(
    profile: TrafficProfile,
    target_mbps: Optional[float] = None,
    min_mbps: Optional[float] = None,
    max_mbps: Optional[float] = None,
    obfuscator: Optional[DynamicObfuscator] = None,
    entropy: float = 1.0,
) -> Iterator[Tuple[List[bytes], float]]:
    """Yield (fragments, delay) continuously according to profile.

    Args:
        profile: Traffic pattern profile
        target_mbps: Target rate in Mbps (if min/max not specified)
        min_mbps: Minimum rate in Mbps for floating rate
        max_mbps: Maximum rate in Mbps for floating rate
        obfuscator: Optional obfuscator instance
        entropy: Payload entropy level 0.0-1.0
    """

    # Setup rate control
    if min_mbps is not None and max_mbps is not None:
        # Floating rate mode - start at a random position for variety
        current_mbps = random.uniform(min_mbps, max_mbps)
        rate_velocity = random.uniform(-0.5, 0.5) * (max_mbps - min_mbps)  # Initial velocity
        rate_acceleration = 0.0  # Acceleration
        last_rate_update = time.time()
        use_floating_rate = True
    elif target_mbps is not None:
        # Fixed target mode
        current_mbps = target_mbps
        use_floating_rate = False
    else:
        # No rate control
        current_mbps = None
        use_floating_rate = False

    # Rate tracking for proper limiting
    rate_window_bytes = 0
    rate_window_start = time.time()

    # Use enhanced features if available
    enhanced_generator = None
    if ENHANCED_AVAILABLE and target_mbps and target_mbps > 10:  # Only use enhanced for high rates
        try:
            ml_generator = MLResistantGenerator()
            timing_model = AdaptiveTimingModel(base_rtt=0.001)  # Lower base RTT for higher throughput
            enhanced_generator = True
        except:
            enhanced_generator = None

    steps = ProtocolMimicry.for_profile(profile)
    if not steps:
        steps = [PatternStep(size=1200, delay=0.001)]

    # Adjust packet sizes for better rate control
    # Use consistent packet sizes for more predictable rate control
    adjusted_steps = []
    for step in steps:
        # Use medium-sized packets for better control
        new_size = max(800, min(1400, step.size))
        # Base delay will be calculated dynamically based on current rate
        new_delay = 0.001  # Minimal base delay
        adjusted_steps.append(PatternStep(size=new_size, delay=new_delay))
    steps = adjusted_steps

    if obfuscator is None:
        obfuscator = DynamicObfuscator()

    idx = 0

    # For floating rate mode - pattern tracking
    pattern_change_interval = random.uniform(2.0, 8.0)  # Change pattern every 2-8 seconds
    last_pattern_change = time.time()

    # Floating rate pattern selection - include more extreme patterns
    rate_pattern_type = random.choice(['wave', 'random_walk', 'bursty', 'steady_drift', 'oscillating', 'extreme'])
    rate_pattern_phase = random.uniform(0, 2 * math.pi)  # Random starting phase
    dwell_at_boundary = False
    dwell_remaining = 0
    last_boundary_visited = 'none'  # Track last boundary

    while True:
        # Update floating rate if enabled
        if use_floating_rate and min_mbps is not None and max_mbps is not None:
            now = time.time()
            dt = now - last_rate_update

            if dt > 0.05:  # Update rate every 50ms for smoother transitions
                rate_range = max_mbps - min_mbps
                rate_center = (min_mbps + max_mbps) / 2

                # Check for pattern change
                if now - last_pattern_change > pattern_change_interval:
                    # Switch traffic pattern - ensure we use patterns that reach boundaries
                    rate_pattern_type = random.choice(['full_sine', 'boundary_jumps', 'sweep', 'aggressive_random'])
                    pattern_change_interval = random.uniform(8.0, 20.0)
                    last_pattern_change = now
                    rate_pattern_phase = 0.0

                    # Often start at a boundary to ensure we visit them
                    if random.random() < 0.6:  # 60% chance to start at boundary
                        if random.random() < 0.5:
                            current_mbps = max_mbps
                        else:
                            current_mbps = min_mbps
                        rate_velocity = 0
                        dwell_at_boundary = True
                        dwell_remaining = random.uniform(2.0, 5.0)

                # Handle dwelling at boundaries
                if dwell_at_boundary:
                    dwell_remaining -= dt
                    if dwell_remaining <= 0:
                        dwell_at_boundary = False
                        # Strong push away from boundary
                        if current_mbps <= min_mbps + 0.1:
                            rate_velocity = rate_range * random.uniform(1.0, 2.0)
                        else:
                            rate_velocity = -rate_range * random.uniform(1.0, 2.0)
                    else:
                        # Stay exactly at boundary
                        if current_mbps < rate_center:
                            current_mbps = min_mbps
                        else:
                            current_mbps = max_mbps
                        last_rate_update = now
                        continue

                # Apply aggressive patterns that ALWAYS use the full range
                if rate_pattern_type == 'full_sine':
                    # Sine wave that definitely spans full range
                    rate_pattern_phase += dt * 0.5  # Moderate speed
                    wave_value = math.sin(rate_pattern_phase)
                    # Direct mapping ensuring we hit exact min and max
                    # Map [-1, 1] to [min_mbps, max_mbps]
                    current_mbps = min_mbps + (max_mbps - min_mbps) * (wave_value + 1.0) / 2.0
                    # Force exact boundaries at extremes
                    if wave_value <= -0.99:
                        current_mbps = min_mbps
                    elif wave_value >= 0.99:
                        current_mbps = max_mbps

                elif rate_pattern_type == 'boundary_jumps':
                    # Frequently jump between boundaries and middle
                    if random.random() < 0.2:  # 20% chance per update - more frequent
                        choice = random.random()
                        if choice < 0.4:
                            current_mbps = min_mbps  # 40% chance for min
                        elif choice < 0.8:
                            current_mbps = max_mbps  # 40% chance for max
                        else:
                            # 20% chance for random position in range
                            current_mbps = min_mbps + rate_range * random.random()

                elif rate_pattern_type == 'sweep':
                    # Linear sweep from min to max and back
                    rate_pattern_phase += dt * 0.25  # Steady sweep speed
                    phase_mod = rate_pattern_phase % 2.0
                    if phase_mod < 1.0:
                        # Sweep up from min to max - ensure we hit both exactly
                        progress = phase_mod
                        if progress <= 0.02:
                            current_mbps = min_mbps  # Start exactly at min
                        elif progress >= 0.98:
                            current_mbps = max_mbps  # End exactly at max
                        else:
                            # Linear interpolation
                            current_mbps = min_mbps + rate_range * progress
                    else:
                        # Sweep down from max to min - ensure we hit both exactly
                        progress = phase_mod - 1.0
                        if progress <= 0.02:
                            current_mbps = max_mbps  # Start exactly at max
                        elif progress >= 0.98:
                            current_mbps = min_mbps  # End exactly at min
                        else:
                            # Linear interpolation
                            current_mbps = max_mbps - rate_range * progress

                elif rate_pattern_type == 'aggressive_random':
                    # Aggressive random walk that favors extremes
                    if random.random() < 0.2:  # 20% chance to jump
                        rand = random.random()
                        if rand < 0.3:
                            # Jump to min
                            current_mbps = min_mbps
                        elif rand < 0.6:
                            # Jump to max
                            current_mbps = max_mbps
                        else:
                            # Random position favoring extremes
                            if random.random() < 0.5:
                                # Near min
                                current_mbps = min_mbps + rate_range * random.uniform(0, 0.3)
                            else:
                                # Near max
                                current_mbps = max_mbps - rate_range * random.uniform(0, 0.3)
                    else:
                        # Small random walk
                        current_mbps += random.gauss(0, rate_range * 0.1)

                # Ensure we stay within bounds
                current_mbps = max(min_mbps, min(max_mbps, current_mbps))

                # Force more frequent exact boundary visits
                if random.random() < 0.1:  # 10% chance for guaranteed boundary hit
                    if random.random() < 0.5:
                        current_mbps = min_mbps
                    else:
                        current_mbps = max_mbps

                last_rate_update = now

        # For floating rate mode, ensure current_mbps is always within bounds
        if use_floating_rate and min_mbps is not None and max_mbps is not None:
            current_mbps = max(min_mbps, min(max_mbps, current_mbps))

        step = steps[idx]
        idx = (idx + 1) % len(steps)

        # Generate packet
        if step.size <= 0:
            fragments, _ = obfuscator.obfuscate(b"", profile=profile, base_delay=step.delay)
        else:
            payload = _generate_payload(step.size, entropy=entropy)
            fragments, _ = obfuscator.obfuscate(payload, profile=profile, base_delay=step.delay)

        # Calculate packet size
        packet_bytes = sum(len(f) for f in fragments)

        # Update rate window tracking
        now = time.time()
        window_elapsed = now - rate_window_start

        # Reset window every second to prevent drift
        if window_elapsed > 1.0:
            rate_window_bytes = 0
            rate_window_start = now
            window_elapsed = 0

        # Calculate delay to achieve target rate while strictly enforcing boundaries
        if use_floating_rate and min_mbps is not None and max_mbps is not None:
            # Ensure current_mbps is strictly within bounds
            rate_limit_mbps = max(min_mbps, min(max_mbps, current_mbps))
        elif target_mbps:
            rate_limit_mbps = target_mbps
        else:
            rate_limit_mbps = None

        if rate_limit_mbps and rate_limit_mbps > 0:
            # Target bytes per second for desired rate
            target_bytes_per_second = rate_limit_mbps * 1024 * 1024 / 8

            # Simple and direct delay calculation for better rate achievement
            if target_bytes_per_second > 0 and packet_bytes > 0:
                # Calculate ideal delay for this packet size at target rate
                delay = packet_bytes / target_bytes_per_second

                # For higher rates, reduce delay to allow bursting
                if rate_limit_mbps >= 5:
                    delay = delay * 0.9  # Allow 10% burst for high rates
                elif rate_limit_mbps >= 2:
                    delay = delay * 0.95  # Allow 5% burst for medium rates

                # Minimal bounds to prevent issues
                delay = max(0.00001, min(0.1, delay))
            else:
                delay = 0.001
        else:
            delay = step.delay if hasattr(step, 'delay') else 0.001

        # Update window counter
        rate_window_bytes += packet_bytes

        yield fragments, delay


# Common utilities for server/client

@dataclass
class ObfuscationConfig:
    padding_strategy: str = "random"
    header_mode: str = "none"
    mtu: int = 1200
    entropy: float = 1.0
    timing_jitter: float = 0.002


def parse_profile(profile: Union[str, TrafficProfile, None]) -> TrafficProfile:
    if isinstance(profile, TrafficProfile):
        return profile
    if isinstance(profile, str):
        try:
            return TrafficProfile(profile)
        except Exception:
            return TrafficProfile.MIXED
    return TrafficProfile.MIXED


def build_obfuscator(cfg: ObfuscationConfig) -> DynamicObfuscator:
    return DynamicObfuscator(
        padding_strategy=cfg.padding_strategy,
        timing_jitter=cfg.timing_jitter,
        mtu=cfg.mtu,
        header_mode=cfg.header_mode,
    )


def init_udp_socket(sock: socket.socket, sndbuf: int = 4 * 1024 * 1024, rcvbuf: int = 4 * 1024 * 1024) -> socket.socket:
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, int(sndbuf))
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, int(rcvbuf))
    return sock


def send_fragments(
    sock: socket.socket,
    addrs: Union[Tuple[str, int], List[Tuple[str, int]]],
    fragments: List[bytes],
    on_sent: Optional[Callable[[int], None]] = None,
) -> None:
    """
    Send fragments to one or many addresses. on_sent(len_bytes) is called per-fragment per-destination.
    """
    if isinstance(addrs, tuple):
        addrs_list = [addrs]
    else:
        addrs_list = list(addrs)
    for frag in fragments:
        for addr in addrs_list:
            sock.sendto(frag, addr)
            if on_sent:
                on_sent(len(frag))
