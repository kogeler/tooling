# Copyright © 2026 kogeler
# SPDX-License-Identifier: Apache-2.0

"""Uplink ratio accounting includes framed DATA and padded control bytes."""

import random

import pytest

from conftest import TEST_PSK
from control_protocol import CLIENT_TO_SERVER, FRAME_OVERHEAD, MessageType, derive_session_key
from traffic_masking_client import AdaptiveTrafficClient


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


def connected_client(clock, response_ratio=0.25):
    client = AdaptiveTrafficClient(
        "server.example",
        8888,
        response_ratio=response_ratio,
        psk=TEST_PSK,
        rng=random.Random(44),
        byte_source=lambda size: b"u" * size,
        monotonic_clock=clock,
    )
    client.socket = RecordingSocket()
    client.server_addr = ("192.0.2.10", 8888)
    client.client_nonce = b"c" * 16
    client.session_nonce = b"s" * 16
    client.session_send_key = derive_session_key(
        TEST_PSK,
        client.client_nonce,
        client.session_nonce,
        CLIENT_TO_SERVER,
    )
    client.handshake_accepted = True
    return client


def test_received_rate_uses_actual_monotonic_elapsed_time():
    clock = FakeClock()
    client = connected_client(clock)

    clock.advance(2.5)
    client._record_received_data(125_000)

    assert client.received_rate == pytest.approx(0.4)


def test_data_keepalive_framing_and_padding_share_one_uplink_budget():
    clock = FakeClock()
    client = connected_client(clock, response_ratio=0.25)
    client._record_received_data(120_000)

    keepalive_bytes = client._send_session_message(
        MessageType.KEEPALIVE, allow_budget_debt=True
    )
    while client.uplink_budget.available_bytes >= FRAME_OVERHEAD + 200:
        packet_size = min(
            500,
            int(client.uplink_budget.available_bytes) - FRAME_OVERHEAD,
        )
        assert client.send_packet(client.generate_response_packet(packet_size)) > 0

    observed_bytes = sum(len(datagram) for datagram, _ in client.socket.sent)
    assert keepalive_bytes > FRAME_OVERHEAD
    assert observed_bytes == client.stats["bytes_sent"]
    assert observed_bytes == client.uplink_budget.uplink_bytes
    assert client.uplink_budget.observed_ratio <= client.response_ratio
    assert (
        client.response_ratio - client.uplink_budget.observed_ratio
        < client.mtu / client.uplink_budget.downlink_bytes
    )


def test_uncredited_data_cannot_bypass_budget_but_keepalive_can_create_debt():
    clock = FakeClock()
    client = connected_client(clock, response_ratio=0.0)
    sequence = client.control_send_sequence

    assert client.send_packet(client.generate_response_packet(500)) == 0
    assert client.control_send_sequence == sequence
    assert client._send_session_message(
        MessageType.KEEPALIVE, allow_budget_debt=True
    ) > 0
    assert client.uplink_budget.available_bytes == 0


def test_client_snapshot_is_atomic_and_uses_monotonic_timestamp():
    clock = FakeClock()
    client = connected_client(clock, response_ratio=0.25)
    clock.advance(2.0)
    client._record_received_data(1000)
    sent = client._send_session_message(
        MessageType.KEEPALIVE, allow_budget_debt=True
    )

    snapshot = client.snapshot()
    assert snapshot.timestamp == 2.0
    assert snapshot.connected
    assert snapshot.handshake_accepted
    assert snapshot.bytes_received == 1000
    assert snapshot.packets_received == 1
    assert snapshot.bytes_sent == sent
    assert snapshot.packets_sent == 1
    assert snapshot.uplink_ratio == pytest.approx(sent / 1000)
