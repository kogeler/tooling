# Copyright © 2026 kogeler
# SPDX-License-Identifier: Apache-2.0

"""Deterministic contracts for bounded floating-rate state."""

import random
import statistics

import pytest

from masking_lib import FloatingRate


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def test_floating_rate_moves_early_stays_bounded_and_has_variance():
    clock = FakeClock()
    process = FloatingRate(2.0, 6.0, clock=clock, rng=random.Random(1042))
    values = [process.value_mbps]

    for _ in range(600):
        clock.advance(0.1)
        values.append(process.update())

    assert all(2.0 < value < 6.0 for value in values)
    assert any(value != values[0] for value in values[1:21])
    derivatives = [
        abs(current - previous) / 0.1
        for previous, current in zip(values, values[1:])
    ]
    assert max(derivatives) <= process.max_slope_mbps_per_second + 1e-9
    assert statistics.pvariance(values[20:]) > 0.001
    assert 2.0 not in values and 6.0 not in values


@pytest.mark.parametrize(
    "kwargs",
    [
        {"minimum_mbps": 0, "maximum_mbps": 1},
        {"minimum_mbps": 2, "maximum_mbps": 1},
        {
            "minimum_mbps": 1,
            "maximum_mbps": 2,
            "max_slope_mbps_per_second": 0,
        },
    ],
)
def test_floating_rate_rejects_invalid_bounds_and_slope(kwargs):
    with pytest.raises(ValueError):
        FloatingRate(**kwargs)
