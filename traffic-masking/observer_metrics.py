# Copyright © 2026 kogeler
# SPDX-License-Identifier: Apache-2.0

"""Metrics for traces captured at a declared external observer boundary."""

from __future__ import annotations

import math
from dataclasses import dataclass

UPLINK = "uplink"
DOWNLINK = "downlink"
BYTE_LAYERS = frozenset({"outer", "inner"})
DIRECTIONS = frozenset({UPLINK, DOWNLINK})


@dataclass(frozen=True, slots=True)
class ObserverEvent:
    """One externally observed datagram with explicit capture semantics."""

    timestamp: float
    direction: str
    outer_datagram_bytes: int
    connection_id: str
    capture_point: str
    encapsulation_overhead: int = 0

    def __post_init__(self):
        if (
            isinstance(self.timestamp, bool)
            or not isinstance(self.timestamp, (int, float))
            or not math.isfinite(self.timestamp)
            or self.timestamp < 0
        ):
            raise ValueError("timestamp must be a non-negative finite number")
        if self.direction not in DIRECTIONS:
            raise ValueError("direction must be 'uplink' or 'downlink'")
        if (
            isinstance(self.outer_datagram_bytes, bool)
            or not isinstance(self.outer_datagram_bytes, int)
            or self.outer_datagram_bytes <= 0
        ):
            raise ValueError("outer datagram bytes must be a positive integer")
        if (
            isinstance(self.encapsulation_overhead, bool)
            or not isinstance(self.encapsulation_overhead, int)
            or not 0 <= self.encapsulation_overhead <= self.outer_datagram_bytes
        ):
            raise ValueError(
                "encapsulation overhead must be within the outer datagram"
            )
        if not isinstance(self.connection_id, str) or not self.connection_id:
            raise ValueError("connection ID must be a non-empty string")
        if not isinstance(self.capture_point, str) or not self.capture_point:
            raise ValueError("capture point must be a non-empty string")

    @property
    def inner_datagram_bytes(self):
        return self.outer_datagram_bytes - self.encapsulation_overhead


@dataclass(frozen=True, slots=True)
class TraceWindow:
    """Directional byte and datagram totals for one half-open time window."""

    started_at: float
    ended_at: float
    uplink_outer_bytes: int
    downlink_outer_bytes: int
    uplink_overhead_bytes: int
    downlink_overhead_bytes: int
    uplink_datagrams: int
    downlink_datagrams: int

    @property
    def uplink_inner_bytes(self):
        return self.uplink_outer_bytes - self.uplink_overhead_bytes

    @property
    def downlink_inner_bytes(self):
        return self.downlink_outer_bytes - self.downlink_overhead_bytes


@dataclass(frozen=True, slots=True)
class IdleDistribution:
    count: int
    minimum: float | None
    median: float | None
    p95: float | None
    maximum: float | None
    mean: float | None


@dataclass(frozen=True, slots=True)
class DirectionRatio:
    byte_layer: str
    uplink_bytes: int
    downlink_bytes: int
    uplink_to_downlink: float | None


@dataclass(frozen=True, slots=True)
class BurstMetrics:
    byte_layer: str
    burst_count: int
    mean_bytes: float
    maximum_bytes: int
    maximum_datagrams: int
    maximum_duration: float


def select_trace(
    events,
    *,
    connection_id=None,
    capture_point=None,
    direction=None,
):
    """Return a timestamp-ordered trace restricted to declared dimensions."""
    if direction is not None and direction not in DIRECTIONS:
        raise ValueError("direction must be 'uplink' or 'downlink'")
    selected = []
    for event in events:
        if not isinstance(event, ObserverEvent):
            raise ValueError("trace entries must be ObserverEvent instances")
        if connection_id is not None and event.connection_id != connection_id:
            continue
        if capture_point is not None and event.capture_point != capture_point:
            continue
        if direction is not None and event.direction != direction:
            continue
        selected.append(event)
    return tuple(sorted(selected, key=lambda event: event.timestamp))


def fixed_windows(events, window_seconds, *, origin=None):
    """Aggregate outer and encapsulation bytes into fixed half-open windows."""
    try:
        window_seconds = float(window_seconds)
    except (TypeError, ValueError):
        raise ValueError("window seconds must be positive and finite") from None
    if not math.isfinite(window_seconds) or window_seconds <= 0:
        raise ValueError("window seconds must be positive and finite")

    ordered = select_trace(events)
    if not ordered:
        return ()
    if origin is None:
        origin = ordered[0].timestamp
    try:
        origin = float(origin)
    except (TypeError, ValueError):
        raise ValueError("window origin must be non-negative and finite") from None
    if not math.isfinite(origin) or origin < 0:
        raise ValueError("window origin must be non-negative and finite")
    if ordered[0].timestamp < origin:
        raise ValueError("window origin must not follow the first event")

    final_index = int((ordered[-1].timestamp - origin) // window_seconds)
    totals = [
        {
            "uplink_outer": 0,
            "downlink_outer": 0,
            "uplink_overhead": 0,
            "downlink_overhead": 0,
            "uplink_datagrams": 0,
            "downlink_datagrams": 0,
        }
        for _ in range(final_index + 1)
    ]
    for event in ordered:
        index = int((event.timestamp - origin) // window_seconds)
        direction = event.direction
        totals[index][f"{direction}_outer"] += event.outer_datagram_bytes
        totals[index][f"{direction}_overhead"] += event.encapsulation_overhead
        totals[index][f"{direction}_datagrams"] += 1

    return tuple(
        TraceWindow(
            started_at=origin + index * window_seconds,
            ended_at=origin + (index + 1) * window_seconds,
            uplink_outer_bytes=window["uplink_outer"],
            downlink_outer_bytes=window["downlink_outer"],
            uplink_overhead_bytes=window["uplink_overhead"],
            downlink_overhead_bytes=window["downlink_overhead"],
            uplink_datagrams=window["uplink_datagrams"],
            downlink_datagrams=window["downlink_datagrams"],
        )
        for index, window in enumerate(totals)
    )


def idle_gaps(events):
    """Return the complete inter-datagram gap distribution in seconds."""
    ordered = select_trace(events)
    return tuple(
        current.timestamp - previous.timestamp
        for previous, current in zip(ordered, ordered[1:])
    )


def summarize_idle_gaps(events):
    gaps = sorted(idle_gaps(events))
    if not gaps:
        return IdleDistribution(0, None, None, None, None, None)
    count = len(gaps)
    middle = count // 2
    median = (
        gaps[middle]
        if count % 2
        else (gaps[middle - 1] + gaps[middle]) / 2
    )
    p95_index = max(0, math.ceil(count * 0.95) - 1)
    return IdleDistribution(
        count=count,
        minimum=gaps[0],
        median=median,
        p95=gaps[p95_index],
        maximum=gaps[-1],
        mean=sum(gaps) / count,
    )


def direction_ratio(events, *, byte_layer="outer"):
    """Compute uplink/downlink byte ratio at the requested byte layer."""
    _validate_byte_layer(byte_layer)
    uplink = 0
    downlink = 0
    for event in select_trace(events):
        byte_count = _event_bytes(event, byte_layer)
        if event.direction == UPLINK:
            uplink += byte_count
        else:
            downlink += byte_count
    return DirectionRatio(
        byte_layer=byte_layer,
        uplink_bytes=uplink,
        downlink_bytes=downlink,
        uplink_to_downlink=(uplink / downlink if downlink else None),
    )


def burst_metrics(events, maximum_gap, *, byte_layer="outer"):
    """Group adjacent events separated by at most ``maximum_gap`` seconds."""
    _validate_byte_layer(byte_layer)
    try:
        maximum_gap = float(maximum_gap)
    except (TypeError, ValueError):
        raise ValueError("maximum burst gap must be non-negative and finite") from None
    if not math.isfinite(maximum_gap) or maximum_gap < 0:
        raise ValueError("maximum burst gap must be non-negative and finite")

    ordered = select_trace(events)
    if not ordered:
        return BurstMetrics(byte_layer, 0, 0.0, 0, 0, 0.0)
    bursts = []
    started_at = ordered[0].timestamp
    previous_at = started_at
    byte_count = _event_bytes(ordered[0], byte_layer)
    datagrams = 1
    for event in ordered[1:]:
        if event.timestamp - previous_at > maximum_gap:
            bursts.append((byte_count, datagrams, previous_at - started_at))
            started_at = event.timestamp
            byte_count = 0
            datagrams = 0
        byte_count += _event_bytes(event, byte_layer)
        datagrams += 1
        previous_at = event.timestamp
    bursts.append((byte_count, datagrams, previous_at - started_at))

    return BurstMetrics(
        byte_layer=byte_layer,
        burst_count=len(bursts),
        mean_bytes=sum(burst[0] for burst in bursts) / len(bursts),
        maximum_bytes=max(burst[0] for burst in bursts),
        maximum_datagrams=max(burst[1] for burst in bursts),
        maximum_duration=max(burst[2] for burst in bursts),
    )


def size_autocorrelation(events, *, lag=1, byte_layer="outer"):
    """Return Pearson autocorrelation of datagram sizes, or None if undefined."""
    _validate_byte_layer(byte_layer)
    if isinstance(lag, bool) or not isinstance(lag, int) or lag <= 0:
        raise ValueError("autocorrelation lag must be a positive integer")
    sizes = [_event_bytes(event, byte_layer) for event in select_trace(events)]
    if len(sizes) <= lag:
        return None
    left = sizes[:-lag]
    right = sizes[lag:]
    left_mean = sum(left) / len(left)
    right_mean = sum(right) / len(right)
    numerator = sum(
        (first - left_mean) * (second - right_mean)
        for first, second in zip(left, right)
    )
    left_variance = sum((value - left_mean) ** 2 for value in left)
    right_variance = sum((value - right_mean) ** 2 for value in right)
    denominator = math.sqrt(left_variance * right_variance)
    return numerator / denominator if denominator else None


def _event_bytes(event, byte_layer):
    if byte_layer == "outer":
        return event.outer_datagram_bytes
    return event.inner_datagram_bytes


def _validate_byte_layer(byte_layer):
    if byte_layer not in BYTE_LAYERS:
        raise ValueError("byte layer must be 'outer' or 'inner'")
