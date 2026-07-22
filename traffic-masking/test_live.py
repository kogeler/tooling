# Copyright © 2026 kogeler
# SPDX-License-Identifier: Apache-2.0

"""Native end-to-end tests: spawn the real server and client on loopback.

Ported from the former standalone test_traffic_masking.py and
test_realistic_patterns.py runners so nothing runs outside pytest. Bounded to stay
CI-safe. Reconnection cases use explicit short keepalive/receive/backoff values.
"""

import os
import re
import signal
import socket
import time

import pytest

from conftest import CLIENT, TEST_PSK, last_match, read_log, stop_process, wait_for
from control_protocol import (
    NONCE_SIZE,
    ZERO_NONCE,
    MessageType,
    decode_frame,
    encode_frame,
)

pytestmark = pytest.mark.live


def _server_args(port, psk_file, lo=2, hi=4):
    return [
        "--host", "127.0.0.1", "--port", str(port),
        "--shape-mode", "profile", "--max-mbps", str(hi),
        "--profile", "mixed", "--stats-interval", "1",
        "--psk-file", str(psk_file),
    ]


def _fast_client_timings():
    return [
        "--keepalive-interval", "0.2",
        "--keepalive-jitter", "0",
        "--receive-timeout", "0.8",
        "--reconnect-delay-min", "0.2",
        "--reconnect-delay-max", "0.5",
    ]


def test_transmission_bidirectional(spawn, start_server, psk_file):
    """Client connects, receives downlink and emits uplink; server sees the client."""
    server, port = start_server(
        lambda selected_port: _server_args(selected_port, psk_file), "server"
    )

    client = spawn(
        CLIENT,
        [
            "--server", "127.0.0.1", "--port", str(port),
            "--response", "0.3", "--padding", "random",
            "--stats-interval", "1", "--psk-file", str(psk_file),
        ],
        "client",
    )

    assert wait_for(client, "Rx:", 10.0), read_log(client)
    assert wait_for(server, "New client connected", 5.0), read_log(server)

    # Let a few stats windows accumulate, then check real downlink/uplink.
    time.sleep(4)
    client_log = read_log(client)
    rx_windows = [
        float(value) for value in re.findall(r"Rx:\s*([0-9.]+)\s*Mbps", client_log)
    ]
    tx_windows = [
        float(value) for value in re.findall(r"Tx:\s*([0-9.]+)\s*Mbps", client_log)
    ]
    assert any(rate > 0.0 for rate in rx_windows), client_log
    assert any(rate > 0.0 for rate in tx_windows), client_log


def test_reconnection_after_server_restart(spawn, start_server, psk_file):
    """Three-phase: connected -> server down (no false success) -> restarted -> resumed."""
    def server_args(selected_port):
        return _server_args(selected_port, psk_file)

    server, port = start_server(server_args, "server1")

    client = spawn(
        CLIENT,
        ["--server", "127.0.0.1", "--port", str(port), "--response", "0.3",
         "--stats-interval", "1", "--psk-file", str(psk_file),
         *_fast_client_timings()],
        "client",
    )
    assert wait_for(client, "Rx:", 10.0), read_log(client)

    # Phase 2: kill the server; the client must detect loss and must NOT falsely
    # report a reconnect while the server is down.
    stop_process(server)
    assert wait_for(client, "Connection lost", 5.0), read_log(client)
    downtime = read_log(client).split("Connection lost", 1)[1]
    assert "Reconnected successfully" not in downtime, read_log(client)

    # Phase 3: restart the server; the client must reconnect.
    reconnect_offset = client.mark_log()
    server2, _ = start_server(server_args, "server2", port=port)
    assert wait_for(
        client, "Reconnected successfully", 8.0, offset=reconnect_offset
    ), read_log(client, offset=reconnect_offset)
    assert server2.process.poll() is None
    assert "Receive error" not in read_log(client, offset=reconnect_offset)


def test_fixed_rate_is_not_inflated(spawn, start_server, psk_file):
    """Characterization: --mbps 1 emits on the order of 1 Mbit/s, not ~8.8.

    The legacy pattern generator legitimately scales the commanded rate
    (bursts up to 4x for single windows), so this only pins the gross unit
    error: the old bits-as-bytes budget inflated the average ~8.8x.
    """
    server, port = start_server(
        lambda selected_port: [
            "--host", "127.0.0.1", "--port", str(selected_port),
            "--mbps", "1", "--stats-interval", "1",
            "--psk-file", str(psk_file),
        ],
        "server",
    )

    client = spawn(
        CLIENT,
        [
            "--server", "127.0.0.1", "--port", str(port),
            "--stats-interval", "1", "--psk-file", str(psk_file),
        ],
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


def test_floating_rate_stays_within_bounds(spawn, start_server, psk_file):
    """Characterization: the emitted server rate stays within a slack of [min,max].

    (The old realistic-pattern runner's boundary-coverage "quality" scoring is
    intentionally not ported: it rewards exact-boundary teleporting, a behaviour a
    later stage removes.)
    """
    lo, hi = 2.0, 6.0
    server, port = start_server(
        lambda selected_port: [
            "--host", "127.0.0.1", "--port", str(selected_port),
            "--shape-mode", "rate", "--min-mbps", str(lo),
            "--max-mbps", str(hi), "--stats-interval", "1",
            "--psk-file", str(psk_file),
        ],
        "server",
    )

    client = spawn(
        CLIENT,
        [
            "--server", "127.0.0.1", "--port", str(port),
            "--stats-interval", "1", "--psk-file", str(psk_file),
        ],
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


def test_two_clients_share_total_cap_with_bounded_fairness(
    spawn, start_server, psk_file
):
    cap = 1.5
    server, port = start_server(
        lambda selected_port: [
            "--host", "127.0.0.1", "--port", str(selected_port),
            "--mbps", "1", "--max-total-mbps", str(cap),
            "--stats-interval", "1", "--psk-file", str(psk_file),
        ],
        "fair-server",
    )
    clients = [
        spawn(
            CLIENT,
            [
                "--server", "127.0.0.1", "--port", str(port),
                "--stats-interval", "1", "--psk-file", str(psk_file),
            ],
            f"fair-client-{index}",
        )
        for index in range(2)
    ]
    for client in clients:
        assert wait_for(client, "Authenticated session accepted", 5.0), read_log(
            client
        )

    time.sleep(5)
    client_rates = [
        last_match(client, r"Rx:\s*([0-9.]+)\s*Mbps") for client in clients
    ]
    total_rate = last_match(server, r"Total Rate:\s*([0-9.]+)\s*Mbps")
    assert all(rate is not None and rate > 0.4 for rate in client_rates)
    assert total_rate is not None and total_rate <= cap * 1.15
    assert abs(client_rates[0] - client_rates[1]) <= max(client_rates) * 0.35
    assert "Per-client:" in read_log(server)


def test_raw_probe_gets_only_bounded_challenge(start_server, psk_file):
    server, port = start_server(
        lambda selected_port: [
            "--host", "127.0.0.1", "--port", str(selected_port),
            "--mbps", "1", "--stats-interval", "1",
            "--psk-file", str(psk_file),
        ],
        "probe-server",
    )
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    probe.bind(("127.0.0.1", 0))
    probe.settimeout(0.5)
    try:
        probe.sendto(b"x", ("127.0.0.1", port))
        with pytest.raises(socket.timeout):
            probe.recvfrom(65535)

        hello = encode_frame(
            MessageType.HELLO,
            b"p" * NONCE_SIZE,
            ZERO_NONCE,
            1,
            TEST_PSK,
        )
        probe.sendto(hello, ("127.0.0.1", port))
        challenge, source = probe.recvfrom(65535)
        assert source == ("127.0.0.1", port)
        assert len(challenge) <= len(hello) * 3
        assert decode_frame(challenge, TEST_PSK).message_type is MessageType.CHALLENGE

        with pytest.raises(socket.timeout):
            probe.recvfrom(65535)
        assert "New client connected" not in read_log(server)
    finally:
        probe.close()


def test_wrong_psk_client_remains_unregistered(
    spawn, start_server, psk_file, tmp_path
):
    server, port = start_server(
        lambda selected_port: [
            "--host", "127.0.0.1", "--port", str(selected_port),
            "--mbps", "1", "--stats-interval", "1",
            "--psk-file", str(psk_file),
        ],
        "wrong-key-server",
    )
    wrong_psk = tmp_path / "wrong.psk"
    wrong_psk.write_bytes(b"w" * 32)
    wrong_psk.chmod(0o600)
    client = spawn(
        CLIENT,
        [
            "--server", "127.0.0.1", "--port", str(port),
            "--stats-interval", "1", "--psk-file", str(wrong_psk),
        ],
        "wrong-key-client",
    )
    assert wait_for(client, "Handshake HELLO sent", 5.0), read_log(client)
    time.sleep(2)
    assert "New client connected" not in read_log(server)
    assert "Authenticated session accepted" not in read_log(client)
    assert last_match(client, r"Rx:\s*([0-9.]+)\s*Mbps") == 0.0


def test_sigterm_stops_both_processes_cleanly(spawn, start_server, psk_file):
    server, port = start_server(
        lambda selected_port: [
            "--host", "127.0.0.1", "--port", str(selected_port),
            "--mbps", "1", "--stats-interval", "1",
            "--psk-file", str(psk_file),
        ],
        "signal-server",
    )
    client = spawn(
        CLIENT,
        [
            "--server", "127.0.0.1", "--port", str(port),
            "--stats-interval", "1", "--psk-file", str(psk_file),
        ],
        "signal-client",
    )
    assert wait_for(client, "Authenticated session accepted", 5.0), read_log(client)

    for spawned in (client, server):
        os.killpg(spawned.process.pid, signal.SIGTERM)
        assert spawned.process.wait(timeout=5) == 0, read_log(spawned)
        assert "Traceback" not in read_log(spawned)
