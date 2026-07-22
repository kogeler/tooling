# Copyright © 2026 kogeler
# SPDX-License-Identifier: Apache-2.0

"""Binary control framing, authentication, cookies and PSK validation."""

import struct
import time

import pytest

from control_protocol import (
    CLIENT_TO_SERVER,
    COOKIE_SIZE,
    FRAME_OVERHEAD,
    HEADER_SIZE,
    MAX_PADDING_SIZE,
    MAX_PAYLOAD_SIZE,
    MAX_PSK_SIZE,
    MIN_CONTROL_MTU,
    NONCE_SIZE,
    MessageType,
    ProtocolError,
    SERVER_TO_CLIENT,
    create_cookie,
    decode_frame,
    derive_session_key,
    encode_frame,
    inspect_frame,
    load_psk,
    make_padding,
    verify_cookie,
)
from traffic_masking_server import MaskingTrafficServer
from traffic_masking_client import AdaptiveTrafficClient

KEY = b"k" * 32
CLIENT_NONCE = b"c" * NONCE_SIZE
SESSION_NONCE = b"s" * NONCE_SIZE


@pytest.mark.parametrize("message_type", list(MessageType))
def test_frame_round_trip_for_every_message_type(message_type):
    encoded = encode_frame(
        message_type,
        CLIENT_NONCE,
        SESSION_NONCE,
        42,
        KEY,
        payload=b"payload",
        padding=b"pad",
    )
    decoded = decode_frame(encoded, KEY)
    assert decoded.message_type is message_type
    assert decoded.client_nonce == CLIENT_NONCE
    assert decoded.session_nonce == SESSION_NONCE
    assert decoded.sequence == 42
    assert decoded.payload == b"payload"
    assert decoded.padding == b"pad"
    assert len(encoded) == FRAME_OVERHEAD + len(b"payloadpad")


@pytest.mark.parametrize("cut", [0, 1, FRAME_OVERHEAD - 1])
def test_truncated_frame_is_rejected(cut):
    encoded = encode_frame(
        MessageType.HELLO, CLIENT_NONCE, SESSION_NONCE, 1, KEY
    )
    with pytest.raises(ProtocolError, match="truncated|length"):
        decode_frame(encoded[:cut], KEY)


def test_unknown_version_and_type_are_rejected():
    encoded = bytearray(
        encode_frame(MessageType.HELLO, CLIENT_NONCE, SESSION_NONCE, 1, KEY)
    )
    encoded[4] = 99
    with pytest.raises(ProtocolError, match="version"):
        inspect_frame(encoded)

    encoded[4] = 1
    encoded[5] = 99
    with pytest.raises(ProtocolError, match="message type"):
        inspect_frame(encoded)


def test_corrupt_tag_is_rejected():
    encoded = bytearray(
        encode_frame(MessageType.DATA, CLIENT_NONCE, SESSION_NONCE, 7, KEY)
    )
    encoded[-1] ^= 1
    with pytest.raises(ProtocolError, match="authentication tag"):
        decode_frame(encoded, KEY)


def test_keys_and_nonces_must_be_byte_strings():
    with pytest.raises(ProtocolError, match="client nonce must be bytes"):
        encode_frame(MessageType.HELLO, NONCE_SIZE, SESSION_NONCE, 1, KEY)
    with pytest.raises(ProtocolError, match="key must be bytes"):
        encode_frame(MessageType.HELLO, CLIENT_NONCE, SESSION_NONCE, 1, 32)


def test_oversized_declared_lengths_are_rejected():
    encoded = bytearray(
        encode_frame(MessageType.DATA, CLIENT_NONCE, SESSION_NONCE, 7, KEY)
    )
    payload_length_offset = HEADER_SIZE - 4
    struct.pack_into("!H", encoded, payload_length_offset, MAX_PAYLOAD_SIZE + 1)
    with pytest.raises(ProtocolError, match="payload is too large"):
        inspect_frame(encoded)

    encoded = bytearray(
        encode_frame(MessageType.DATA, CLIENT_NONCE, SESSION_NONCE, 7, KEY)
    )
    padding_length_offset = HEADER_SIZE - 2
    struct.pack_into("!H", encoded, padding_length_offset, MAX_PADDING_SIZE + 1)
    with pytest.raises(ProtocolError, match="padding is too large"):
        inspect_frame(encoded)


def test_cookie_binds_address_nonce_sequence_and_expiry():
    cookie = create_cookie(
        KEY,
        ("127.0.0.1", 12345),
        CLIENT_NONCE,
        SESSION_NONCE,
        hello_sequence=10,
        expires_at=110,
    )
    assert len(cookie) == COOKIE_SIZE
    decoded = verify_cookie(
        cookie,
        KEY,
        ("127.0.0.1", 12345),
        CLIENT_NONCE,
        SESSION_NONCE,
        now=100,
        max_future_seconds=10,
    )
    assert decoded.hello_sequence == 10
    assert decoded.expires_at == 110

    with pytest.raises(ProtocolError, match="authentication tag"):
        verify_cookie(
            cookie,
            KEY,
            ("127.0.0.1", 54321),
            CLIENT_NONCE,
            SESSION_NONCE,
            now=100,
            max_future_seconds=10,
        )
    with pytest.raises(ProtocolError, match="expired"):
        verify_cookie(
            cookie,
            KEY,
            ("127.0.0.1", 12345),
            CLIENT_NONCE,
            SESSION_NONCE,
            now=111,
            max_future_seconds=10,
        )


def test_session_key_is_bound_to_both_nonces():
    key = derive_session_key(
        KEY, CLIENT_NONCE, SESSION_NONCE, CLIENT_TO_SERVER
    )
    assert key != derive_session_key(
        KEY, b"d" * NONCE_SIZE, SESSION_NONCE, CLIENT_TO_SERVER
    )
    assert key != derive_session_key(
        KEY, CLIENT_NONCE, b"t" * NONCE_SIZE, CLIENT_TO_SERVER
    )
    assert key != derive_session_key(
        KEY, CLIENT_NONCE, SESSION_NONCE, SERVER_TO_CLIENT
    )
    with pytest.raises(ProtocolError, match="direction"):
        derive_session_key(KEY, CLIENT_NONCE, SESSION_NONCE, b"sideways")


class FixedRng:
    def __init__(self, value):
        self.value = value

    def randint(self, minimum, maximum):
        assert minimum <= self.value <= maximum
        return self.value


def test_control_padding_respects_deterministic_bounds():
    padding = make_padding(
        FixedRng(7), byte_source=lambda size: b"p" * size, minimum=4, maximum=9
    )
    assert padding == b"p" * 7


def test_psk_file_requires_length_and_restrictive_permissions(tmp_path):
    psk_file = tmp_path / "control.psk"
    psk_file.write_bytes(KEY)
    psk_file.chmod(0o600)
    assert load_psk(psk_file) == KEY

    psk_file.chmod(0o644)
    with pytest.raises(ValueError, match="permissions"):
        load_psk(psk_file)

    psk_file.chmod(0o600)
    psk_file.write_bytes(b"short")
    with pytest.raises(ValueError, match="between"):
        load_psk(psk_file)

    psk_file.write_bytes(b"x" * (MAX_PSK_SIZE + 1))
    with pytest.raises(ValueError, match="between"):
        load_psk(psk_file)

    with pytest.raises(ValueError, match="regular file|cannot read"):
        load_psk(tmp_path)


class FakeClock:
    def __init__(self, now=100.0):
        self.now = now

    def __call__(self):
        return self.now


class RecordingSocket:
    def __init__(self):
        self.sent = []

    def sendto(self, datagram, address):
        self.sent.append((bytes(datagram), address))
        return len(datagram)


def make_server(clock=None, **kwargs):
    server = MaskingTrafficServer(
        target_mbps=1,
        psk=KEY,
        clock=clock or FakeClock(),
        rng=FixedRng(0),
        byte_source=lambda size: b"n" * size,
        cookie_secret=b"z" * 32,
        **kwargs,
    )
    server.socket = RecordingSocket()
    return server


def complete_handshake(server, address, client_nonce=CLIENT_NONCE, hello_sequence=10):
    hello = encode_frame(
        MessageType.HELLO,
        client_nonce,
        bytes(NONCE_SIZE),
        hello_sequence,
        KEY,
    )
    assert server.handle_datagram(hello, address)
    challenge_datagram = server.socket.sent[-1][0]
    challenge = decode_frame(challenge_datagram, KEY)
    assert challenge.message_type is MessageType.CHALLENGE

    client_to_server_key = derive_session_key(
        KEY, client_nonce, challenge.session_nonce, CLIENT_TO_SERVER
    )
    server_to_client_key = derive_session_key(
        KEY, client_nonce, challenge.session_nonce, SERVER_TO_CLIENT
    )
    auth = encode_frame(
        MessageType.AUTH,
        client_nonce,
        challenge.session_nonce,
        hello_sequence + 1,
        KEY,
        payload=challenge.payload,
    )
    assert server.handle_datagram(auth, address)
    accept = decode_frame(server.socket.sent[-1][0], server_to_client_key)
    assert accept.message_type is MessageType.ACCEPT
    return (
        auth,
        client_to_server_key,
        server_to_client_key,
        challenge.session_nonce,
    )


def test_unknown_and_unauthenticated_datagrams_never_register_client():
    server = make_server()
    address = ("127.0.0.1", 20001)
    assert not server.handle_datagram(b"x", address)
    assert not server.handle_datagram(b"\x06", address)
    unauthenticated_keepalive = encode_frame(
        MessageType.KEEPALIVE,
        CLIENT_NONCE,
        SESSION_NONCE,
        1,
        KEY,
    )
    assert not server.handle_datagram(unauthenticated_keepalive, address)
    assert server.clients == {}
    assert server.socket.sent == []


def test_valid_handshake_keepalive_and_replay_protection():
    clock = FakeClock()
    server = make_server(clock=clock)
    address = ("127.0.0.1", 20002)
    auth, client_to_server_key, _, session_nonce = complete_handshake(
        server, address
    )
    assert address in server.clients
    assert not server.handle_datagram(auth, address)

    keepalive = encode_frame(
        MessageType.KEEPALIVE,
        CLIENT_NONCE,
        session_nonce,
        12,
        client_to_server_key,
    )
    clock.now += 5
    assert server.handle_datagram(keepalive, address)
    assert server.clients[address]["last_seen"] == clock.now
    assert not server.handle_datagram(keepalive, address)


def test_expired_auth_fails_and_prevalidation_is_non_amplifying():
    clock = FakeClock()
    server = make_server(clock=clock)
    address = ("127.0.0.1", 20003)
    hello = encode_frame(
        MessageType.HELLO, CLIENT_NONCE, bytes(NONCE_SIZE), 20, KEY
    )
    assert server.handle_datagram(hello, address)
    challenge = decode_frame(server.socket.sent[-1][0], KEY)
    received, replied = server.prevalidation_totals(address)
    assert replied <= received * 3

    clock.now += server.cookie_ttl + 1
    auth = encode_frame(
        MessageType.AUTH,
        CLIENT_NONCE,
        challenge.session_nonce,
        21,
        KEY,
        payload=challenge.payload,
    )
    assert not server.handle_datagram(auth, address)
    assert address not in server.clients
    received, replied = server.prevalidation_totals(address)
    assert replied <= received * 3


@pytest.mark.parametrize(
    ("max_clients", "max_total_mbps"),
    [(1, 100)],
)
def test_client_count_cap_refuses_new_enrollment(
    max_clients, max_total_mbps
):
    server = make_server(
        max_clients=max_clients, max_total_mbps=max_total_mbps
    )
    complete_handshake(server, ("127.0.0.1", 20004))

    second_nonce = b"d" * NONCE_SIZE
    hello = encode_frame(
        MessageType.HELLO, second_nonce, bytes(NONCE_SIZE), 30, KEY
    )
    second_address = ("127.0.0.1", 20005)
    assert server.handle_datagram(hello, second_address)
    challenge = decode_frame(server.socket.sent[-1][0], KEY)
    auth = encode_frame(
        MessageType.AUTH,
        second_nonce,
        challenge.session_nonce,
        31,
        KEY,
        payload=challenge.payload,
    )
    assert not server.handle_datagram(auth, second_address)
    assert len(server.clients) == 1


def test_total_rate_cap_allows_enrollment_for_fair_runtime_sharing():
    server = make_server(max_clients=2, max_total_mbps=1)
    complete_handshake(server, ("127.0.0.1", 20006))
    complete_handshake(
        server,
        ("127.0.0.1", 20007),
        client_nonce=b"d" * NONCE_SIZE,
        hello_sequence=30,
    )

    assert len(server.clients) == 2
    assert server.total_rate_limiter.rate_bytes_per_second == 125_000


def test_handshake_rate_and_pending_state_are_bounded():
    clock = FakeClock()
    server = make_server(
        clock=clock,
        max_clients=1,
        max_handshakes_per_second=1,
        cookie_ttl=1,
    )
    state_limit = server._handshake_state_limit
    for index in range(state_limit + 3):
        hello = encode_frame(
            MessageType.HELLO,
            index.to_bytes(NONCE_SIZE, "big"),
            bytes(NONCE_SIZE),
            index + 1,
            KEY,
        )
        server.handle_datagram(hello, ("127.0.0.1", 21000 + index))

    assert len(server._handshake_times) <= 1
    assert len(server._prevalidation) <= state_limit

    clock.now += 1.01
    next_hello = encode_frame(
        MessageType.HELLO,
        b"r" * NONCE_SIZE,
        bytes(NONCE_SIZE),
        100,
        KEY,
    )
    assert server.handle_datagram(next_hello, ("127.0.0.1", 22000))


def test_authenticated_framing_keeps_up_with_configured_rate():
    server = make_server()
    payload = b"d" * server.data_payload_ceiling
    iterations = 2_000

    started = time.perf_counter()
    for sequence in range(iterations):
        datagram = encode_frame(
            MessageType.DATA,
            CLIENT_NONCE,
            SESSION_NONCE,
            sequence,
            KEY,
            payload=payload,
        )
        decode_frame(datagram, KEY)
    elapsed = time.perf_counter() - started

    processed_mbps = iterations * server.mtu * 8 / elapsed / 1_000_000
    assert processed_mbps >= server.configured_max_mbps


def test_protocol_overhead_is_reserved_from_client_payload_mtu():
    client = AdaptiveTrafficClient("server.example", 8888, psk=KEY, mtu=1200)
    assert client.data_payload_ceiling == 1200 - FRAME_OVERHEAD
    assert MIN_CONTROL_MTU == FRAME_OVERHEAD + COOKIE_SIZE + 16


class ClientRng:
    def __init__(self, uniform_values=()):
        self.uniform_values = iter(uniform_values)

    def randint(self, minimum, maximum):
        return minimum

    def uniform(self, minimum, maximum):
        value = next(self.uniform_values)
        assert minimum <= value <= maximum
        return value


def make_client(rng=None):
    client = AdaptiveTrafficClient(
        "server.example",
        8888,
        psk=KEY,
        rng=rng or ClientRng(),
        byte_source=lambda size: b"c" * size,
    )
    client.socket = RecordingSocket()
    client.server_addr = ("192.0.2.10", 8888)
    client._reset_protocol_state()
    return client


def test_client_ignores_unexpected_source_and_only_accepts_authenticated_data():
    client = make_client()
    session_nonce = b"s" * NONCE_SIZE
    challenge = encode_frame(
        MessageType.CHALLENGE,
        client.client_nonce,
        session_nonce,
        client.handshake_sequence,
        KEY,
        payload=b"opaque-cookie",
    )
    assert client._process_datagram(challenge, ("192.0.2.11", 8888)) is None
    assert client.socket.sent == []

    assert client._process_datagram(challenge, client.server_addr) is None
    assert len(client.socket.sent) == 1
    client_to_server_key = derive_session_key(
        KEY, client.client_nonce, session_nonce, CLIENT_TO_SERVER
    )
    server_to_client_key = derive_session_key(
        KEY, client.client_nonce, session_nonce, SERVER_TO_CLIENT
    )
    accept = encode_frame(
        MessageType.ACCEPT,
        client.client_nonce,
        session_nonce,
        0,
        server_to_client_key,
    )
    assert client._process_datagram(accept, client.server_addr) is None
    assert client.handshake_accepted
    assert not client.connected

    reflected_client_data = encode_frame(
        MessageType.DATA,
        client.client_nonce,
        session_nonce,
        client.control_send_sequence + 1,
        client_to_server_key,
        payload=b"reflected-client-data",
    )
    assert client._process_datagram(
        reflected_client_data, client.server_addr
    ) is None

    data = encode_frame(
        MessageType.DATA,
        client.client_nonce,
        session_nonce,
        1,
        server_to_client_key,
        payload=b"cover-data",
    )
    assert client._process_datagram(data, client.server_addr) == b"cover-data"
    assert client._process_datagram(data, client.server_addr) is None

    forged = bytearray(data)
    forged[-1] ^= 1
    assert client._process_datagram(forged, client.server_addr) is None


def test_keepalive_jitter_stays_within_configured_bounds():
    client = make_client(rng=ClientRng(uniform_values=(-0.2, 0.2)))
    assert client._next_keepalive_delay() == pytest.approx(4.0)
    assert client._next_keepalive_delay() == pytest.approx(6.0)
