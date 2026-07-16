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
import threading
import time
import math
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple, Union


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
    from enhanced.state_machine import ProtocolStateMachine  # noqa: F401  (availability probe)
    ENHANCED_AVAILABLE = True
except ImportError:
    ENHANCED_AVAILABLE = False
    # Fallback implementations will be provided

__all__ = [
    "TrafficProfile",
    "PatternStep",
    "ShapeEvent",
    "Packetizer",
    "FloatingRate",
    "RatioBudget",
    "RateLimiter",
    "RateReservation",
    "ProtocolMimicry",
    "DynamicObfuscator",
    "StatisticalAnalyzer",
    "stream_generator",
    "profile_event_generator",
    "ObfuscationConfig",
    "parse_profile",
    "build_obfuscator",
    "init_udp_socket",
    "send_fragments",
    "mbps_to_bytes_per_second",
    "generate_payload",
]

# Bit-rate unit is decimal megabits/s (10^6 bit/s), converted to byte budgets.
_BITS_PER_MEGABIT = 1_000_000


def mbps_to_bytes_per_second(mbps: float) -> float:
    """Convert a decimal-Mbps rate to application bytes per second."""
    return float(mbps) * _BITS_PER_MEGABIT / 8


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


@dataclass(frozen=True)
class ShapeEvent:
    """One logical offered-load event before obfuscation and packetization."""

    byte_count: int
    delay: float = 0.0

    def __post_init__(self):
        if isinstance(self.byte_count, bool) or not isinstance(self.byte_count, int):
            raise ValueError("event byte_count must be a non-negative integer")
        if self.byte_count < 0:
            raise ValueError("event byte_count must be non-negative")
        if not math.isfinite(self.delay) or self.delay < 0:
            raise ValueError("event delay must be a non-negative finite number")


class Packetizer:
    """Split application bytes so final framed datagrams fit a fixed ceiling."""

    def __init__(self, datagram_ceiling, framing_overhead=0):
        if isinstance(datagram_ceiling, bool) or not isinstance(
            datagram_ceiling, int
        ):
            raise ValueError("datagram ceiling must be a positive integer")
        if isinstance(framing_overhead, bool) or not isinstance(
            framing_overhead, int
        ):
            raise ValueError("framing overhead must be a non-negative integer")
        if datagram_ceiling <= 0 or framing_overhead < 0:
            raise ValueError("invalid packetizer dimensions")
        if framing_overhead >= datagram_ceiling:
            raise ValueError("framing overhead leaves no payload capacity")
        self.datagram_ceiling = datagram_ceiling
        self.framing_overhead = framing_overhead
        self.payload_ceiling = datagram_ceiling - framing_overhead

    def packetize(self, payload):
        if not isinstance(payload, (bytes, bytearray, memoryview)):
            raise ValueError("packetizer payload must be bytes")
        payload = bytes(payload)
        return tuple(
            payload[offset : offset + self.payload_ceiling]
            for offset in range(0, len(payload), self.payload_ceiling)
        )


class FloatingRate:
    """Bounded, slope-limited rate process driven by a monotonic clock."""

    def __init__(
        self,
        minimum_mbps,
        maximum_mbps,
        clock=None,
        rng=None,
        max_slope_mbps_per_second=None,
        response_time=1.5,
    ):
        try:
            minimum_mbps = float(minimum_mbps)
            maximum_mbps = float(maximum_mbps)
            response_time = float(response_time)
        except (TypeError, ValueError):
            raise ValueError("floating rate bounds must be finite numbers") from None
        if (
            not math.isfinite(minimum_mbps)
            or not math.isfinite(maximum_mbps)
            or minimum_mbps <= 0
            or minimum_mbps >= maximum_mbps
        ):
            raise ValueError("floating rate bounds must be positive and ordered")
        if not math.isfinite(response_time) or response_time <= 0:
            raise ValueError("floating rate response time must be positive")

        span = maximum_mbps - minimum_mbps
        if max_slope_mbps_per_second is None:
            max_slope_mbps_per_second = span / 4
        try:
            max_slope_mbps_per_second = float(max_slope_mbps_per_second)
        except (TypeError, ValueError):
            raise ValueError("floating rate slope must be positive") from None
        if (
            not math.isfinite(max_slope_mbps_per_second)
            or max_slope_mbps_per_second <= 0
        ):
            raise ValueError("floating rate slope must be positive")

        self.minimum_mbps = minimum_mbps
        self.maximum_mbps = maximum_mbps
        self.max_slope_mbps_per_second = max_slope_mbps_per_second
        self.response_time = response_time
        self._span = span
        self._midpoint = (minimum_mbps + maximum_mbps) / 2
        self._clock = clock or time.monotonic
        self._rng = rng or random.Random()
        self._updated_at = self._clock()
        self.value_mbps = self._midpoint
        self.slope_mbps_per_second = self._rng.uniform(
            -self.max_slope_mbps_per_second,
            self.max_slope_mbps_per_second,
        )
        minimum_starting_slope = self.max_slope_mbps_per_second * 0.05
        if abs(self.slope_mbps_per_second) < minimum_starting_slope:
            self.slope_mbps_per_second = minimum_starting_slope

    def update(self):
        """Advance to the injected clock and return the current rate in Mbps."""
        now = self._clock()
        elapsed = max(0.0, now - self._updated_at)
        self._updated_at = now
        if elapsed == 0:
            return self.value_mbps

        center_slope = (
            (self._midpoint - self.value_mbps)
            / self._span
            * self.max_slope_mbps_per_second
        )
        noise_slope = self._rng.uniform(
            -self.max_slope_mbps_per_second,
            self.max_slope_mbps_per_second,
        )
        desired_slope = center_slope * 0.6 + noise_slope * 0.4
        blend = min(1.0, elapsed / self.response_time)
        slope = self.slope_mbps_per_second + (
            desired_slope - self.slope_mbps_per_second
        ) * blend
        slope = max(
            -self.max_slope_mbps_per_second,
            min(self.max_slope_mbps_per_second, slope),
        )
        candidate = self.value_mbps + slope * elapsed

        # Reflect overshoot into the range and reduce momentum at the edge. The
        # epsilon keeps samples away from an exact-boundary dwell.
        epsilon = self._span * 1e-9
        for _ in range(8):
            if candidate < self.minimum_mbps:
                candidate = self.minimum_mbps + (
                    self.minimum_mbps - candidate
                ) * 0.5
                slope = abs(slope) * 0.5
            elif candidate > self.maximum_mbps:
                candidate = self.maximum_mbps - (
                    candidate - self.maximum_mbps
                ) * 0.5
                slope = -abs(slope) * 0.5
            else:
                break
        self.value_mbps = min(
            self.maximum_mbps - epsilon,
            max(self.minimum_mbps + epsilon, candidate),
        )
        self.slope_mbps_per_second = slope
        return self.value_mbps


class RatioBudget:
    """Track successful uplink bytes against a fraction of downlink bytes."""

    def __init__(self, ratio):
        try:
            ratio = float(ratio)
        except (TypeError, ValueError):
            raise ValueError("ratio must be in [0.0, 1.0]") from None
        if not math.isfinite(ratio) or not 0.0 <= ratio <= 1.0:
            raise ValueError("ratio must be in [0.0, 1.0]")
        self.ratio = ratio
        self.downlink_bytes = 0
        self.uplink_bytes = 0
        self._lock = threading.Lock()

    @staticmethod
    def _validate_byte_count(byte_count):
        if (
            isinstance(byte_count, bool)
            or not isinstance(byte_count, int)
            or byte_count < 0
        ):
            raise ValueError("byte count must be a non-negative integer")
        return byte_count

    @property
    def available_bytes(self):
        with self._lock:
            return max(0.0, self.downlink_bytes * self.ratio - self.uplink_bytes)

    @property
    def observed_ratio(self):
        with self._lock:
            if self.downlink_bytes == 0:
                return 0.0
            return self.uplink_bytes / self.downlink_bytes

    def record_downlink(self, byte_count):
        byte_count = self._validate_byte_count(byte_count)
        with self._lock:
            self.downlink_bytes += byte_count

    def allows(self, byte_count, allow_debt=False):
        byte_count = self._validate_byte_count(byte_count)
        with self._lock:
            available = self.downlink_bytes * self.ratio - self.uplink_bytes
            return allow_debt or byte_count <= max(0.0, available)

    def record_uplink(self, byte_count):
        byte_count = self._validate_byte_count(byte_count)
        with self._lock:
            self.uplink_bytes += byte_count


@dataclass(frozen=True)
class RateReservation:
    byte_count: int
    delay: float
    token: int


class RateLimiter:
    """Bounded token bucket with explicit send reservation accounting."""

    def __init__(
        self,
        rate_bytes_per_second,
        burst_bytes,
        clock=None,
    ):
        self._clock = clock or time.monotonic
        self._rate = self._validate_rate(rate_bytes_per_second)
        if isinstance(burst_bytes, bool) or not isinstance(burst_bytes, int):
            raise ValueError("burst bytes must be a positive integer")
        if burst_bytes <= 0:
            raise ValueError("burst bytes must be a positive integer")
        self._capacity = burst_bytes
        self._tokens = float(burst_bytes)
        self._updated_at = self._clock()
        self._next_token = 0
        self._reservations = {}

    @staticmethod
    def _validate_rate(value):
        try:
            value = float(value)
        except (TypeError, ValueError):
            raise ValueError("rate must be a positive finite number") from None
        if not math.isfinite(value) or value <= 0:
            raise ValueError("rate must be a positive finite number")
        return value

    @property
    def rate_bytes_per_second(self):
        return self._rate

    @property
    def burst_bytes(self):
        return self._capacity

    def _accrue(self):
        now = self._clock()
        elapsed = max(0.0, now - self._updated_at)
        self._updated_at = now
        self._tokens = min(
            float(self._capacity), self._tokens + elapsed * self._rate
        )

    def set_rate(self, rate_bytes_per_second):
        self._accrue()
        self._rate = self._validate_rate(rate_bytes_per_second)

    def reset(self):
        self._tokens = float(self._capacity)
        self._updated_at = self._clock()
        self._reservations.clear()

    def reserve(self, byte_count):
        if isinstance(byte_count, bool) or not isinstance(byte_count, int):
            raise ValueError("reservation size must be a positive integer")
        if byte_count <= 0:
            raise ValueError("reservation size must be a positive integer")
        self._accrue()
        missing = max(0.0, byte_count - self._tokens)
        self._tokens -= byte_count
        self._next_token += 1
        reservation = RateReservation(
            byte_count=byte_count,
            delay=missing / self._rate,
            token=self._next_token,
        )
        self._reservations[reservation.token] = reservation.byte_count
        return reservation

    def commit(self, reservation, successful_bytes=None):
        reserved = self._reservations.get(reservation.token)
        if reserved != reservation.byte_count:
            raise ValueError("unknown or already completed reservation")
        if successful_bytes is None:
            successful_bytes = reserved
        if (
            isinstance(successful_bytes, bool)
            or not isinstance(successful_bytes, int)
            or not 0 <= successful_bytes <= reserved
        ):
            raise ValueError("successful bytes must be within the reservation")
        del self._reservations[reservation.token]
        self._accrue()
        refund = reserved - successful_bytes
        self._tokens = min(float(self._capacity), self._tokens + refund)
        return successful_bytes

    def refund(self, reservation):
        return self.commit(reservation, successful_bytes=0)


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
            except Exception:
                self.enhanced = False

    @staticmethod
    def web_browsing_session(rng=None) -> List[PatternStep]:
        rng = rng or random
        steps: List[PatternStep] = []
        # Initial page HTML/CSS/JS fetch bursts
        for _ in range(rng.randint(6, 14)):
            steps.append(PatternStep(size=rng.randint(300, 1800), delay=rng.uniform(0.005, 0.03)))
        # Assets (images, fonts)
        for _ in range(rng.randint(8, 22)):
            steps.append(PatternStep(size=rng.randint(800, 4000), delay=rng.uniform(0.01, 0.06)))
        # Reading pause
        steps.append(PatternStep(size=0, delay=rng.uniform(1.2, 6.0)))
        # Background AJAX/pings
        for _ in range(rng.randint(4, 10)):
            steps.append(PatternStep(size=rng.randint(80, 400), delay=rng.uniform(0.3, 1.5)))
        return steps

    @staticmethod
    def video_streaming_session(
        quality: Optional[str] = None, rng=None
    ) -> List[PatternStep]:
        rng = rng or random
        bitrates_kbps = {"360p": 1000, "480p": 2500, "720p": 5000, "1080p": 8000}
        if quality not in bitrates_kbps:
            quality = rng.choice(list(bitrates_kbps.keys()))
        bps = bitrates_kbps[quality] * 1024 // 8  # bytes/sec
        steps: List[PatternStep] = []
        # Startup buffering (~1s)
        for _ in range(100):
            size = int(bps / 100 * rng.uniform(0.9, 1.2))
            steps.append(PatternStep(size=max(200, size), delay=0.01))
        # Steady state (~10s)
        for _ in range(1000):
            size = int(bps / 100 * rng.uniform(0.95, 1.05))
            steps.append(PatternStep(size=max(100, size), delay=0.01))
        # Occasional keyframe-like bursts
        for _ in range(rng.randint(5, 15)):
            steps.append(PatternStep(size=int(bps * rng.uniform(0.05, 0.15)), delay=0.02))
        return steps

    @staticmethod
    def voip_call(codec: Optional[str] = None, rng=None) -> List[PatternStep]:
        rng = rng or random
        codecs = {"g711": {"size": 160, "interval": 0.02},
                  "g729": {"size": 20, "interval": 0.02},
                  "opus": {"size": rng.randint(40, 120), "interval": 0.02}}
        if codec not in codecs:
            codec = rng.choice(list(codecs.keys()))
        c = codecs[codec]
        steps: List[PatternStep] = []
        for _ in range(3000):
            steps.append(PatternStep(size=max(10, int(c["size"] * rng.uniform(0.9, 1.1))),
                                     delay=c["interval"] * rng.uniform(0.98, 1.02)))
            if rng.random() < 0.005:
                steps.append(PatternStep(size=rng.randint(60, 120), delay=0.0))
        return steps

    @staticmethod
    def file_transfer_session(
        target_mbps: float = 10.0, rng=None
    ) -> List[PatternStep]:
        rng = rng or random
        bps = mbps_to_bytes_per_second(max(0.5, target_mbps))
        mtu_pay = rng.randint(1100, 1400)
        interval = mtu_pay / bps
        steps: List[PatternStep] = []
        for _ in range(2000):
            steps.append(PatternStep(size=int(mtu_pay * rng.uniform(0.92, 1.0)),
                                     delay=max(0.0005, interval * rng.uniform(0.9, 1.1))))
        for _ in range(rng.randint(5, 15)):
            steps.append(PatternStep(size=0, delay=rng.uniform(0.01, 0.2)))
        return steps

    @staticmethod
    def gaming_session(rng=None) -> List[PatternStep]:
        rng = rng or random
        steps: List[PatternStep] = []
        for _ in range(4000):
            steps.append(PatternStep(size=rng.randint(40, 220), delay=rng.uniform(0.01, 0.05)))
            if rng.random() < 0.02:
                steps.append(PatternStep(size=rng.randint(400, 1200), delay=0.001))
        return steps

    @staticmethod
    def mixed_session(rng=None) -> List[PatternStep]:
        rng = rng or random
        choices = [ProtocolMimicry.web_browsing_session,
                   ProtocolMimicry.video_streaming_session,
                   ProtocolMimicry.voip_call,
                   ProtocolMimicry.file_transfer_session,
                   ProtocolMimicry.gaming_session]
        steps: List[PatternStep] = []
        for _ in range(rng.randint(3, 6)):
            steps.extend(rng.choice(choices)(rng=rng))
        return steps

    @staticmethod
    def for_profile(profile: TrafficProfile, rng=None) -> List[PatternStep]:
        return {
            TrafficProfile.WEB_BROWSING: ProtocolMimicry.web_browsing_session,
            TrafficProfile.VIDEO_STREAMING: ProtocolMimicry.video_streaming_session,
            TrafficProfile.VOIP_CALL: ProtocolMimicry.voip_call,
            TrafficProfile.FILE_TRANSFER: ProtocolMimicry.file_transfer_session,
            TrafficProfile.GAMING: ProtocolMimicry.gaming_session,
            TrafficProfile.MIXED: ProtocolMimicry.mixed_session,
        }[profile](rng=rng)


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
        rng=None,
        byte_source=None,
    ):
        self.padding_strategy = padding_strategy
        self.timing_jitter = max(0.0, float(timing_jitter))
        self.mtu = int(mtu)
        if self.mtu <= 0:
            raise ValueError("mtu must be positive")
        self.header_mode = header_mode
        self.fixed_buckets = tuple(fixed_buckets) if fixed_buckets else (128, 256, 512, 1024, 1280, 1400)
        self._rng = rng or random.Random()
        self._byte_source = byte_source or os.urandom
        # RTP-like state
        self._rtp_seq = self._rng.randint(0, 65535)
        self._rtp_ssrc = self._rng.getrandbits(32)
        self._rtp_ts_base = self._rng.getrandbits(32)
        # QUIC-like PN
        self._quic_pn = self._rng.randint(0, 2**32 - 1)

        # Enhanced features if available
        self.enhanced = ENHANCED_AVAILABLE
        if self.enhanced:
            try:
                self.timing_model = AdaptiveTimingModel()
                self.entropy_enhancer = EntropyEnhancer()
            except Exception:
                self.enhanced = False

    def obfuscate(self, payload: bytes, profile: Optional[TrafficProfile] = None, base_delay: float = 0.0) -> Tuple[List[bytes], float]:
        pkt = self.transform(payload, profile)
        fragments = self._fragment(pkt, self.mtu)

        # Compatibility API: preserve the caller's delay and only add bounded jitter.
        if base_delay > 0:
            jitter = self._rng.gauss(
                0.0, min(self.timing_jitter, base_delay * 0.1)
            )
            delay = max(0.0, base_delay + jitter)
        else:
            delay = 0.0

        return fragments, delay

    def transform(self, payload, profile=None):
        """Apply optional pseudo-header and padding without packetizing."""
        if not isinstance(payload, (bytes, bytearray, memoryview)):
            raise ValueError("payload must be bytes")
        packet = self._apply_header(bytes(payload), profile)
        return self._apply_padding(packet, profile)

    def _apply_padding(self, packet: bytes, profile: Optional[TrafficProfile]) -> bytes:
        if self.padding_strategy == "none":
            return packet
        if self.padding_strategy == "random":
            max_pad = max(16, min(120, int(len(packet) * 0.07)))
            pad_len = self._rng.randint(0, max_pad)
            return packet + self._byte_source(pad_len)
        if self.padding_strategy == "progressive":
            factor = self._rng.uniform(0.0, 0.20)
            pad_len = int(len(packet) * factor)
            if pad_len <= 0:
                return packet
            return packet + self._byte_source(pad_len)
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
            return packet + self._byte_source(pad_len)
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
        marker = 1 if self._rng.random() < 0.02 else 0
        payload_type = {
            TrafficProfile.VOIP_CALL: 111,
            TrafficProfile.VIDEO_STREAMING: 96,
        }.get(profile, self._rng.randint(96, 127))
        b0 = (version << 6) | (padding << 5) | (extension << 4) | (csrc_count & 0x0F)
        b1 = ((marker & 0x01) << 7) | (payload_type & 0x7F)
        self._rtp_seq = (self._rtp_seq + 1) & 0xFFFF
        ts_step = self._rng.randint(800, 2000)
        self._rtp_ts_base = (self._rtp_ts_base + ts_step) & 0xFFFFFFFF
        header = struct.pack("!BBHII", b0, b1, self._rtp_seq, self._rtp_ts_base, self._rtp_ssrc)
        return header + payload

    def _quic_like(self, payload: bytes) -> bytes:
        flags = 0xC0 | (self._rng.randint(0, 3) << 4)
        dcid_len = self._rng.choice([8, 12, 16])
        scid_len = self._rng.choice([0, 8, 12])
        dcid = self._byte_source(dcid_len)
        scid = self._byte_source(scid_len)
        pn_len = self._rng.choice([1, 2, 3, 4])
        self._quic_pn = (self._quic_pn + 1) & 0xFFFFFFFF
        pn_mask = (1 << (pn_len * 8)) - 1
        pn_val = self._quic_pn & pn_mask
        pn_bytes = pn_val.to_bytes(pn_len, "big")
        header = bytes([flags, dcid_len]) + dcid + bytes([scid_len]) + scid + pn_bytes
        return header + payload

    @staticmethod
    def fragment(packet: bytes, mtu: int) -> List[bytes]:
        mtu = int(mtu)
        if mtu <= 0:
            raise ValueError("mtu must be positive")
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


def generate_payload(
    size: int,
    entropy: float = 1.0,
    rng=None,
    byte_source=None,
) -> bytes:
    rng = rng or random.Random()
    byte_source = byte_source or os.urandom
    size = max(0, int(size))
    if size == 0:
        return b""

    # For performance, use simple random generation by default
    # Enhanced entropy is expensive and should be used sparingly
    if ENHANCED_AVAILABLE and size > 1000 and rng.random() < 0.1:  # Use enhanced only 10% of time for large packets
        try:
            enhancer = EntropyEnhancer()
            return enhancer.generate_realistic_encrypted_payload(size, content_type='mixed')
        except Exception:
            pass

    # Fast path for high entropy (most common case)
    if entropy >= 0.95:
        return byte_source(size)

    # Optimized generation for lower entropy
    if entropy < 0.5:
        # Low entropy - mostly repeated bytes
        base_byte = rng.randint(0, 255)
        data = bytearray([base_byte] * size)
        # Add some variation
        for _ in range(int(size * entropy)):
            data[rng.randint(0, size-1)] = rng.randint(0, 255)
        return bytes(data)
    else:
        # Medium to high entropy - mix of random and patterns
        return byte_source(size)


def profile_event_generator(profile, rng=None):
    """Yield native experimental profile events without rewriting volume/gaps."""
    rng = rng or random.Random()
    while True:
        steps = ProtocolMimicry.for_profile(profile, rng=rng)
        if not steps:
            steps = [PatternStep(size=0, delay=1.0)]
        for step in steps:
            yield ShapeEvent(byte_count=step.size, delay=step.delay)


def stream_generator(
    profile: TrafficProfile,
    target_mbps: Optional[float] = None,
    min_mbps: Optional[float] = None,
    max_mbps: Optional[float] = None,
    obfuscator: Optional[DynamicObfuscator] = None,
    entropy: float = 1.0,
    rng=None,
) -> Iterator[Tuple[List[bytes], float]]:
    """Compatibility iterator built on unmodified logical profile events."""
    if (min_mbps is None) != (max_mbps is None):
        raise ValueError("min_mbps and max_mbps must be given together")
    rng = rng or random.Random()
    obfuscator = obfuscator or DynamicObfuscator(rng=rng)
    events = profile_event_generator(profile, rng=rng)
    rate_controlled = target_mbps is not None or min_mbps is not None

    for event in events:
        if rate_controlled and event.byte_count == 0:
            continue
        payload = generate_payload(
            event.byte_count, entropy=entropy, rng=rng
        )
        fragments, _ = obfuscator.obfuscate(payload, profile=profile)
        if min_mbps is not None:
            rate_mbps = rng.uniform(min_mbps, max_mbps)
        else:
            rate_mbps = target_mbps
        if rate_mbps is None:
            delay = event.delay
        else:
            delay = sum(len(fragment) for fragment in fragments) / (
                mbps_to_bytes_per_second(rate_mbps)
            )
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


def build_obfuscator(cfg: ObfuscationConfig, rng=None, byte_source=None) -> DynamicObfuscator:
    return DynamicObfuscator(
        padding_strategy=cfg.padding_strategy,
        timing_jitter=cfg.timing_jitter,
        mtu=cfg.mtu,
        header_mode=cfg.header_mode,
        rng=rng,
        byte_source=byte_source,
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
