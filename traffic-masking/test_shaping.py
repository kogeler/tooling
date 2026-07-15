# Copyright © 2026 kogeler
# SPDX-License-Identifier: Apache-2.0

"""Explicit shaping, packetization, and profile-mode contracts."""

import random

import pytest

from control_protocol import (
    CLIENT_TO_SERVER,
    FRAME_OVERHEAD,
    MessageType,
    derive_session_key,
    encode_frame,
)
from masking_lib import (
    Packetizer,
    PatternStep,
    ProtocolMimicry,
    ShapeEvent,
    TrafficProfile,
    profile_event_generator,
)
from traffic_masking_client import AdaptiveTrafficClient


def test_packetizer_preserves_large_event_and_final_datagram_ceiling():
    packetizer = Packetizer(datagram_ceiling=1200, framing_overhead=FRAME_OVERHEAD)
    payload = bytes(range(256)) * 40

    fragments = packetizer.packetize(payload)

    assert b"".join(fragments) == payload
    assert sum(map(len, fragments)) == len(payload)
    assert len(fragments) > 1
    assert all(len(fragment) + FRAME_OVERHEAD <= 1200 for fragment in fragments)


def test_shape_event_and_packetizer_reject_invalid_dimensions():
    with pytest.raises(ValueError, match="byte_count"):
        ShapeEvent(-1)
    with pytest.raises(ValueError, match="delay"):
        ShapeEvent(1, float("inf"))
    with pytest.raises(ValueError, match="no payload"):
        Packetizer(82, framing_overhead=82)


def test_profile_event_generator_preserves_native_steps(monkeypatch):
    steps = [
        ShapeEvent(8_000, 0.25),
        ShapeEvent(0, 1.5),
        ShapeEvent(120, 0.02),
    ]
    monkeypatch.setattr(
        ProtocolMimicry,
        "for_profile",
        lambda profile, rng=None: [
            PatternStep(size=event.byte_count, delay=event.delay)
            for event in steps
        ],
    )
    events = profile_event_generator(
        TrafficProfile.VIDEO_STREAMING, rng=random.Random(7)
    )

    observed = [next(events) for _ in steps]

    assert observed == steps


def test_voip_native_rate_remains_below_one_mbps_cap():
    steps = ProtocolMimicry.voip_call(
        codec="g711", rng=random.Random(1234)
    )
    total_bytes = sum(step.size for step in steps)
    total_seconds = sum(step.delay for step in steps)
    native_mbps = total_bytes * 8 / total_seconds / 1_000_000

    assert 0.05 <= native_mbps <= 0.08
    assert native_mbps < 1.0


def test_packetized_payloads_fit_after_authenticated_framing():
    packetizer = Packetizer(1200, framing_overhead=FRAME_OVERHEAD)
    payload = b"v" * 25_000

    datagrams = [
        encode_frame(
            MessageType.DATA,
            b"c" * 16,
            b"s" * 16,
            sequence,
            b"k" * 32,
            payload=fragment,
        )
        for sequence, fragment in enumerate(packetizer.packetize(payload), 1)
    ]

    assert all(len(datagram) <= 1200 for datagram in datagrams)
    assert sum(len(datagram) - FRAME_OVERHEAD for datagram in datagrams) == len(
        payload
    )


def test_large_video_event_is_not_truncated_to_one_datagram():
    event = ProtocolMimicry.video_streaming_session(
        quality="1080p", rng=random.Random(8)
    )[0]
    packetizer = Packetizer(1200, framing_overhead=FRAME_OVERHEAD)
    payload = b"v" * event.size

    fragments = packetizer.packetize(payload)

    assert event.size > packetizer.payload_ceiling
    assert len(fragments) > 1
    assert sum(map(len, fragments)) == event.size


class RecordingSocket:
    def __init__(self):
        self.sent = []

    def sendto(self, datagram, address):
        self.sent.append((bytes(datagram), address))
        return len(datagram)


def test_client_packetizes_large_uplink_before_session_framing():
    key = b"k" * 32
    client = AdaptiveTrafficClient("server.example", 8888, psk=key, mtu=1200)
    client.socket = RecordingSocket()
    client.server_addr = ("192.0.2.1", 8888)
    client.client_nonce = b"c" * 16
    client.session_nonce = b"s" * 16
    client.session_send_key = derive_session_key(
        key, client.client_nonce, client.session_nonce, CLIENT_TO_SERVER
    )
    client.handshake_accepted = True
    payload = b"u" * 9_000

    client.send_packet(payload)

    assert len(client.socket.sent) > 1
    assert all(len(datagram) <= client.mtu for datagram, _ in client.socket.sent)
    assert sum(
        len(datagram) - FRAME_OVERHEAD for datagram, _ in client.socket.sent
    ) == len(payload)
