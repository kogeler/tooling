# Copyright © 2026 kogeler
# SPDX-License-Identifier: Apache-2.0

"""Per-client shaping isolation and aggregate-cap fairness."""

import threading
from types import SimpleNamespace

import pytest

from conftest import TEST_PSK
from masking_lib import ShapeEvent, mbps_to_bytes_per_second
from traffic_masking_client import AdaptiveTrafficClient
from traffic_masking_server import MaskingTrafficServer


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class RecordingSocket:
    def __init__(self):
        self.sent = []

    def sendto(self, datagram, address):
        self.sent.append((bytes(datagram), address))
        return len(datagram)


def make_server(clock, **kwargs):
    server = MaskingTrafficServer(
        target_mbps=1,
        psk=TEST_PSK,
        clock=clock,
        monotonic_clock=clock,
        sleep=clock.advance,
        byte_source=lambda size: b"n" * size,
        cookie_secret=b"z" * 32,
        **kwargs,
    )
    server.socket = RecordingSocket()
    return server


def add_client(server, marker):
    frame = SimpleNamespace(
        client_nonce=bytes([marker]) * 16,
        session_nonce=bytes([marker + 16]) * 16,
        sequence=1,
    )
    client = server._new_client_state(
        frame,
        server._clock(),
        TEST_PSK,
        TEST_PSK,
    )
    address = ("127.0.0.1", 20000 + marker)
    server._add_client(address, client)
    return address, client


def exercise_round_robin(server, clients, rounds=400):
    fragment = b"d" * server.data_payload_ceiling
    for _ in range(rounds):
        for address, client in clients:
            server._send_fragment(address, client, fragment)


def test_clients_own_independent_generator_limiter_rng_and_counters():
    clock = FakeClock()
    server = MaskingTrafficServer(
        min_mbps=2,
        max_mbps=6,
        max_total_mbps=12,
        psk=TEST_PSK,
        clock=clock,
        monotonic_clock=clock,
        sleep=clock.advance,
        byte_source=lambda size: b"n" * size,
        cookie_secret=b"z" * 32,
    )
    first = add_client(server, 1)[1]
    second = add_client(server, 2)[1]

    assert first["packet_gen"] is not second["packet_gen"]
    assert first["rate_limiter"] is not second["rate_limiter"]
    assert first["floating_rate"] is not second["floating_rate"]
    first_rates = []
    second_rates = []
    for _ in range(20):
        clock.advance(0.1)
        server._next_shape_event(first)
        server._next_shape_event(second)
        first_rates.append(first["current_rate_mbps"])
        second_rates.append(second["current_rate_mbps"])

    assert first_rates != second_rates
    assert all(2 < value < 6 for value in first_rates + second_rates)


def test_two_clients_share_binding_total_cap_fairly_without_exceeding_it():
    clock = FakeClock()
    server = make_server(clock, max_total_mbps=1.5)
    clients = [add_client(server, 1), add_client(server, 2)]

    exercise_round_robin(server, clients)

    total_bytes = server.stats["bytes_sent"]
    total_cap = mbps_to_bytes_per_second(server.max_total_mbps)
    assert total_bytes <= total_cap * clock.now + server.mtu + 1
    sustained_total = (total_bytes - server.mtu) / clock.now
    assert sustained_total == pytest.approx(total_cap, rel=0.02)
    first_bytes = clients[0][1]["bytes_sent"]
    second_bytes = clients[1][1]["bytes_sent"]
    assert first_bytes == second_bytes
    assert abs(first_bytes - second_bytes) / first_bytes <= 0.05


def test_two_clients_each_track_target_when_total_cap_is_not_binding():
    clock = FakeClock()
    server = make_server(clock, max_total_mbps=3)
    clients = [add_client(server, 3), add_client(server, 4)]

    exercise_round_robin(server, clients)

    for _, client in clients:
        sustained = (client["bytes_sent"] - server.mtu) / clock.now
        assert sustained == pytest.approx(mbps_to_bytes_per_second(1), rel=0.03)


def test_profile_gap_starts_after_the_last_fragment_is_submitted():
    clock = FakeClock()
    server = MaskingTrafficServer(
        shape_mode="profile",
        profile="voip",
        padding="none",
        max_total_mbps=100,
        psk=TEST_PSK,
        clock=clock,
        monotonic_clock=clock,
        sleep=clock.advance,
        byte_source=lambda size: b"n" * size,
        cookie_secret=b"z" * 32,
    )
    server.socket = RecordingSocket()
    address, client = add_client(server, 5)
    client["generator"] = iter(
        [
            ShapeEvent(server.data_payload_ceiling * 2, delay=0.5),
            ShapeEvent(100, delay=0),
        ]
    )

    for _ in range(2):
        fragment = server._next_client_fragment(client)
        assert fragment is not None
        server._send_fragment(address, client, fragment)
        server._complete_client_fragment(client)

    completed_at = clock.now
    assert client["next_event_at"] == pytest.approx(completed_at + 0.5)
    assert server._next_client_fragment(client) is None
    clock.advance(0.5)
    assert server._next_client_fragment(client) is not None


def test_threaded_add_touch_cleanup_preserves_registered_client_counters():
    clock = FakeClock()
    server = make_server(clock)
    main_address, _ = add_client(server, 10)
    assert server._record_client_receive(main_address, 100.0)
    worker_count = 7
    barrier = threading.Barrier(worker_count)
    failures = []

    def touch_main():
        try:
            barrier.wait()
            for _ in range(1000):
                assert server._record_client_receive(main_address, 100.0, 1)
        except Exception as exc:  # pragma: no cover - reported after join
            failures.append(exc)

    def churn(marker):
        try:
            barrier.wait()
            for port in range(200):
                frame = SimpleNamespace(
                    client_nonce=bytes([marker]) * 16,
                    session_nonce=bytes([marker + 16]) * 16,
                    sequence=1,
                )
                client = server._new_client_state(frame, 0.0, TEST_PSK, TEST_PSK)
                address = ("127.0.0.1", 30000 + marker * 1000 + port)
                server._add_client(address, client)
                server._record_client_receive(address, 0.0, 1)
        except Exception as exc:  # pragma: no cover - reported after join
            failures.append(exc)

    def cleanup():
        try:
            barrier.wait()
            for _ in range(400):
                server._remove_inactive_clients(10.0, idle_seconds=5.0)
        except Exception as exc:  # pragma: no cover - reported after join
            failures.append(exc)

    threads = [threading.Thread(target=touch_main) for _ in range(4)]
    threads.extend(threading.Thread(target=churn, args=(marker,)) for marker in (20, 21))
    threads.append(threading.Thread(target=cleanup))
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert failures == []
    assert all(not thread.is_alive() for thread in threads)
    main = next(
        item for item in server.snapshot(now=10.0).clients
        if item.address == main_address
    )
    assert main.bytes_received == 4000
    assert main.packets_received == 4000


def test_server_and_client_stop_are_idempotent_and_join_non_daemon_workers():
    server = MaskingTrafficServer(host="127.0.0.1", port=0, psk=TEST_PSK)
    server.start()
    assert all(not thread.daemon for thread in server.worker_threads)
    assert server.stop()
    assert server.stop()
    assert all(not thread.is_alive() for thread in server.worker_threads)

    client = AdaptiveTrafficClient("127.0.0.1", 9, psk=TEST_PSK)
    client.connect()
    assert all(not thread.daemon for thread in client.worker_threads)
    assert client.stop()
    assert client.stop()
    assert all(not thread.is_alive() for thread in client.worker_threads)


def test_server_stop_event_interrupts_a_long_pacing_wait():
    server = MaskingTrafficServer(psk=TEST_PSK)
    waiter_started = threading.Event()
    result = []

    def wait_for_pacing():
        waiter_started.set()
        result.append(server._wait_for_pacing(60.0))

    thread = threading.Thread(target=wait_for_pacing)
    thread.start()
    assert waiter_started.wait(timeout=1)
    server._stop_event.set()
    thread.join(timeout=1)

    assert not thread.is_alive()
    assert result == [True]
