# Copyright © 2026 kogeler
# SPDX-License-Identifier: Apache-2.0

"""Exact metric checks over a synthetic observer trace."""

import pytest

from observer_metrics import (
    DOWNLINK,
    UPLINK,
    ObserverEvent,
    burst_metrics,
    direction_ratio,
    fixed_windows,
    idle_gaps,
    select_trace,
    size_autocorrelation,
    summarize_idle_gaps,
)


@pytest.fixture
def synthetic_trace():
    """Synthetic arithmetic fixture; it is not a legitimate-traffic baseline."""
    return (
        ObserverEvent(0.0, DOWNLINK, 1200, "outer-1", "wan", 100),
        ObserverEvent(0.1, DOWNLINK, 800, "outer-1", "wan", 100),
        ObserverEvent(0.6, UPLINK, 400, "outer-1", "wan", 80),
        ObserverEvent(1.2, DOWNLINK, 1000, "outer-1", "wan", 100),
        ObserverEvent(1.3, UPLINK, 500, "outer-1", "wan", 80),
    )


def test_fixed_windows_preserve_direction_and_overhead(synthetic_trace):
    windows = fixed_windows(synthetic_trace, 1.0, origin=0.0)

    assert len(windows) == 2
    assert windows[0].started_at == 0.0
    assert windows[0].ended_at == 1.0
    assert windows[0].downlink_outer_bytes == 2000
    assert windows[0].uplink_outer_bytes == 400
    assert windows[0].downlink_inner_bytes == 1800
    assert windows[0].uplink_inner_bytes == 320
    assert windows[0].downlink_datagrams == 2
    assert windows[0].uplink_datagrams == 1
    assert windows[1].downlink_outer_bytes == 1000
    assert windows[1].uplink_outer_bytes == 500


def test_idle_direction_and_burst_metrics_are_exact(synthetic_trace):
    assert idle_gaps(synthetic_trace) == pytest.approx((0.1, 0.5, 0.6, 0.1))
    idle = summarize_idle_gaps(synthetic_trace)
    assert idle.count == 4
    assert idle.minimum == pytest.approx(0.1)
    assert idle.median == pytest.approx(0.3)
    assert idle.p95 == pytest.approx(0.6)
    assert idle.maximum == pytest.approx(0.6)
    assert idle.mean == pytest.approx(0.325)

    outer_ratio = direction_ratio(synthetic_trace)
    inner_ratio = direction_ratio(synthetic_trace, byte_layer="inner")
    assert outer_ratio.uplink_bytes == 900
    assert outer_ratio.downlink_bytes == 3000
    assert outer_ratio.uplink_to_downlink == pytest.approx(0.3)
    assert inner_ratio.uplink_bytes == 740
    assert inner_ratio.downlink_bytes == 2700

    bursts = burst_metrics(synthetic_trace, 0.2)
    assert bursts.burst_count == 3
    assert bursts.mean_bytes == pytest.approx(1300)
    assert bursts.maximum_bytes == 2000
    assert bursts.maximum_datagrams == 2
    assert bursts.maximum_duration == pytest.approx(0.1)


def test_selection_and_autocorrelation_have_declared_dimensions(synthetic_trace):
    downlink = select_trace(
        reversed(synthetic_trace),
        connection_id="outer-1",
        capture_point="wan",
        direction=DOWNLINK,
    )
    assert [event.timestamp for event in downlink] == [0.0, 0.1, 1.2]

    alternating = tuple(
        ObserverEvent(index, DOWNLINK, size, "outer-1", "wan")
        for index, size in enumerate((100, 200, 100, 200, 100, 200))
    )
    assert size_autocorrelation(alternating) == pytest.approx(-1.0)
    assert size_autocorrelation(alternating, lag=2) == pytest.approx(1.0)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"timestamp": -1},
        {"timestamp": "now"},
        {"direction": "sideways"},
        {"outer_datagram_bytes": 0},
        {"encapsulation_overhead": 101},
        {"connection_id": ""},
        {"capture_point": ""},
    ],
)
def test_observer_event_rejects_ambiguous_schema_values(kwargs):
    values = {
        "timestamp": 1.0,
        "direction": UPLINK,
        "outer_datagram_bytes": 100,
        "connection_id": "outer-1",
        "capture_point": "wan",
        "encapsulation_overhead": 10,
    }
    values.update(kwargs)
    with pytest.raises(ValueError):
        ObserverEvent(**values)
