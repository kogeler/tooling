# Copyright © 2026 kogeler
# SPDX-License-Identifier: Apache-2.0

"""Native end-to-end tests: spawn the real server and client on loopback.

Ported from the former standalone test_traffic_masking.py and
test_realistic_patterns.py runners so nothing runs outside pytest. Bounded to stay
CI-safe. Durations are dictated by the current hard-coded keepalive/receive
timeouts; a later stage adds timing knobs to shrink them.
"""

import re
import time

import pytest

from conftest import CLIENT, SERVER, free_udp_port, last_match, read_log, wait_for

pytestmark = pytest.mark.live


def _server_args(port, lo=2, hi=4):
    return [
        "--host", "127.0.0.1", "--port", str(port),
        "--min-mbps", str(lo), "--max-mbps", str(hi),
        "--advanced", "--profile", "mixed", "--stats-interval", "1",
    ]


def test_transmission_bidirectional(spawn):
    """Client connects, receives downlink and emits uplink; server sees the client."""
    port = free_udp_port()
    _server, slog = spawn(SERVER, _server_args(port), "server")
    assert wait_for(slog, "started", 5.0), read_log(slog)

    _client, clog = spawn(
        CLIENT,
        [
            "--server", "127.0.0.1", "--port", str(port),
            "--response", "0.3", "--advanced", "--uplink-profile", "mixed",
            "--stats-interval", "1",
        ],
        "client",
    )

    assert wait_for(clog, "Rx:", 10.0), read_log(clog)
    assert wait_for(slog, "New client connected", 5.0), read_log(slog)

    # Let a few stats windows accumulate, then check real downlink/uplink.
    time.sleep(4)
    rx = last_match(clog, r"Rx:\s*([0-9.]+)\s*Mbps")
    tx = last_match(clog, r"Tx:\s*([0-9.]+)\s*Mbps")
    assert rx is not None and rx > 0.0, read_log(clog)
    assert tx is not None and tx > 0.0, read_log(clog)


def test_reconnection_after_server_restart(spawn):
    """Three-phase: connected -> server down (no false success) -> restarted -> resumed."""
    port = free_udp_port()
    args = _server_args(port)

    server, slog = spawn(SERVER, args, "server1")
    assert wait_for(slog, "started", 5.0), read_log(slog)

    _client, clog = spawn(
        CLIENT,
        ["--server", "127.0.0.1", "--port", str(port), "--response", "0.3",
         "--stats-interval", "1"],
        "client",
    )
    assert wait_for(clog, "Rx:", 10.0), read_log(clog)

    # Phase 2: kill the server; the client must detect loss and must NOT falsely
    # report a reconnect while the server is down.
    server.terminate()
    server.wait(timeout=5)
    assert wait_for(clog, "Connection lost", 20.0), read_log(clog)
    downtime = read_log(clog).split("Connection lost", 1)[1]
    assert "Reconnected successfully" not in downtime, read_log(clog)

    # Phase 3: restart the server; the client must reconnect.
    _server2, slog2 = spawn(SERVER, args, "server2")
    assert wait_for(slog2, "started", 5.0), read_log(slog2)
    assert wait_for(clog, "Reconnected successfully", 25.0), read_log(clog)


def test_floating_rate_stays_within_bounds(spawn):
    """Characterization: the emitted server rate stays within a slack of [min,max].

    (The old realistic-pattern runner's boundary-coverage "quality" scoring is
    intentionally not ported: it rewards exact-boundary teleporting, a behaviour a
    later stage removes.)
    """
    port = free_udp_port()
    lo, hi = 2.0, 6.0
    _server, slog = spawn(SERVER, _server_args(port, lo, hi), "server")
    assert wait_for(slog, "started", 5.0), read_log(slog)

    _client, clog = spawn(
        CLIENT,
        ["--server", "127.0.0.1", "--port", str(port), "--stats-interval", "1"],
        "client",
    )
    assert wait_for(clog, "Rx:", 10.0), read_log(clog)

    time.sleep(6)
    rates = [float(m) for m in re.findall(r"Rate:\s*([0-9.]+)\s*Mbps", read_log(slog))]
    assert rates, read_log(slog)
    assert min(rates) >= 0.0
    # Generous slack: this only guards against runaway rate, not shape quality.
    assert max(rates) <= hi * 1.75, rates
