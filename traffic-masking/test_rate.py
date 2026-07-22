# Copyright © 2026 kogeler
# SPDX-License-Identifier: Apache-2.0

"""Rate correctness: decimal units and reservation-based pacing."""

import masking_lib
import pytest
from conftest import TEST_PSK
from masking_lib import ProtocolMimicry, RateLimiter, mbps_to_bytes_per_second
from traffic_masking_server import MaskingTrafficServer


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
    server = MaskingTrafficServer(target_mbps=1, psk=TEST_PSK)
    assert server.target_bytes_per_second == 125_000
    legacy_bytes_budget = 1 * 1024 * 1024 * 1.1  # what the old loop granted
    assert legacy_bytes_budget / server.target_bytes_per_second > 8


def test_fixed_and_floating_conversions_are_identical():
    fixed = MaskingTrafficServer(target_mbps=5, psk=TEST_PSK)
    floating = MaskingTrafficServer(
        min_mbps=2, max_mbps=8, psk=TEST_PSK
    )  # midpoint 5
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


def test_rate_limiter_tracks_target_without_exceeding_short_or_long_cap():
    clock = FakeClock()
    target = mbps_to_bytes_per_second(1)
    limiter = RateLimiter(target, burst_bytes=1200, clock=clock)
    sent = 0

    for _ in range(1200):
        reservation = limiter.reserve(1200)
        clock.advance(reservation.delay)
        sent += limiter.commit(reservation)
        assert sent <= target * clock.now + limiter.burst_bytes + 1

    sustained = (sent - limiter.burst_bytes) / clock.now
    assert sustained == pytest.approx(target, rel=0.01)


def test_rate_limiter_initial_burst_and_failed_send_refund_are_bounded():
    clock = FakeClock()
    target = mbps_to_bytes_per_second(1)
    limiter = RateLimiter(target, burst_bytes=1200, clock=clock)

    first = limiter.reserve(1200)
    assert first.delay == 0
    limiter.commit(first)

    failed = limiter.reserve(1200)
    assert failed.delay == pytest.approx(1200 / target)
    clock.advance(failed.delay)
    limiter.refund(failed)

    retry = limiter.reserve(1200)
    assert retry.delay == 0
    limiter.commit(retry, successful_bytes=600)

    after_partial = limiter.reserve(600)
    assert after_partial.delay == 0
    limiter.commit(after_partial)
