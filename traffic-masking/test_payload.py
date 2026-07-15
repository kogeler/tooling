# Copyright © 2026 kogeler
# SPDX-License-Identifier: Apache-2.0

"""Payload generation is non-deterministic and does not touch global RNG state."""

import random

from conftest import TEST_PSK
from traffic_masking_client import AdaptiveTrafficClient
from traffic_masking_server import PacketGenerator


def test_server_payload_is_not_deterministic():
    gen = PacketGenerator()
    p1 = gen.generate_packet(600)
    p2 = gen.generate_packet(600)
    assert len(p1) == 600 and len(p2) == 600
    # Payload region follows the 28-byte header (seq 4 + ts 8 + md5 16).
    assert p1[28:] != p2[28:]


def test_generate_packet_does_not_mutate_global_rng():
    gen = PacketGenerator()
    original_state = random.getstate()
    try:
        random.seed(1234)
        before = random.random()
        random.seed(1234)
        gen.generate_packet()
        after = random.random()
        assert before == after
    finally:
        random.setstate(original_state)


def test_generate_packet_guards_small_sizes():
    gen = PacketGenerator()
    # Smaller than the 28-byte header must not raise or produce negative sizes.
    pkt = gen.generate_packet(1)
    assert len(pkt) >= 28


def test_client_response_payload_is_not_deterministic():
    client = AdaptiveTrafficClient(
        "127.0.0.1", 9, psk=TEST_PSK
    )  # no socket opened in __init__
    p1 = client.generate_response_packet(600)
    p2 = client.generate_response_packet(600)
    # Header is [type 1][seq 4][ts 8] = 13 bytes; the payload after must differ.
    assert p1[13:] != p2[13:]


def test_client_response_does_not_mutate_global_rng():
    client = AdaptiveTrafficClient("127.0.0.1", 9, psk=TEST_PSK)
    original_state = random.getstate()
    try:
        random.seed(5678)
        before = random.random()
        random.seed(5678)
        client.generate_response_packet()
        after = random.random()
        assert before == after
    finally:
        random.setstate(original_state)
