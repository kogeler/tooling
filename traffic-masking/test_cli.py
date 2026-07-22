# Copyright © 2026 kogeler
# SPDX-License-Identifier: Apache-2.0

"""Input validation: bad config raises ValueError; bad CLI exits with code 2."""

import os
import re
import shutil
import subprocess
import sys

import pytest

from conftest import BASE_DIR, CLIENT, SERVER, TEST_PSK
from control_protocol import MIN_CONTROL_MTU
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
        {"target_mbps": 5, "mtu": MIN_CONTROL_MTU - 1},
        {"target_mbps": 5, "stats_interval": 0},
        {"target_mbps": float("nan")},
        {"target_mbps": float("inf")},
        {"min_mbps": float("nan"), "max_mbps": 8},
        {"target_mbps": 5, "stats_interval": float("inf")},
        {"target_mbps": 5, "mtu": float("inf")},
        {"target_mbps": 5, "max_clients": 1.5},
        {"target_mbps": 5, "max_handshakes_per_second": 1.5},
    ],
)
def test_server_rejects_bad_config(kwargs):
    with pytest.raises(ValueError):
        MaskingTrafficServer(psk=TEST_PSK, **kwargs)


def test_server_accepts_valid_floating_config():
    server = MaskingTrafficServer(min_mbps=2, max_mbps=8, psk=TEST_PSK)
    # 5 Mbps midpoint -> 625_000 bytes/s (decimal).
    assert server.target_bytes_per_second == 625_000


@pytest.mark.parametrize(
    "kwargs",
    [
        {"response_ratio": 1.5},
        {"response_ratio": -0.1},
        {"mtu": 0},
        {"mtu": MIN_CONTROL_MTU - 1},
        {"stats_interval": -1},
        {"response_ratio": float("nan")},
        {"stats_interval": float("inf")},
        {"mtu": float("inf")},
        {"mtu": 1200.5},
        {"keepalive_interval": 0},
        {"keepalive_interval": 5, "receive_timeout": 6},
        {"reconnect_delay_min": 3, "reconnect_delay_max": 2},
    ],
)
def test_client_rejects_bad_config(kwargs):
    with pytest.raises(ValueError):
        AdaptiveTrafficClient("127.0.0.1", 8888, psk=TEST_PSK, **kwargs)


def test_client_default_response_is_download_only():
    client = AdaptiveTrafficClient("127.0.0.1", 8888, psk=TEST_PSK)
    assert client.response_ratio == 0.0


def _run(script, *args, env=None):
    return subprocess.run(
        [sys.executable, script, *args],
        capture_output=True,
        text=True,
        timeout=15,
        env=env,
    )


@pytest.mark.parametrize(
    ("script", "args", "message"),
    [
        (
            SERVER,
            ("--insecure-diagnostic", "--mbps", "0"),
            "positive finite number",
        ),
        (
            SERVER,
            ("--insecure-diagnostic", "--mbps", "nan"),
            "positive finite number",
        ),
        (
            SERVER,
            ("--insecure-diagnostic", "--min-mbps", "2"),
            "must be given together",
        ),
        (
            SERVER,
            ("--insecure-diagnostic", "--stats-interval", "inf"),
            "positive finite number",
        ),
        (
            SERVER,
            (
                "--insecure-diagnostic", "--shape-mode", "rate",
                "--profile", "voip",
            ),
            "profile is not valid in rate shape mode",
        ),
        (
            SERVER,
            ("--insecure-diagnostic", "--shape-mode", "profile"),
            "requires --profile",
        ),
        (
            SERVER,
            (
                "--insecure-diagnostic", "--shape-mode", "profile",
                "--profile", "voip", "--min-mbps", "0.5",
                "--max-mbps", "1",
            ),
            "min-mbps is not valid",
        ),
        (
            CLIENT,
            (
                "--server", "127.0.0.1", "--insecure-diagnostic",
                "--response", "2",
            ),
            "response ratio must be in [0.0, 1.0]",
        ),
        (
            CLIENT,
            (
                "--server", "127.0.0.1", "--insecure-diagnostic",
                "--stats-interval", "nan",
            ),
            "stats-interval must be a positive finite number",
        ),
        (
            CLIENT,
            (
                "--server", "127.0.0.1", "--insecure-diagnostic",
                "--keepalive-interval", "5", "--receive-timeout", "6",
            ),
            "maximum jittered keepalive interval",
        ),
        (
            CLIENT,
            (
                "--server", "127.0.0.1", "--insecure-diagnostic",
                "--reconnect-delay-min", "3", "--reconnect-delay-max", "2",
            ),
            "reconnect-delay-min must not exceed",
        ),
    ],
)
def test_invalid_cli_exits_2_with_useful_message(script, args, message):
    result = _run(script, *args)
    assert result.returncode == 2
    assert message in result.stderr


@pytest.mark.parametrize("script", [SERVER, CLIENT])
def test_cli_requires_psk_unless_diagnostic(script):
    args = [] if script == SERVER else ["--server", "127.0.0.1"]
    result = _run(script, *args)
    assert result.returncode == 2
    assert "--psk-file is required" in result.stderr


def test_timing_environment_defaults_are_validated():
    env = os.environ.copy()
    env["TRAFFIC_MASKING_RECEIVE_TIMEOUT"] = "0"
    result = _run(
        CLIENT,
        "--server",
        "127.0.0.1",
        "--insecure-diagnostic",
        env=env,
    )
    assert result.returncode == 2
    assert "receive-timeout must be a positive finite number" in result.stderr


def test_server_rejects_limits_below_per_client_rate():
    with pytest.raises(ValueError, match="max-total-mbps"):
        MaskingTrafficServer(target_mbps=5, max_total_mbps=4, psk=TEST_PSK)


@pytest.mark.parametrize(
    "factory",
    [
        lambda: MaskingTrafficServer(psk=32),
        lambda: AdaptiveTrafficClient("127.0.0.1", 8888, psk=32),
    ],
)
def test_constructors_reject_non_byte_psks(factory):
    with pytest.raises(ValueError, match="psk must be bytes"):
        factory()


def test_profile_mode_has_native_load_and_optional_cap():
    uncapped = MaskingTrafficServer(
        shape_mode="profile", profile="voip", psk=TEST_PSK
    )
    capped = MaskingTrafficServer(
        shape_mode="profile", profile="voip", max_mbps=1, psk=TEST_PSK
    )
    assert uncapped.target_mbps is None
    assert uncapped.max_mbps is None
    assert capped.configured_max_mbps == 1


def test_systemd_units_verify_when_analyzer_is_available():
    analyzer = shutil.which("systemd-analyze")
    if analyzer is None:
        pytest.skip("systemd-analyze is not installed")
    units = [
        BASE_DIR / "systemd" / "traffic-masking-server.service",
        BASE_DIR / "systemd" / "traffic-masking-client.service",
    ]
    result = subprocess.run(
        [analyzer, "verify", *(str(unit) for unit in units)],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr


def test_release_version_is_canonical_for_container_metadata():
    version = (BASE_DIR / ".version").read_text().strip()
    dockerfile = (BASE_DIR / "Dockerfile").read_text()

    assert re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version)
    assert "ARG VERSION" in dockerfile
    assert 'org.opencontainers.image.version="${VERSION}"' in dockerfile
    assert 'test "${VERSION}" = "$(cat /app/.version)"' in dockerfile
