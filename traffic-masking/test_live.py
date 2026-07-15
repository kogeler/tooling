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

from conftest import CLIENT, last_match, read_log, stop_process, wait_for

pytestmark = pytest.mark.live


def _server_args(port, lo=2, hi=4):
    return [
        "--host", "127.0.0.1", "--port", str(port),
        "--min-mbps", str(lo), "--max-mbps", str(hi),
        "--advanced", "--profile", "mixed", "--stats-interval", "1",
    ]


def test_transmission_bidirectional(spawn, start_server):
    """Client connects, receives downlink and emits uplink; server sees the client."""
    server, port = start_server(_server_args, "server")

    client = spawn(
        CLIENT,
        [
            "--server", "127.0.0.1", "--port", str(port),
            "--response", "0.3", "--advanced", "--uplink-profile", "mixed",
            "--stats-interval", "1",
        ],
        "client",
    )

    assert wait_for(client, "Rx:", 10.0), read_log(client)
    assert wait_for(server, "New client connected", 5.0), read_log(server)

    # Let a few stats windows accumulate, then check real downlink/uplink.
    time.sleep(4)
    rx = last_match(client, r"Rx:\s*([0-9.]+)\s*Mbps")
    tx = last_match(client, r"Tx:\s*([0-9.]+)\s*Mbps")
    assert rx is not None and rx > 0.0, read_log(client)
    assert tx is not None and tx > 0.0, read_log(client)


def test_reconnection_after_server_restart(spawn, start_server):
    """Three-phase: connected -> server down (no false success) -> restarted -> resumed."""
    server, port = start_server(_server_args, "server1")

    client = spawn(
        CLIENT,
        ["--server", "127.0.0.1", "--port", str(port), "--response", "0.3",
         "--stats-interval", "1"],
        "client",
    )
    assert wait_for(client, "Rx:", 10.0), read_log(client)

    # Phase 2: kill the server; the client must detect loss and must NOT falsely
    # report a reconnect while the server is down.
    stop_process(server)
    assert wait_for(client, "Connection lost", 20.0), read_log(client)
    downtime = read_log(client).split("Connection lost", 1)[1]
    assert "Reconnected successfully" not in downtime, read_log(client)

    # Phase 3: restart the server; the client must reconnect.
    reconnect_offset = client.mark_log()
    server2, _ = start_server(_server_args, "server2", port=port)
    assert wait_for(
        client, "Reconnected successfully", 25.0, offset=reconnect_offset
    ), read_log(client, offset=reconnect_offset)
    assert server2.process.poll() is None


def test_fixed_rate_is_not_inflated(spawn, start_server):
    """Characterization: --mbps 1 emits on the order of 1 Mbit/s, not ~8.8.

    The legacy pattern generator legitimately scales the commanded rate
    (bursts up to 4x for single windows), so this only pins the gross unit
    error: the old bits-as-bytes budget inflated the average ~8.8x.
    """
    server, port = start_server(
        lambda selected_port: [
            "--host", "127.0.0.1", "--port", str(selected_port),
            "--mbps", "1", "--stats-interval", "1",
        ],
        "server",
    )

    client = spawn(
        CLIENT,
        ["--server", "127.0.0.1", "--port", str(port), "--stats-interval", "1"],
        "client",
    )
    assert wait_for(client, "Rx:", 10.0), read_log(client)

    time.sleep(6)
    rates = [
        float(m)
        for m in re.findall(r"Rate:\s*([0-9.]+)\s*Mbps", read_log(server))
    ]
    assert rates, read_log(server)
    mean_rate = sum(rates) / len(rates)
    assert 0.1 <= mean_rate <= 3.0, rates
    assert max(rates) <= 5.0, rates


def test_floating_rate_stays_within_bounds(spawn, start_server):
    """Characterization: the emitted server rate stays within a slack of [min,max].

    (The old realistic-pattern runner's boundary-coverage "quality" scoring is
    intentionally not ported: it rewards exact-boundary teleporting, a behaviour a
    later stage removes.)
    """
    lo, hi = 2.0, 6.0
    server, port = start_server(
        lambda selected_port: _server_args(selected_port, lo, hi), "server"
    )

    client = spawn(
        CLIENT,
        ["--server", "127.0.0.1", "--port", str(port), "--stats-interval", "1"],
        "client",
    )
    assert wait_for(client, "Rx:", 10.0), read_log(client)

    time.sleep(6)
    rates = [
        float(m)
        for m in re.findall(r"Rate:\s*([0-9.]+)\s*Mbps", read_log(server))
    ]
    assert rates, read_log(server)
    assert min(rates) >= 0.0
    # Generous slack: this only guards against runaway rate, not shape quality.
    assert max(rates) <= hi * 1.75, rates
