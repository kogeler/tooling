# Copyright © 2026 kogeler
# SPDX-License-Identifier: Apache-2.0

"""Fast in-process smoke tests for the core library (characterization baseline)."""

import random
import socket

import pytest

from masking_lib import (
    PayloadPadder,
    ProtocolMimicry,
    TrafficProfile,
    profile_event_generator,
)


@pytest.mark.parametrize("profile", list(TrafficProfile))
def test_for_profile_is_nonempty(profile):
    steps = ProtocolMimicry.for_profile(profile, rng=random.Random(profile.value))
    assert len(steps) > 0


@pytest.mark.parametrize("strategy", sorted(PayloadPadder.STRATEGIES))
def test_payload_padder_preserves_payload_and_only_adds_bytes(strategy):
    payload = b"test packet data"
    padder = PayloadPadder(strategy=strategy, rng=random.Random(1))
    transformed = padder.transform(payload)

    assert transformed.startswith(payload)
    assert len(transformed) >= len(payload)
    if strategy == "none":
        assert transformed == payload


def test_profile_event_generator_yields_native_shape_event():
    event = next(
        profile_event_generator(TrafficProfile.WEB_BROWSING, random.Random(3))
    )
    assert event.byte_count > 0
    assert event.delay > 0


def test_loopback_udp_roundtrip():
    """Basic UDP loopback works in this environment (ported connectivity check)."""
    receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    receiver.bind(("127.0.0.1", 0))
    receiver.settimeout(2.0)
    port = receiver.getsockname()[1]

    sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sender.sendto(b"PING", ("127.0.0.1", port))
        data, _ = receiver.recvfrom(64)
        assert data == b"PING"
    finally:
        sender.close()
        receiver.close()
