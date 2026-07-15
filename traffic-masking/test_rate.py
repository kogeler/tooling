# Copyright © 2026 kogeler
# SPDX-License-Identifier: Apache-2.0

"""Rate correctness: decimal-Mbps conversion and pacing budget accounting."""

import masking_lib
import pytest
from masking_lib import ProtocolMimicry, mbps_to_bytes_per_second
from traffic_masking_server import MaskingTrafficServer, _budget_bytes, _RateBudget


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


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


def test_file_transfer_profile_uses_decimal_mbps(monkeypatch):
    monkeypatch.setattr(masking_lib.random, "randint", lambda low, high: low)

    def deterministic_uniform(low, high):
        if (low, high) in ((0.92, 1.0), (0.9, 1.1)):
            return 1.0
        return (low + high) / 2

    monkeypatch.setattr(masking_lib.random, "uniform", deterministic_uniform)
    first_step = ProtocolMimicry.file_transfer_session(target_mbps=1.0)[0]
    assert first_step.size / first_step.delay == pytest.approx(125_000)


def test_budget_zero_for_nonpositive_inputs():
    assert _budget_bytes(0, 1.0) == 0
    assert _budget_bytes(-5, 1.0) == 0
    assert _budget_bytes(125_000, 0.0) == 0
    assert _budget_bytes(125_000, -1.0) == 0


def test_budget_caps_idle_gap_to_one_tick():
    # A long idle gap must not turn into accumulated credit: at most one
    # scheduling tick of bytes is granted no matter how much time passed.
    rate = 125_000  # 1 Mbps
    assert _budget_bytes(rate, 60.0) == _budget_bytes(rate, 0.1)
    assert _budget_bytes(rate, 3600.0) == _budget_bytes(rate, 0.1)


def test_budget_tracks_configured_rate_over_fake_clock_window():
    # Simulate the pacing loop over a fake one-second window of 10 ms ticks
    # at a constant commanded rate; the granted budget must stay within ±15%
    # of the configured decimal rate (exact modulo integer truncation).
    clock = FakeClock()
    budget = _RateBudget(clock=clock)
    target = mbps_to_bytes_per_second(1)
    submitted = 0

    for _ in range(100):
        clock.advance(0.01)
        allowed = budget.accrue(target)
        submitted += allowed
        budget.consume(allowed)

    assert abs(submitted - target) <= target * 0.15


def test_rate_budget_caps_elapsed_and_resets_idle_credit():
    clock = FakeClock()
    budget = _RateBudget(clock=clock)
    target = mbps_to_bytes_per_second(1)

    clock.advance(60)
    assert budget.accrue(target) == _budget_bytes(target, 0.1)
    budget.reset()
    assert budget.available == 0

    clock.advance(0.01)
    assert budget.accrue(target) == pytest.approx(
        _budget_bytes(target, 0.01), abs=1
    )
