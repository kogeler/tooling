# Copyright © 2026 kogeler
# SPDX-License-Identifier: Apache-2.0

"""Fast in-process smoke tests for the core library (characterization baseline)."""

import socket

import pytest

from masking_lib import (
    DynamicObfuscator,
    ProtocolMimicry,
    TrafficProfile,
    parse_profile,
    stream_generator,
)


def test_parse_profile_known_and_fallback():
    assert parse_profile("mixed") is TrafficProfile.MIXED
    assert parse_profile("web") is TrafficProfile.WEB_BROWSING
    # Unknown strings fall back to MIXED rather than raising.
    assert parse_profile("bogus") is TrafficProfile.MIXED


@pytest.mark.parametrize("profile", list(TrafficProfile))
def test_for_profile_is_nonempty(profile):
    steps = ProtocolMimicry.for_profile(profile)
    assert len(steps) > 0


def test_obfuscator_produces_fragments():
    obf = DynamicObfuscator()
    fragments, delay = obf.obfuscate(b"test packet data")
    assert len(fragments) > 0
    assert delay >= 0


def test_stream_generator_fixed_rate_yields():
    gen = stream_generator(TrafficProfile.MIXED, target_mbps=1.0)
    fragments, delay = next(gen)
    assert len(fragments) > 0
    assert delay > 0


def test_stream_generator_floating_rate_yields():
    gen = stream_generator(TrafficProfile.MIXED, min_mbps=1.0, max_mbps=5.0)
    fragments, delay = next(gen)
    assert len(fragments) > 0
    assert delay > 0


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
