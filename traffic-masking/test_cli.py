# Copyright © 2026 kogeler
# SPDX-License-Identifier: Apache-2.0

"""Input validation: bad config raises ValueError; bad CLI exits with code 2."""

import subprocess
import sys

import pytest

from conftest import CLIENT, SERVER
from traffic_masking_client import AdaptiveTrafficClient
from traffic_masking_server import MaskingTrafficServer


@pytest.mark.parametrize(
    "kwargs",
    [
        {"target_mbps": 0},
        {"target_mbps": -1},
        {"min_mbps": 5, "max_mbps": 2},  # min >= max
        {"min_mbps": 2},  # only one of the pair
        {"max_mbps": 8},  # only one of the pair
        {"target_mbps": 5, "mtu": 0},
        {"target_mbps": 5, "entropy": 1.5},
        {"target_mbps": 5, "stats_interval": 0},
    ],
)
def test_server_rejects_bad_config(kwargs):
    with pytest.raises(ValueError):
        MaskingTrafficServer(**kwargs)


def test_server_accepts_valid_floating_config():
    server = MaskingTrafficServer(min_mbps=2, max_mbps=8)
    # 5 Mbps midpoint -> 625_000 bytes/s (decimal).
    assert server.target_bytes_per_second == 625_000


@pytest.mark.parametrize(
    "kwargs",
    [
        {"response_ratio": 1.5},
        {"response_ratio": -0.1},
        {"entropy": 2.0},
        {"mtu": 0},
        {"stats_interval": -1},
    ],
)
def test_client_rejects_bad_config(kwargs):
    with pytest.raises(ValueError):
        AdaptiveTrafficClient("127.0.0.1", 8888, **kwargs)


def _run(script, *args):
    return subprocess.run(
        [sys.executable, script, *args],
        capture_output=True,
        text=True,
        timeout=15,
    )


def test_server_cli_bad_rate_exits_2():
    assert _run(SERVER, "--mbps", "0").returncode == 2


def test_server_cli_partial_range_exits_2():
    assert _run(SERVER, "--min-mbps", "2").returncode == 2


def test_client_cli_bad_response_exits_2():
    assert _run(CLIENT, "--server", "127.0.0.1", "--response", "2").returncode == 2
