# Copyright © 2026 kogeler
# SPDX-License-Identifier: Apache-2.0

"""Bounded end-to-end tests for real server and client processes on loopback."""

import os
import signal
import socket
import time
from pathlib import Path

import pytest

from conftest import (
    CLIENT,
    TEST_PSK,
    process_group_exists,
    read_log,
    read_snapshots,
    stop_process,
    wait_for,
    wait_for_snapshot,
)
from control_protocol import (
    NONCE_SIZE,
    ZERO_NONCE,
    MessageType,
    decode_frame,
    encode_frame,
)

pytestmark = pytest.mark.live

STATS_INTERVAL = "0.2"


def _server_base(port, psk_file):
    return [
        "--host", "127.0.0.1", "--port", str(port),
        "--stats-interval", STATS_INTERVAL, "--stats-json",
        "--psk-file", str(psk_file),
    ]


def _fixed_server_args(port, psk_file, target=1.0, total_cap=100.0):
    return [
        *_server_base(port, psk_file),
        "--mbps", str(target), "--max-total-mbps", str(total_cap),
    ]


def _profile_server_args(port, psk_file, profile="mixed"):
    return [
        *_server_base(port, psk_file),
        "--shape-mode", "profile", "--profile", profile,
        "--max-mbps", "8",
    ]


def _client_args(port, psk_file, *extra):
    return [
        "--server", "127.0.0.1", "--port", str(port),
        "--stats-interval", STATS_INTERVAL, "--stats-json",
        "--psk-file", str(psk_file),
        *extra,
    ]


def _fast_client_timings():
    return [
        "--keepalive-interval", "0.1",
        "--keepalive-jitter", "0",
        "--receive-timeout", "0.4",
        "--reconnect-delay-min", "0.1",
        "--reconnect-delay-max", "0.2",
    ]


def _is_client_data(snapshot, minimum_bytes=1):
    return (
        snapshot.get("kind") == "client"
        and snapshot.get("connected") is True
        and snapshot.get("handshake_accepted") is True
        and snapshot.get("totals", {}).get("bytes_received", 0) >= minimum_bytes
    )


def _rate_between(first, second, key="bytes_received"):
    elapsed = second["timestamp"] - first["timestamp"]
    byte_count = second["totals"][key] - first["totals"][key]
    return byte_count * 8 / (elapsed * 1_000_000)


def test_correct_psk_downlink_matches_fixed_decimal_rate(
    spawn, start_server, psk_file
):
    target = 1.0
    server, port = start_server(
        lambda selected_port: _fixed_server_args(
            selected_port, psk_file, target=target
        ),
        "fixed-server",
    )
    client = spawn(CLIENT, _client_args(port, psk_file), "fixed-client")

    first = wait_for_snapshot(
        client,
        lambda snapshot: _is_client_data(snapshot, minimum_bytes=25_000),
        5.0,
        description="authenticated client data",
    )
    assert first is not None
    second = wait_for_snapshot(
        client,
        lambda snapshot: (
            _is_client_data(snapshot)
            and snapshot["timestamp"] >= first["timestamp"] + 1.0
        ),
        3.0,
        description="one-second fixed-rate observation",
    )
    assert second is not None
    observed = _rate_between(first, second)
    assert target * 0.85 <= observed <= target * 1.15, observed

    server_snapshot = wait_for_snapshot(
        server,
        lambda snapshot: (
            snapshot.get("kind") == "server"
            and len(snapshot.get("clients", [])) == 1
            and snapshot["clients"][0]["bytes_sent"] > 0
        ),
        2.0,
        description="server-side client counters",
    )
    assert server_snapshot is not None
    assert server_snapshot["clients"][0]["current_rate_mbps"] == target


def test_unvalidated_udp_gets_only_bounded_challenge(start_server, psk_file):
    server, port = start_server(
        lambda selected_port: _fixed_server_args(selected_port, psk_file),
        "probe-server",
    )
    destination = ("127.0.0.1", port)
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    replay_probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    probe.bind(("127.0.0.1", 0))
    replay_probe.bind(("127.0.0.1", 0))
    probe.settimeout(0.25)
    replay_probe.settimeout(0.25)
    try:
        hello = encode_frame(
            MessageType.HELLO,
            b"p" * NONCE_SIZE,
            ZERO_NONCE,
            1,
            TEST_PSK,
        )
        malformed = (b"x", hello[:-1], hello + b"x", hello[:-1] + b"!")
        sent_bytes = 0
        for datagram in malformed:
            sent_bytes += probe.sendto(datagram, destination)
        with pytest.raises(socket.timeout):
            probe.recvfrom(65_535)

        sent_bytes += probe.sendto(hello, destination)
        challenge_datagram, source = probe.recvfrom(65_535)
        challenge = decode_frame(challenge_datagram, TEST_PSK)
        assert source == destination
        assert challenge.message_type is MessageType.CHALLENGE
        assert len(challenge_datagram) <= sent_bytes * 3

        replayed_auth = encode_frame(
            MessageType.AUTH,
            challenge.client_nonce,
            challenge.session_nonce,
            challenge.sequence + 1,
            TEST_PSK,
            payload=challenge.payload,
        )
        replay_probe.sendto(replayed_auth, destination)
        replay_probe.sendto(replayed_auth, destination)
        with pytest.raises(socket.timeout):
            replay_probe.recvfrom(65_535)
        with pytest.raises(socket.timeout):
            probe.recvfrom(65_535)

        snapshot = wait_for_snapshot(
            server,
            lambda item: item.get("kind") == "server" and not item.get("clients"),
            2.0,
            description="empty validated-client set",
        )
        assert snapshot is not None
        assert "New client connected" not in read_log(server)
    finally:
        probe.close()
        replay_probe.close()


def test_wrong_psk_client_remains_unregistered(
    spawn, start_server, psk_file, tmp_path
):
    server, port = start_server(
        lambda selected_port: _fixed_server_args(selected_port, psk_file),
        "wrong-key-server",
    )
    wrong_psk = tmp_path / "wrong.psk"
    wrong_psk.write_bytes(b"w" * 32)
    wrong_psk.chmod(0o600)
    client = spawn(CLIENT, _client_args(port, wrong_psk), "wrong-key-client")

    assert wait_for(client, "Handshake HELLO sent", 3.0), read_log(client)
    client_snapshot = wait_for_snapshot(
        client,
        lambda snapshot: (
            snapshot.get("kind") == "client"
            and snapshot.get("handshake_accepted") is False
            and snapshot.get("totals", {}).get("bytes_received") == 0
            and snapshot.get("timestamp", 0) > 0
        ),
        2.0,
        description="unaccepted wrong-key client",
    )
    assert client_snapshot is not None
    server_snapshot = wait_for_snapshot(
        server,
        lambda snapshot: snapshot.get("kind") == "server" and not snapshot["clients"],
        2.0,
        description="server without wrong-key client",
    )
    assert server_snapshot is not None
    assert "New client connected" not in read_log(server)
    assert "Authenticated session accepted" not in read_log(client)


def test_bidirectional_response_ratio_is_accounted(
    spawn, start_server, psk_file
):
    response = 0.3
    server, port = start_server(
        lambda selected_port: _fixed_server_args(
            selected_port, psk_file, target=2.0
        ),
        "bidirectional-server",
    )
    client = spawn(
        CLIENT,
        _client_args(
            port,
            psk_file,
            "--response", str(response), "--padding", "random",
        ),
        "bidirectional-client",
    )

    client_snapshot = wait_for_snapshot(
        client,
        lambda snapshot: (
            _is_client_data(snapshot, minimum_bytes=200_000)
            and snapshot["totals"]["bytes_sent"] > 40_000
            and response * 0.85 <= snapshot["uplink_ratio"] <= response
        ),
        5.0,
        description="accounted bidirectional ratio",
    )
    assert client_snapshot is not None
    server_snapshot = wait_for_snapshot(
        server,
        lambda snapshot: (
            snapshot.get("kind") == "server"
            and len(snapshot.get("clients", [])) == 1
            and snapshot["clients"][0]["bytes_received"] > 40_000
        ),
        2.0,
        description="server-side uplink accounting",
    )
    assert server_snapshot is not None


def test_reconnection_after_server_restart(spawn, start_server, psk_file):
    def server_args(selected_port):
        return _fixed_server_args(selected_port, psk_file, target=1.5)

    server, port = start_server(server_args, "reconnect-server-1")
    client = spawn(
        CLIENT,
        _client_args(port, psk_file, *_fast_client_timings()),
        "reconnect-client",
    )
    connected = wait_for_snapshot(
        client,
        lambda snapshot: _is_client_data(snapshot, minimum_bytes=20_000),
        5.0,
        description="initial connected phase",
    )
    assert connected is not None

    stop_process(server)
    assert wait_for(client, "Connection lost", 3.0), read_log(client)
    downtime_offset = client.mark_log()
    time.sleep(0.45)
    assert "Reconnected successfully" not in read_log(
        client, offset=downtime_offset
    )

    reconnect_offset = client.mark_log()
    server2, _ = start_server(server_args, "reconnect-server-2", port=port)
    assert wait_for(
        client, "Reconnected successfully", 5.0, offset=reconnect_offset
    ), read_log(client, offset=reconnect_offset)
    resumed = wait_for_snapshot(
        client,
        lambda snapshot: (
            _is_client_data(snapshot)
            and snapshot["totals"]["bytes_received"]
            > connected["totals"]["bytes_received"]
        ),
        3.0,
        offset=reconnect_offset,
        description="data after reconnect",
    )
    assert resumed is not None
    assert server2.process.poll() is None
    assert "Receive error" not in read_log(client, offset=reconnect_offset)


def test_two_clients_share_total_cap_with_independent_state(
    spawn, start_server, psk_file
):
    target = 1.0
    cap = 1.5
    server, port = start_server(
        lambda selected_port: _fixed_server_args(
            selected_port, psk_file, target=target, total_cap=cap
        ),
        "fair-server",
    )
    clients = [
        spawn(CLIENT, _client_args(port, psk_file), f"fair-client-{index}")
        for index in range(2)
    ]
    for client in clients:
        snapshot = wait_for_snapshot(
            client,
            lambda item: _is_client_data(item, minimum_bytes=10_000),
            5.0,
            description="independent client data",
        )
        assert snapshot is not None

    first = wait_for_snapshot(
        server,
        lambda snapshot: (
            snapshot.get("kind") == "server"
            and len(snapshot.get("clients", [])) == 2
            and all(client["bytes_sent"] > 0 for client in snapshot["clients"])
        ),
        3.0,
        description="two server clients",
    )
    assert first is not None
    second = wait_for_snapshot(
        server,
        lambda snapshot: (
            snapshot.get("kind") == "server"
            and len(snapshot.get("clients", [])) == 2
            and snapshot["timestamp"] >= first["timestamp"] + 1.0
        ),
        3.0,
        description="two-client cap window",
    )
    assert second is not None

    first_by_address = {tuple(item["address"]): item for item in first["clients"]}
    second_by_address = {tuple(item["address"]): item for item in second["clients"]}
    assert len(second_by_address) == 2
    assert second_by_address.keys() == first_by_address.keys()
    elapsed = second["timestamp"] - first["timestamp"]
    rates = [
        (
            item["bytes_sent"] - first_by_address[address]["bytes_sent"]
        )
        * 8
        / (elapsed * 1_000_000)
        for address, item in second_by_address.items()
    ]
    assert all(item["current_rate_mbps"] == target for item in second["clients"])
    assert all(rate >= target * 0.55 for rate in rates), rates
    assert sum(rates) <= cap * 1.15, rates
    assert abs(rates[0] - rates[1]) <= max(rates) * 0.25, rates


def test_floating_rate_and_observed_egress_stay_bounded(
    spawn, start_server, psk_file
):
    lower, upper = 1.0, 2.0
    server, port = start_server(
        lambda selected_port: [
            *_server_base(selected_port, psk_file),
            "--shape-mode", "rate",
            "--min-mbps", str(lower), "--max-mbps", str(upper),
        ],
        "floating-server",
    )
    client = spawn(CLIENT, _client_args(port, psk_file), "floating-client")
    first = wait_for_snapshot(
        client,
        lambda snapshot: _is_client_data(snapshot, minimum_bytes=25_000),
        5.0,
        description="floating-rate data",
    )
    assert first is not None
    second = wait_for_snapshot(
        client,
        lambda snapshot: (
            _is_client_data(snapshot)
            and snapshot["timestamp"] >= first["timestamp"] + 1.0
        ),
        3.0,
        description="floating-rate observation window",
    )
    assert second is not None
    observed = _rate_between(first, second)
    assert lower * 0.85 <= observed <= upper * 1.15, observed

    server_snapshots = read_snapshots(server)
    targets = [
        client_state["current_rate_mbps"]
        for snapshot in server_snapshots
        for client_state in snapshot.get("clients", [])
    ]
    assert targets
    assert all(lower <= target <= upper for target in targets)


@pytest.mark.parametrize(
    "profile", ["web", "video", "voip", "file", "gaming", "mixed"]
)
def test_each_profile_emits_authenticated_data(
    profile, spawn, start_server, psk_file
):
    server, port = start_server(
        lambda selected_port: _profile_server_args(
            selected_port, psk_file, profile=profile
        ),
        f"profile-{profile}-server",
    )
    client = spawn(
        CLIENT,
        _client_args(port, psk_file),
        f"profile-{profile}-client",
    )
    snapshot = wait_for_snapshot(
        client,
        lambda item: _is_client_data(item),
        5.0,
        description=f"{profile} profile data",
    )
    assert snapshot is not None
    server_snapshot = wait_for_snapshot(
        server,
        lambda item: (
            item.get("kind") == "server"
            and item.get("pattern") == f"experimental-profile:{profile}"
            and len(item.get("clients", [])) == 1
        ),
        2.0,
        description=f"{profile} server snapshot",
    )
    assert server_snapshot is not None


def _socket_inodes(pid):
    inodes = set()
    for descriptor in (Path("/proc") / str(pid) / "fd").iterdir():
        try:
            target = os.readlink(descriptor)
        except FileNotFoundError:
            continue
        if target.startswith("socket:[") and target.endswith("]"):
            inodes.add(target[8:-1])
    return inodes


def _udp_socket_rows(pid):
    socket_inodes = _socket_inodes(pid)
    rows = []
    for table_name in ("udp", "udp6"):
        table = Path("/proc") / str(pid) / "net" / table_name
        for line in table.read_text().splitlines()[1:]:
            fields = line.split()
            if len(fields) < 10 or fields[9] not in socket_inodes:
                continue
            local_host, local_port = fields[1].rsplit(":", 1)
            rows.append(
                {
                    "family": table_name,
                    "inode": fields[9],
                    "local_host": local_host,
                    "local_port": int(local_port, 16),
                }
            )
    return socket_inodes, rows


@pytest.mark.skipif(not Path("/proc/self/net/udp").exists(), reason="requires procfs")
def test_deployment_smoke_has_only_declared_udp_sockets(
    spawn, start_server, psk_file
):
    server, port = start_server(
        lambda selected_port: _fixed_server_args(
            selected_port, psk_file, target=1.0
        ),
        "deployment-server",
    )
    client = spawn(
        CLIENT,
        _client_args(port, psk_file, "--response", "0.1"),
        "deployment-client",
    )
    snapshot = wait_for_snapshot(
        client,
        lambda item: (
            _is_client_data(item, minimum_bytes=10_000)
            and item["totals"]["bytes_sent"] > 0
        ),
        5.0,
        description="declared bidirectional flow",
    )
    assert snapshot is not None
    assert snapshot["server_address"] == ["127.0.0.1", port]

    server_inodes, server_udp = _udp_socket_rows(server.process.pid)
    client_inodes, client_udp = _udp_socket_rows(client.process.pid)
    assert len(server_inodes) == len(server_udp) == 1
    assert len(client_inodes) == len(client_udp) == 1
    assert server_udp[0]["family"] == "udp"
    assert server_udp[0]["local_port"] == port
    assert client_udp[0]["family"] == "udp"
    assert client_udp[0]["local_port"] not in (0, port)


def test_sigterm_stops_both_process_groups_cleanly(
    spawn, start_server, psk_file
):
    server, port = start_server(
        lambda selected_port: _fixed_server_args(selected_port, psk_file),
        "signal-server",
    )
    client = spawn(CLIENT, _client_args(port, psk_file), "signal-client")
    assert wait_for_snapshot(
        client,
        lambda snapshot: _is_client_data(snapshot),
        5.0,
        description="data before SIGTERM",
    ) is not None

    for spawned in (client, server):
        process_group_id = spawned.process.pid
        os.killpg(process_group_id, signal.SIGTERM)
        assert spawned.process.wait(timeout=3) == 0, read_log(spawned)
        assert not process_group_exists(process_group_id)
        assert "Traceback" not in read_log(spawned)
