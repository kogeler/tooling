# Copyright © 2026 kogeler
# SPDX-License-Identifier: Apache-2.0

"""Authenticated control framing and return-routability cookies."""

import hashlib
import hmac
import os
import stat
import struct
from dataclasses import dataclass, field
from enum import IntEnum
from pathlib import Path

MAGIC = b"TMCP"
VERSION = 1
NONCE_SIZE = 16
TAG_SIZE = hashlib.sha256().digest_size
MAX_PADDING_SIZE = 64
CONTROL_PADDING_MAX = 16
MAX_DATAGRAM_SIZE = 65_507
MIN_PSK_SIZE = 32
MAX_PSK_SIZE = 4096
ZERO_NONCE = bytes(NONCE_SIZE)
INSECURE_DIAGNOSTIC_KEY = hashlib.sha256(
    b"traffic-masking/insecure-diagnostic/v1"
).digest()
CLIENT_TO_SERVER = b"client-to-server"
SERVER_TO_CLIENT = b"server-to-client"
_SESSION_DIRECTIONS = frozenset((CLIENT_TO_SERVER, SERVER_TO_CLIENT))

_HEADER = struct.Struct("!4sBB16s16sQHH")
_COOKIE_BODY = struct.Struct("!QQ")
HEADER_SIZE = _HEADER.size
FRAME_OVERHEAD = HEADER_SIZE + TAG_SIZE
MAX_PAYLOAD_SIZE = MAX_DATAGRAM_SIZE - FRAME_OVERHEAD
COOKIE_SIZE = _COOKIE_BODY.size + TAG_SIZE
MIN_CONTROL_MTU = FRAME_OVERHEAD + COOKIE_SIZE + CONTROL_PADDING_MAX


class ProtocolError(ValueError):
    """A datagram or protocol value is invalid."""


class MessageType(IntEnum):
    HELLO = 1
    CHALLENGE = 2
    AUTH = 3
    ACCEPT = 4
    KEEPALIVE = 5
    DATA = 6


@dataclass(frozen=True)
class Frame:
    message_type: MessageType
    client_nonce: bytes
    session_nonce: bytes
    sequence: int
    payload: bytes
    padding: bytes
    tag: bytes
    signed_data: bytes = field(repr=False)


@dataclass(frozen=True)
class Cookie:
    expires_at: int
    hello_sequence: int


def _validate_nonce(value, name):
    if not isinstance(value, (bytes, bytearray, memoryview)):
        raise ProtocolError(f"{name} must be bytes")
    value = bytes(value)
    if len(value) != NONCE_SIZE:
        raise ProtocolError(f"{name} must be exactly {NONCE_SIZE} bytes")
    return value


def _validate_key(key):
    if not isinstance(key, (bytes, bytearray, memoryview)):
        raise ProtocolError("authentication key must be bytes")
    key = bytes(key)
    if not key:
        raise ProtocolError("authentication key must not be empty")
    return key


def encode_frame(
    message_type,
    client_nonce,
    session_nonce,
    sequence,
    key,
    payload=b"",
    padding=b"",
):
    """Encode and authenticate one control/data frame."""
    try:
        message_type = MessageType(message_type)
    except ValueError:
        raise ProtocolError(f"unknown message type: {message_type}") from None
    client_nonce = _validate_nonce(client_nonce, "client nonce")
    session_nonce = _validate_nonce(session_nonce, "session nonce")
    key = _validate_key(key)
    if not isinstance(sequence, int) or not 0 <= sequence < 2**64:
        raise ProtocolError("sequence must be an unsigned 64-bit integer")
    payload = bytes(payload)
    padding = bytes(padding)
    if len(payload) > MAX_PAYLOAD_SIZE:
        raise ProtocolError("payload is too large")
    if len(padding) > MAX_PADDING_SIZE:
        raise ProtocolError("padding is too large")

    header = _HEADER.pack(
        MAGIC,
        VERSION,
        int(message_type),
        client_nonce,
        session_nonce,
        sequence,
        len(payload),
        len(padding),
    )
    signed_data = header + payload + padding
    if len(signed_data) + TAG_SIZE > MAX_DATAGRAM_SIZE:
        raise ProtocolError("encoded datagram is too large")
    return signed_data + hmac.new(key, signed_data, hashlib.sha256).digest()


def inspect_frame(datagram):
    """Parse a frame structurally without treating it as authenticated."""
    datagram = bytes(datagram)
    if len(datagram) < FRAME_OVERHEAD:
        raise ProtocolError("truncated frame")

    (
        magic,
        version,
        raw_type,
        client_nonce,
        session_nonce,
        sequence,
        payload_length,
        padding_length,
    ) = _HEADER.unpack_from(datagram)
    if magic != MAGIC:
        raise ProtocolError("invalid frame magic")
    if version != VERSION:
        raise ProtocolError(f"unsupported protocol version: {version}")
    try:
        message_type = MessageType(raw_type)
    except ValueError:
        raise ProtocolError(f"unknown message type: {raw_type}") from None
    if payload_length > MAX_PAYLOAD_SIZE:
        raise ProtocolError("declared payload is too large")
    if padding_length > MAX_PADDING_SIZE:
        raise ProtocolError("declared padding is too large")

    expected_length = HEADER_SIZE + payload_length + padding_length + TAG_SIZE
    if len(datagram) != expected_length:
        raise ProtocolError("frame length does not match declared lengths")
    payload_start = HEADER_SIZE
    padding_start = payload_start + payload_length
    tag_start = padding_start + padding_length
    return Frame(
        message_type=message_type,
        client_nonce=client_nonce,
        session_nonce=session_nonce,
        sequence=sequence,
        payload=datagram[payload_start:padding_start],
        padding=datagram[padding_start:tag_start],
        tag=datagram[tag_start:],
        signed_data=datagram[:tag_start],
    )


def decode_frame(datagram, key):
    """Parse a frame and verify its authentication tag."""
    frame = inspect_frame(datagram)
    expected_tag = hmac.new(
        _validate_key(key), frame.signed_data, hashlib.sha256
    ).digest()
    if not hmac.compare_digest(frame.tag, expected_tag):
        raise ProtocolError("invalid authentication tag")
    return frame


def derive_session_key(base_key, client_nonce, session_nonce, direction):
    """Derive a direction-specific key bound to one negotiated session."""
    client_nonce = _validate_nonce(client_nonce, "client nonce")
    session_nonce = _validate_nonce(session_nonce, "session nonce")
    if session_nonce == ZERO_NONCE:
        raise ProtocolError("session nonce must not be zero")
    try:
        direction = bytes(direction)
    except (TypeError, ValueError):
        raise ProtocolError("invalid session key direction") from None
    if direction not in _SESSION_DIRECTIONS:
        raise ProtocolError("invalid session key direction")
    material = (
        b"traffic-masking/session/v1"
        + client_nonce
        + session_nonce
        + direction
    )
    return hmac.new(_validate_key(base_key), material, hashlib.sha256).digest()


def _cookie_material(address, client_nonce, session_nonce, body):
    host = str(address[0]).encode("utf-8")
    port = int(address[1])
    if len(host) > 65_535 or not 0 <= port <= 65_535:
        raise ProtocolError("invalid source address")
    return (
        b"traffic-masking/cookie/v1"
        + struct.pack("!H", len(host))
        + host
        + struct.pack("!H", port)
        + client_nonce
        + session_nonce
        + body
    )


def create_cookie(
    secret,
    address,
    client_nonce,
    session_nonce,
    hello_sequence,
    expires_at,
):
    """Create an opaque cookie bound to a source address and handshake values."""
    secret = _validate_key(secret)
    client_nonce = _validate_nonce(client_nonce, "client nonce")
    session_nonce = _validate_nonce(session_nonce, "session nonce")
    if not 0 <= int(hello_sequence) < 2**64:
        raise ProtocolError("hello sequence is out of range")
    if not 0 <= int(expires_at) < 2**64:
        raise ProtocolError("cookie expiry is out of range")
    body = _COOKIE_BODY.pack(int(expires_at), int(hello_sequence))
    tag = hmac.new(
        secret,
        _cookie_material(
            address, client_nonce, session_nonce, body
        ),
        hashlib.sha256,
    ).digest()
    return body + tag


def verify_cookie(
    cookie,
    secret,
    address,
    client_nonce,
    session_nonce,
    now,
    max_future_seconds,
):
    """Verify a cookie and return its decoded expiry/HELLO sequence."""
    cookie = bytes(cookie)
    if len(cookie) != COOKIE_SIZE:
        raise ProtocolError("invalid cookie length")
    body = cookie[: _COOKIE_BODY.size]
    supplied_tag = cookie[_COOKIE_BODY.size :]
    expected_tag = hmac.new(
        _validate_key(secret),
        _cookie_material(
            address,
            _validate_nonce(client_nonce, "client nonce"),
            _validate_nonce(session_nonce, "session nonce"),
            body,
        ),
        hashlib.sha256,
    ).digest()
    if not hmac.compare_digest(supplied_tag, expected_tag):
        raise ProtocolError("invalid cookie authentication tag")

    expires_at, hello_sequence = _COOKIE_BODY.unpack(body)
    now = int(now)
    if expires_at < now:
        raise ProtocolError("cookie has expired")
    if expires_at > now + int(max_future_seconds):
        raise ProtocolError("cookie expiry is outside the allowed window")
    return Cookie(expires_at=expires_at, hello_sequence=hello_sequence)


def make_padding(rng, byte_source=os.urandom, minimum=0, maximum=16):
    """Return authenticated variable-length padding within configured bounds."""
    if not 0 <= minimum <= maximum <= MAX_PADDING_SIZE:
        raise ValueError("invalid control padding bounds")
    size = rng.randint(minimum, maximum)
    padding = bytes(byte_source(size))
    if len(padding) != size:
        raise ValueError("byte source returned the wrong padding length")
    return padding


def load_psk(path):
    """Read a bounded PSK file and reject permissions exposed to other users."""
    if path is None:
        raise ValueError("--psk-file is required unless --insecure-diagnostic is set")
    path = Path(path)
    try:
        with path.open("rb") as handle:
            metadata = os.fstat(handle.fileno())
            if not stat.S_ISREG(metadata.st_mode):
                raise ValueError(f"PSK path is not a regular file: {path}")
            if os.name == "posix" and stat.S_IMODE(metadata.st_mode) & 0o077:
                raise ValueError(
                    "PSK file permissions must not grant group/other access"
                )
            secret = handle.read(MAX_PSK_SIZE + 1)
    except ValueError:
        raise
    except OSError as exc:
        raise ValueError(f"cannot read PSK file {path}: {exc}") from None
    if not MIN_PSK_SIZE <= len(secret) <= MAX_PSK_SIZE:
        raise ValueError(
            f"PSK must contain between {MIN_PSK_SIZE} and {MAX_PSK_SIZE} bytes"
        )
    return secret
