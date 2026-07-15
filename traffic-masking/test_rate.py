# Copyright © 2026 kogeler
# SPDX-License-Identifier: Apache-2.0

"""Rate correctness: decimal-Mbps conversion and pacing budget accounting."""

from masking_lib import mbps_to_bytes_per_second
from traffic_masking_server import MaskingTrafficServer, _budget_bytes


def test_mbps_to_bytes_per_second_is_decimal():
    assert mbps_to_bytes_per_second(1) == 125_000
    assert mbps_to_bytes_per_second(8) == 1_000_000


def test_server_target_uses_decimal_conversion():
    # The old loop budgeted `mbps * 1024 * 1024` *bits* as if they were bytes
    # and added a 1.1 fudge factor, inflating --mbps 1 to ~8.8 Mbit/s of
    # payload. The stored target must be plain decimal bytes per second.
    server = MaskingTrafficServer(target_mbps=1)
    assert server.target_bytes_per_second == 125_000
    legacy_bytes_budget = 1 * 1024 * 1024 * 1.1  # what the old loop granted
    assert legacy_bytes_budget / server.target_bytes_per_second > 8


def test_fixed_and_floating_conversions_are_identical():
    fixed = MaskingTrafficServer(target_mbps=5)
    floating = MaskingTrafficServer(min_mbps=2, max_mbps=8)  # midpoint 5
    assert fixed.target_bytes_per_second == floating.target_bytes_per_second


def test_budget_zero_for_nonpositive_inputs():
    assert _budget_bytes(0, 1.0) == 0
    assert _budget_bytes(-5, 1.0) == 0
    assert _budget_bytes(125_000, 0.0) == 0
    assert _budget_bytes(125_000, -1.0) == 0


def test_budget_caps_idle_gap_to_one_tick():
    # A long idle gap must not turn into accumulated credit: at most one
    # scheduling tick of bytes is granted no matter how much time passed.
    rate = 125_000  # 1 Mbps
    assert _budget_bytes(rate, 60.0) == _budget_bytes(rate, 0.5)
    assert _budget_bytes(rate, 3600.0) == _budget_bytes(rate, 0.5)


def test_budget_tracks_configured_rate_over_fake_clock_window():
    # Simulate the pacing loop over a fake one-second window of 10 ms ticks
    # at a constant commanded rate; the granted budget must stay within ±15%
    # of the configured decimal rate (exact modulo integer truncation).
    rate = mbps_to_bytes_per_second(1)  # 125_000 bytes/s
    ticks = 100
    granted = sum(_budget_bytes(rate, 0.01) for _ in range(ticks))
    assert abs(granted - 125_000) <= 125_000 * 0.15
