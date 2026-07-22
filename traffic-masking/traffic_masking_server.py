#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# Copyright © 2025 kogeler
# SPDX-License-Identifier: Apache-2.0

"""Authenticated UDP cover-traffic server."""

import argparse
import hashlib
import json
import math
import os
import random
import signal
import socket
import struct
import threading
import time
from collections import OrderedDict, deque
from dataclasses import dataclass

from control_protocol import (
    CLIENT_TO_SERVER,
    CONTROL_PADDING_MAX,
    FRAME_OVERHEAD,
    INSECURE_DIAGNOSTIC_KEY,
    MAX_DATAGRAM_SIZE,
    MAX_PSK_SIZE,
    MIN_CONTROL_MTU,
    MIN_PSK_SIZE,
    NONCE_SIZE,
    ZERO_NONCE,
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
from masking_lib import (
    FloatingRate,
    Packetizer,
    PayloadPadder,
    RateLimiter,
    ShapeEvent,
    TrafficProfile,
    generate_payload,
    mbps_to_bytes_per_second,
    profile_event_generator,
)


def _positive_finite_float(value, name):
    try:
        value = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a number") from None
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be a positive finite number")
    return value


def _positive_int(value, name):
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a positive integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError(f"{name} must be a positive integer") from None
    if not isinstance(value, str) and value != parsed:
        raise ValueError(f"{name} must be a positive integer")
    if parsed <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return parsed


def _env_default(name, fallback):
    return os.environ.get(name, fallback)


@dataclass(frozen=True, slots=True)
class ServerClientSnapshot:
    address: tuple[str, int]
    last_seen: float
    bytes_received: int
    packets_received: int
    bytes_sent: int
    packets_sent: int
    current_rate_mbps: float | None


@dataclass(frozen=True, slots=True)
class ServerSnapshot:
    timestamp: float
    bytes_sent: int
    packets_sent: int
    clients: tuple[ServerClientSnapshot, ...]

    @property
    def client_count(self):
        return len(self.clients)


class PacketGenerator:
    """Generate opaque rate-mode payloads with variable packet sizes."""

    def __init__(self, min_size=28, max_size=1400, rng=None, byte_source=None):
        self.min_size = min_size
        self.max_size = max_size
        self.sequence = 0
        self._rng = rng or random.Random()
        self._byte_source = byte_source or os.urandom

    def generate_packet(self, target_size=None):
        """Generate a data packet"""
        if target_size is None:
            # Keep rate-mode datagram sizes variable within the configured bounds.
            weights = [0.1, 0.15, 0.5, 0.15, 0.1]  # Favor medium packets
            sizes = [
                self._rng.randint(self.min_size, 200),  # Small
                self._rng.randint(200, 500),  # Small-medium
                self._rng.randint(500, 1000),  # Medium
                self._rng.randint(1000, 1300),  # Medium-large
                self._rng.randint(1300, self.max_size),  # Large
            ]
            size = self._rng.choices(sizes, weights=weights)[0]
        else:
            size = min(max(target_size, self.min_size), self.max_size)

        # Packet layout: [sequence(4)] [timestamp(8)] [checksum(16)] [random_data]
        self.sequence += 1
        timestamp = struct.pack("!Q", int(time.time() * 1000000))  # microseconds
        seq_bytes = struct.pack("!I", self.sequence)

        # Random payload from a bulk CSPRNG source. Never reseed the global RNG
        # in the hot path: it made same-size payloads identical within a window
        # and corrupted the shared random stream used by other threads.
        data_size = max(0, size - 28)  # 4 + 8 + 16 = 28 bytes header
        random_data = self._byte_source(data_size)

        # Calculate checksum
        packet_content = seq_bytes + timestamp + random_data
        checksum = hashlib.md5(packet_content).digest()

        return seq_bytes + timestamp + checksum + random_data


class MaskingTrafficServer:
    """Main server for generating cover traffic"""

    def __init__(
        self,
        host="0.0.0.0",
        port=8888,
        target_mbps=None,
        min_mbps=None,
        max_mbps=None,
        profile=None,
        shape_mode="rate",
        padding="none",
        mtu=1200,
        stats_interval=5.0,
        stats_json=False,
        psk=None,
        insecure_diagnostic=False,
        max_clients=16,
        max_total_mbps=100.0,
        max_handshakes_per_second=20,
        cookie_ttl=10,
        clock=None,
        rng=None,
        byte_source=None,
        cookie_secret=None,
        monotonic_clock=None,
        sleep=None,
    ):
        # Validate the offered-load contract before constructing generators.
        if shape_mode not in ("rate", "profile"):
            raise ValueError("shape-mode must be 'rate' or 'profile'")
        floating = False
        if shape_mode == "rate":
            if profile is not None:
                raise ValueError("profile is not valid in rate shape mode")
            if (min_mbps is None) != (max_mbps is None):
                raise ValueError("min-mbps and max-mbps must be given together")
            floating = min_mbps is not None
            if floating:
                min_mbps = _positive_finite_float(min_mbps, "min-mbps")
                max_mbps = _positive_finite_float(max_mbps, "max-mbps")
                if min_mbps >= max_mbps:
                    raise ValueError("min-mbps must be less than max-mbps")
                target_mbps = None
            else:
                target_mbps = _positive_finite_float(
                    5 if target_mbps is None else target_mbps,
                    "target rate (--mbps)",
                )
        else:
            if profile is None:
                raise ValueError("profile shape mode requires --profile")
            if min_mbps is not None:
                raise ValueError("min-mbps is not valid in profile shape mode")
            if target_mbps is not None:
                raise ValueError("mbps is not valid in profile shape mode")
            if max_mbps is not None:
                max_mbps = _positive_finite_float(max_mbps, "max-mbps")
        if padding not in PayloadPadder.STRATEGIES:
            raise ValueError(f"unknown padding strategy: {padding}")
        if shape_mode == "rate" and padding != "none":
            raise ValueError("padding is only valid in profile shape mode")
        mtu = _positive_int(mtu, "mtu")
        if mtu > MAX_DATAGRAM_SIZE:
            raise ValueError(f"mtu must not exceed {MAX_DATAGRAM_SIZE}")
        if mtu < MIN_CONTROL_MTU:
            raise ValueError(
                f"mtu must be at least {MIN_CONTROL_MTU} bytes "
                "for authenticated control framing"
            )
        stats_interval = _positive_finite_float(
            stats_interval, "stats-interval"
        )
        max_clients = _positive_int(max_clients, "max-clients")
        max_total_mbps = _positive_finite_float(
            max_total_mbps, "max-total-mbps"
        )
        max_handshakes_per_second = _positive_int(
            max_handshakes_per_second, "max-handshakes-per-second"
        )
        cookie_ttl = _positive_int(cookie_ttl, "cookie-ttl")
        configured_max_mbps = (
            max_mbps
            if max_mbps is not None
            else (target_mbps if shape_mode == "rate" else max_total_mbps)
        )
        if configured_max_mbps > max_total_mbps:
            raise ValueError(
                "max-total-mbps must be at least the configured per-client maximum"
            )
        if psk is not None and insecure_diagnostic:
            raise ValueError("psk and insecure diagnostic mode are mutually exclusive")
        if psk is None and not insecure_diagnostic:
            raise ValueError(
                "a PSK is required unless insecure diagnostic mode is explicit"
            )
        if psk is not None:
            if not isinstance(psk, (bytes, bytearray, memoryview)):
                raise ValueError("psk must be bytes")
            psk = bytes(psk)
            if not MIN_PSK_SIZE <= len(psk) <= MAX_PSK_SIZE:
                raise ValueError(
                    f"psk must contain between {MIN_PSK_SIZE} and "
                    f"{MAX_PSK_SIZE} bytes"
                )

        self._clock = clock or time.time
        self._monotonic_clock = monotonic_clock or time.monotonic
        self._sleep = sleep or time.sleep
        self._sleep_is_injected = sleep is not None
        self._rng = rng or random.Random()
        self._byte_source = byte_source or os.urandom
        self.base_key = psk if psk is not None else INSECURE_DIAGNOSTIC_KEY
        self.insecure_diagnostic = bool(insecure_diagnostic)
        if cookie_secret is None:
            self.cookie_secret = bytes(self._byte_source(32))
        elif isinstance(cookie_secret, (bytes, bytearray, memoryview)):
            self.cookie_secret = bytes(cookie_secret)
        else:
            raise ValueError("cookie secret must be bytes")
        if len(self.cookie_secret) < 32:
            raise ValueError("cookie secret must contain at least 32 bytes")

        self.host = host
        self.port = port
        self.target_mbps = target_mbps
        self.min_mbps = min_mbps
        self.max_mbps = max_mbps
        self.shape_mode = shape_mode
        self.current_rate_mbps = (
            (min_mbps + max_mbps) / 2
            if floating
            else target_mbps
        )
        self.target_bytes_per_second = (
            mbps_to_bytes_per_second(self.current_rate_mbps)
            if self.current_rate_mbps is not None
            else None
        )
        self.socket = None
        self.clients = {}  # Only authenticated/validated sessions.
        self._clients_lock = threading.RLock()
        self._handshake_lock = threading.RLock()
        self._stats_lock = threading.Lock()
        self._socket_lock = threading.Lock()
        self._lifecycle_lock = threading.RLock()
        self._stop_event = threading.Event()
        self._threads = []
        self.packetizer = Packetizer(mtu, FRAME_OVERHEAD)
        self.data_payload_ceiling = self.packetizer.payload_ceiling
        self.stats = {
            "bytes_sent": 0,
            "packets_sent": 0,
        }
        self.stats_interval = stats_interval
        self.stats_json = bool(stats_json)
        if shape_mode == "profile":
            try:
                self.profile = (
                    TrafficProfile(profile) if isinstance(profile, str) else profile
                )
            except (TypeError, ValueError):
                raise ValueError(f"unknown traffic profile: {profile}") from None
        else:
            self.profile = None
        self.padding_strategy = padding
        self.mtu = mtu
        self.max_clients = max_clients
        self.max_total_mbps = max_total_mbps
        self.max_handshakes_per_second = max_handshakes_per_second
        self.cookie_ttl = cookie_ttl
        self.configured_max_mbps = configured_max_mbps
        self._handshake_times = deque()
        self._prevalidation = OrderedDict()
        self._accepted_auth = OrderedDict()
        self._handshake_state_limit = (
            max_clients + max_handshakes_per_second * cookie_ttl
        )
        self.total_rate_limiter = RateLimiter(
            mbps_to_bytes_per_second(max_total_mbps),
            burst_bytes=self.mtu,
            clock=self._monotonic_clock,
        )

    def start(self):
        """Bind the socket and start managed worker threads."""
        with self._lifecycle_lock:
            if any(thread.is_alive() for thread in self._threads):
                raise RuntimeError("server is already running")
            self._stop_event.clear()
            server_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                server_socket.setsockopt(
                    socket.SOL_SOCKET, socket.SO_SNDBUF, 4 * 1024 * 1024
                )
                server_socket.setsockopt(
                    socket.SOL_SOCKET, socket.SO_RCVBUF, 4 * 1024 * 1024
                )
                server_socket.settimeout(0.5)
                server_socket.bind((self.host, self.port))
            except Exception:
                server_socket.close()
                raise
            with self._socket_lock:
                self.socket = server_socket
            workers = (
                ("traffic-masking-server-receive", self.receive_loop),
                ("traffic-masking-server-send", self.send_loop),
                ("traffic-masking-server-stats", self.stats_loop),
                ("traffic-masking-server-cleanup", self.cleanup_loop),
            )
            self._threads = [
                threading.Thread(name=name, target=target, daemon=False)
                for name, target in workers
            ]
            for thread in self._threads:
                thread.start()

        print(
            f"[*] Traffic masking server started on {self.host}:{self.port}", flush=True
        )
        if self.shape_mode == "profile":
            print(
                f"[*] Experimental profile shaping: {self.profile.value}"
                + (
                    f" (cap {self.max_mbps} Mbps)"
                    if self.max_mbps is not None
                    else " (native offered load)"
                ),
                flush=True,
            )
        elif self.min_mbps is not None and self.max_mbps is not None:
            print(
                f"[*] Floating throughput: {self.min_mbps}-{self.max_mbps} Mbps",
                flush=True,
            )
        else:
            print(f"[*] Target throughput: {self.target_mbps} Mbps", flush=True)
        if self.shape_mode == "profile":
            print(
                f"[*] Profile padding: {self.padding_strategy} | mtu={self.mtu}",
                flush=True,
            )
        auth_mode = "INSECURE DIAGNOSTIC" if self.insecure_diagnostic else "PSK"
        print(
            f"[*] Control authentication: {auth_mode} | max clients: "
            f"{self.max_clients} | total cap: {self.max_total_mbps} Mbps",
            flush=True,
        )

    @property
    def worker_threads(self):
        return tuple(self._threads)

    def _current_socket(self):
        with self._socket_lock:
            return self.socket

    def _close_socket(self):
        with self._socket_lock:
            server_socket = self.socket
            self.socket = None
        if server_socket is not None:
            try:
                server_socket.close()
            except OSError:
                pass

    def _wait_for_pacing(self, delay):
        if self._sleep_is_injected:
            self._sleep(delay)
            return self._stop_event.is_set()
        return self._stop_event.wait(delay)

    def _prune_handshake_state(self, now):
        with self._handshake_lock:
            while self._handshake_times and self._handshake_times[0] <= now - 1.0:
                self._handshake_times.popleft()
            for mapping in (self._prevalidation, self._accepted_auth):
                expired = [
                    key for key, value in mapping.items() if value["expires"] < now
                ]
                for key in expired:
                    del mapping[key]

    def _consume_handshake_slot(self, now):
        with self._handshake_lock:
            self._prune_handshake_state(now)
            if len(self._handshake_times) >= self.max_handshakes_per_second:
                return False
            self._handshake_times.append(now)
            return True

    def _record_prevalidation_input(self, addr, byte_count, now):
        with self._handshake_lock:
            self._prune_handshake_state(now)
            entry = self._prevalidation.get(addr)
            if entry is None:
                if len(self._prevalidation) >= self._handshake_state_limit:
                    return None
                entry = {
                    "received": 0,
                    "replied": 0,
                    "expires": now + self.cookie_ttl,
                }
                self._prevalidation[addr] = entry
            entry["received"] += byte_count
            entry["expires"] = now + self.cookie_ttl
            self._prevalidation.move_to_end(addr)
            return entry

    def _send_prevalidation(self, addr, datagram, entry):
        with self._handshake_lock:
            if entry["replied"] + len(datagram) > entry["received"] * 3:
                return False
            server_socket = self._current_socket()
            if server_socket is None:
                return False
            try:
                sent = server_socket.sendto(datagram, addr)
            except OSError:
                return False
            if sent != len(datagram):
                return False
            entry["replied"] += sent
            return True

    def prevalidation_totals(self, addr):
        """Return received/replied pre-validation bytes for tests/diagnostics."""
        with self._handshake_lock:
            entry = self._prevalidation.get(addr)
            if entry is None:
                return 0, 0
            return entry["received"], entry["replied"]

    def _add_client(self, addr, client):
        with self._clients_lock:
            self.clients[addr] = client

    def _get_client(self, addr):
        with self._clients_lock:
            return self.clients.get(addr)

    def _client_items(self):
        with self._clients_lock:
            return tuple(self.clients.items())

    def _remove_client(self, addr, expected=None):
        with self._clients_lock:
            client = self.clients.get(addr)
            if client is None or (expected is not None and client is not expected):
                return None
            return self.clients.pop(addr)

    def _record_client_receive(self, addr, now, byte_count=0):
        with self._clients_lock:
            client = self.clients.get(addr)
            if client is None:
                return False
            with client["lock"]:
                client["last_seen"] = now
                if byte_count:
                    client["bytes_received"] += byte_count
                    client["packets_received"] += 1
            return True

    def _control_padding(self):
        return make_padding(
            self._rng, self._byte_source, 0, CONTROL_PADDING_MAX
        )

    def _random_nonce(self):
        nonce = bytes(self._byte_source(NONCE_SIZE))
        if len(nonce) != NONCE_SIZE:
            raise ValueError("byte source returned the wrong nonce length")
        return nonce if nonce != ZERO_NONCE else b"\x01" + nonce[1:]

    def _handle_hello(self, frame, addr, entry, now):
        if (
            frame.client_nonce == ZERO_NONCE
            or frame.session_nonce != ZERO_NONCE
            or frame.payload
            or frame.sequence == 2**64 - 1
        ):
            return False
        session_nonce = self._random_nonce()
        expires_at = int(now) + self.cookie_ttl
        cookie = create_cookie(
            self.cookie_secret,
            addr,
            frame.client_nonce,
            session_nonce,
            frame.sequence,
            expires_at,
        )
        challenge = encode_frame(
            MessageType.CHALLENGE,
            frame.client_nonce,
            session_nonce,
            frame.sequence,
            self.base_key,
            payload=cookie,
            padding=self._control_padding(),
        )
        return self._send_prevalidation(addr, challenge, entry)

    def _handle_auth(self, frame, addr, entry, now):
        if frame.client_nonce == ZERO_NONCE or frame.session_nonce == ZERO_NONCE:
            return False
        try:
            cookie = verify_cookie(
                frame.payload,
                self.cookie_secret,
                addr,
                frame.client_nonce,
                frame.session_nonce,
                now=int(now),
                max_future_seconds=self.cookie_ttl,
            )
        except ProtocolError:
            return False
        if frame.sequence != cookie.hello_sequence + 1:
            return False

        replay_key = frame.client_nonce + frame.session_nonce
        receive_key = derive_session_key(
            self.base_key,
            frame.client_nonce,
            frame.session_nonce,
            CLIENT_TO_SERVER,
        )
        send_key = derive_session_key(
            self.base_key,
            frame.client_nonce,
            frame.session_nonce,
            SERVER_TO_CLIENT,
        )
        accept = encode_frame(
            MessageType.ACCEPT,
            frame.client_nonce,
            frame.session_nonce,
            0,
            send_key,
            padding=self._control_padding(),
        )
        client = self._new_client_state(
            frame,
            now,
            receive_key,
            send_key,
        )
        with self._handshake_lock, self._clients_lock:
            self._prune_handshake_state(now)
            if replay_key in self._accepted_auth:
                return False
            prospective_clients = (
                len(self.clients) - (1 if addr in self.clients else 0) + 1
            )
            if prospective_clients > self.max_clients:
                return False
            if len(self._accepted_auth) >= self._handshake_state_limit:
                return False
            if not self._send_prevalidation(addr, accept, entry):
                return False
            self._accepted_auth[replay_key] = {
                "expires": now + self.cookie_ttl
            }
            self._add_client(addr, client)
        print(f"[+] New client connected: {addr}", flush=True)
        return True

    def _new_client_state(self, frame, now, receive_key, send_key):
        seed_material = hashlib.sha256(
            self.cookie_secret + frame.client_nonce + frame.session_nonce
        ).digest()
        client_rng = random.Random(int.from_bytes(seed_material, "big"))
        floating_rate = None
        current_rate_mbps = self.target_mbps
        if self.shape_mode == "rate" and self.min_mbps is not None:
            floating_rate = FloatingRate(
                self.min_mbps,
                self.max_mbps,
                clock=self._monotonic_clock,
                rng=client_rng,
            )
            current_rate_mbps = floating_rate.value_mbps
        limiter_mbps = (
            current_rate_mbps if self.shape_mode == "rate" else self.max_mbps
        )
        padder = None
        generator = None
        if self.shape_mode == "profile":
            padder = PayloadPadder(
                strategy=self.padding_strategy,
                ceiling=self.data_payload_ceiling,
                rng=client_rng,
                byte_source=self._byte_source,
            )
            generator = profile_event_generator(self.profile, rng=client_rng)

        return {
            "lock": threading.RLock(),
            "last_seen": now,
            "bytes_received": 0,
            "packets_received": 0,
            "bytes_sent": 0,
            "packets_sent": 0,
            "client_nonce": frame.client_nonce,
            "session_nonce": frame.session_nonce,
            "receive_key": receive_key,
            "send_key": send_key,
            "receive_sequence": frame.sequence,
            "send_sequence": 0,
            "rate_limiter": (
                RateLimiter(
                    mbps_to_bytes_per_second(limiter_mbps),
                    burst_bytes=self.mtu,
                    clock=self._monotonic_clock,
                )
                if limiter_mbps is not None
                else None
            ),
            "rng": client_rng,
            "packet_gen": PacketGenerator(
                max_size=min(1400, self.data_payload_ceiling),
                rng=client_rng,
                byte_source=self._byte_source,
            ),
            "floating_rate": floating_rate,
            "current_rate_mbps": current_rate_mbps,
            "generator": generator,
            "padder": padder,
            "pending_fragments": deque(),
            "next_event_at": self._monotonic_clock(),
            "pending_event_delay": 0.0,
            "delay_after_send": None,
        }

    def _handle_session_frame(self, inspected, datagram, addr, now):
        with self._clients_lock:
            client = self.clients.get(addr)
            if client is None:
                return False
            with client["lock"]:
                if inspected.message_type not in (
                    MessageType.KEEPALIVE,
                    MessageType.DATA,
                ):
                    return False
                if (
                    inspected.client_nonce != client["client_nonce"]
                    or inspected.session_nonce != client["session_nonce"]
                ):
                    return False
                try:
                    frame = decode_frame(datagram, client["receive_key"])
                except ProtocolError:
                    return False
                if frame.sequence <= client["receive_sequence"]:
                    return False

                client["receive_sequence"] = frame.sequence
                byte_count = len(datagram) if frame.message_type is MessageType.DATA else 0
                return self._record_client_receive(addr, now, byte_count)

    def handle_datagram(self, datagram, addr):
        """Validate and dispatch one UDP datagram; return whether it was accepted."""
        try:
            inspected = inspect_frame(datagram)
        except ProtocolError:
            return False
        now = self._clock()
        if inspected.message_type in (MessageType.KEEPALIVE, MessageType.DATA):
            return self._handle_session_frame(inspected, datagram, addr, now)
        if inspected.message_type not in (MessageType.HELLO, MessageType.AUTH):
            return False

        with self._handshake_lock:
            entry = self._record_prevalidation_input(addr, len(datagram), now)
            if entry is None or not self._consume_handshake_slot(now):
                return False
            try:
                frame = decode_frame(datagram, self.base_key)
            except ProtocolError:
                return False
            if frame.message_type is MessageType.HELLO:
                return self._handle_hello(frame, addr, entry, now)
            return self._handle_auth(frame, addr, entry, now)

    def receive_loop(self):
        """Receive and authenticate packets from clients."""
        while not self._stop_event.is_set():
            server_socket = self._current_socket()
            if server_socket is None:
                break
            try:
                data, addr = server_socket.recvfrom(MAX_DATAGRAM_SIZE)
                self.handle_datagram(data, addr)
            except socket.timeout:
                continue
            except OSError as exc:
                if self._stop_event.is_set() or server_socket is not self._current_socket():
                    continue
                print(f"[!] Receive error: {exc}", flush=True)
            except Exception as exc:
                if not self._stop_event.is_set():
                    print(f"[!] Receive error: {exc}", flush=True)

    def _frame_data_for_client(self, client, payload):
        with client["lock"]:
            client["send_sequence"] += 1
            return encode_frame(
                MessageType.DATA,
                client["client_nonce"],
                client["session_nonce"],
                client["send_sequence"],
                client["send_key"],
                payload=payload,
            )

    def send_loop(self):
        """Serve one datagram per client per round under the aggregate cap."""
        while not self._stop_event.is_set():
            clients = self._client_items()
            if not clients:
                self._stop_event.wait(0.1)
                continue

            sent_any = False
            next_ready_at = None
            for addr, client in clients:
                fragment = self._next_client_fragment(client)
                if fragment is not None:
                    self._send_fragment(addr, client, fragment)
                    self._complete_client_fragment(client)
                    sent_any = True
                elif client["next_event_at"] > self._monotonic_clock():
                    next_ready_at = min(
                        client["next_event_at"],
                        next_ready_at or client["next_event_at"],
                    )
            if not sent_any:
                delay = 0.01
                if next_ready_at is not None:
                    delay = min(
                        delay,
                        max(0.0, next_ready_at - self._monotonic_clock()),
                    )
                self._stop_event.wait(delay)

    def _next_client_fragment(self, client):
        with client["lock"]:
            if client["pending_fragments"]:
                fragment = client["pending_fragments"].popleft()
                if not client["pending_fragments"]:
                    client["delay_after_send"] = client["pending_event_delay"]
                return fragment
            now = self._monotonic_clock()
            if now < client["next_event_at"]:
                return None

            event = self._next_shape_event(client)
            if not event.byte_count:
                client["next_event_at"] = now + event.delay
                return None
            payload = self._make_event_payload(client, event)
            client["pending_fragments"].extend(self.packetizer.packetize(payload))
            if not client["pending_fragments"]:
                return None
            client["pending_event_delay"] = event.delay
            fragment = client["pending_fragments"].popleft()
            if not client["pending_fragments"]:
                client["delay_after_send"] = event.delay
            return fragment

    def _complete_client_fragment(self, client):
        with client["lock"]:
            if client["delay_after_send"] is not None:
                client["next_event_at"] = (
                    self._monotonic_clock() + client["delay_after_send"]
                )
                client["delay_after_send"] = None

    def _next_shape_event(self, client):
        if self.shape_mode == "profile":
            return next(client["generator"])

        if client["floating_rate"] is not None:
            current_rate = client["floating_rate"].update()
            client["current_rate_mbps"] = current_rate
            client["rate_limiter"].set_rate(
                mbps_to_bytes_per_second(current_rate)
            )
        return ShapeEvent(byte_count=self.data_payload_ceiling)

    def _make_event_payload(self, client, event):
        if self.shape_mode == "rate":
            return client["packet_gen"].generate_packet(event.byte_count)
        payload = generate_payload(
            event.byte_count,
            byte_source=self._byte_source,
        )
        return client["padder"].transform(payload)

    def _send_fragment(self, addr, client, fragment):
        framed = self._frame_data_for_client(client, fragment)
        limiter = client["rate_limiter"]
        client_reservation = limiter.reserve(len(framed)) if limiter else None
        total_reservation = self.total_rate_limiter.reserve(len(framed))
        delay = max(
            client_reservation.delay if client_reservation else 0.0,
            total_reservation.delay,
        )
        sent = 0
        try:
            if delay and self._wait_for_pacing(delay):
                return 0
            server_socket = self._current_socket()
            if server_socket is None:
                return 0
            sent = server_socket.sendto(framed, addr)
            if sent == len(framed):
                with self._stats_lock:
                    self.stats["bytes_sent"] += sent
                    self.stats["packets_sent"] += 1
                    with client["lock"]:
                        client["bytes_sent"] += sent
                        client["packets_sent"] += 1
            else:
                sent = max(0, min(sent, len(framed)))
        except OSError as exc:
            if not self._stop_event.is_set():
                print(f"[!] Send error to client {addr}: {exc}", flush=True)
        finally:
            if client_reservation:
                limiter.commit(client_reservation, successful_bytes=sent)
            self.total_rate_limiter.commit(
                total_reservation, successful_bytes=sent
            )
        return sent

    def _remove_inactive_clients(self, now, idle_seconds=30.0):
        removed = []
        with self._clients_lock:
            for addr, client in tuple(self.clients.items()):
                with client["lock"]:
                    inactive = now - client["last_seen"] > idle_seconds
                if inactive and self._remove_client(addr, expected=client) is not None:
                    removed.append(addr)
        return tuple(removed)

    def cleanup_loop(self):
        """Remove inactive clients until shutdown is requested."""
        while not self._stop_event.wait(5.0):
            for addr in self._remove_inactive_clients(self._clock()):
                print(f"[-] Client removed (inactive): {addr}", flush=True)

    def snapshot(self, now=None):
        """Return an immutable, internally consistent runtime snapshot."""
        clients = []
        with self._stats_lock, self._clients_lock:
            timestamp = self._monotonic_clock() if now is None else float(now)
            bytes_sent = self.stats["bytes_sent"]
            packets_sent = self.stats["packets_sent"]
            for addr, client in self.clients.items():
                with client["lock"]:
                    clients.append(
                        ServerClientSnapshot(
                            address=addr,
                            last_seen=client["last_seen"],
                            bytes_received=client["bytes_received"],
                            packets_received=client["packets_received"],
                            bytes_sent=client["bytes_sent"],
                            packets_sent=client["packets_sent"],
                            current_rate_mbps=client["current_rate_mbps"],
                        )
                    )
        return ServerSnapshot(
            timestamp=timestamp,
            bytes_sent=bytes_sent,
            packets_sent=packets_sent,
            clients=tuple(sorted(clients, key=lambda item: item.address)),
        )

    def stats_loop(self):
        """Print total and per-client application-datagram egress rates."""
        previous = self.snapshot()
        while not self._stop_event.wait(self.stats_interval):
            current = self.snapshot()
            time_delta = current.timestamp - previous.timestamp
            if time_delta <= 0:
                previous = current
                continue
            bytes_delta = current.bytes_sent - previous.bytes_sent
            packets_delta = current.packets_sent - previous.packets_sent
            mbps = bytes_delta * 8 / (time_delta * 1_000_000)
            pps = packets_delta / time_delta
            pattern_desc = (
                "rate:per-client"
                if self.shape_mode == "rate" and self.min_mbps is None
                else f"floating:{self.min_mbps:.2f}-{self.max_mbps:.2f}Mbps"
                if self.shape_mode == "rate"
                else f"experimental-profile:{self.profile.value}"
            )
            previous_clients = {item.address: item for item in previous.clients}
            client_rates = []
            client_rates_structured = []
            for client in current.clients:
                prior = previous_clients.get(client.address)
                prior_bytes = prior.bytes_sent if prior is not None else 0
                client_mbps = (
                    (client.bytes_sent - prior_bytes) * 8
                    / (time_delta * 1_000_000)
                )
                target_text = (
                    f",target={client.current_rate_mbps:.2f}Mbps"
                    if client.current_rate_mbps is not None
                    else ",native-profile"
                )
                client_rates.append(
                    f"{client.address[0]}:{client.address[1]}="
                    f"{client_mbps:.2f}Mbps{target_text}"
                )
                client_rates_structured.append((client, client_mbps))
            per_client = ";".join(client_rates) or "none"
            if self.stats_json:
                payload = {
                    "kind": "server",
                    "timestamp": current.timestamp,
                    "totals": {
                        "bytes_sent": current.bytes_sent,
                        "packets_sent": current.packets_sent,
                    },
                    "window": {
                        "duration_seconds": time_delta,
                        "mbps": mbps,
                        "pps": pps,
                    },
                    "clients": [
                        {
                            "address": list(client.address),
                            "last_seen": client.last_seen,
                            "bytes_received": client.bytes_received,
                            "packets_received": client.packets_received,
                            "bytes_sent": client.bytes_sent,
                            "packets_sent": client.packets_sent,
                            "current_rate_mbps": client.current_rate_mbps,
                            "window_mbps": client_mbps,
                        }
                        for client, client_mbps in client_rates_structured
                    ],
                    "pattern": pattern_desc,
                }
                print(
                    "[SNAPSHOT] "
                    + json.dumps(payload, sort_keys=True, separators=(",", ":")),
                    flush=True,
                )
            else:
                print(
                    f"[STATS] Clients: {current.client_count} | "
                    f"Total Rate: {mbps:.2f} Mbps | "
                    f"Total PPS: {pps:.0f} | "
                    f"Per-client: {per_client} | Pattern: {pattern_desc}",
                    flush=True,
                )
            previous = current

    def stop(self, join_timeout=2.0):
        """Request shutdown, close the socket, and join workers once."""
        with self._lifecycle_lock:
            self._stop_event.set()
            self._close_socket()
            threads = tuple(self._threads)
        deadline = time.monotonic() + max(0.0, float(join_timeout))
        current = threading.current_thread()
        for thread in threads:
            if thread is current:
                continue
            thread.join(max(0.0, deadline - time.monotonic()))
        return not any(thread.is_alive() for thread in threads if thread is not current)


def main():
    parser = argparse.ArgumentParser(description="Traffic masking server")
    parser.add_argument("--host", default="0.0.0.0", help="IP address to bind")
    parser.add_argument("--port", type=int, default=8888, help="UDP port")
    parser.add_argument(
        "--mbps",
        type=float,
        default=None,
        help="Fixed target Mbps in rate shape mode (default: 5)",
    )
    parser.add_argument(
        "--min-mbps",
        type=float,
        default=None,
        help="Minimum rate in Mbps for floating rate mode",
    )
    parser.add_argument(
        "--max-mbps",
        type=float,
        default=None,
        help="Rate-mode upper bound or optional profile-mode ceiling",
    )
    parser.add_argument(
        "--shape-mode",
        choices=["rate", "profile"],
        default="rate",
        help="Offered-load contract (default: rate)",
    )
    parser.add_argument(
        "--profile",
        choices=["web", "video", "voip", "file", "gaming", "mixed"],
        default=None,
        help="Required experimental traffic profile in profile shape mode",
    )
    parser.add_argument(
        "--padding",
        choices=["random", "fixed_buckets", "progressive", "none"],
        default="none",
        help="Observable payload padding in profile mode (default: none)",
    )
    parser.add_argument(
        "--mtu", type=int, default=1200, help="Maximum application UDP datagram size"
    )
    parser.add_argument(
        "--stats-interval",
        type=float,
        default=_env_default("TRAFFIC_MASKING_STATS_INTERVAL", 5.0),
        help="Stats print interval in seconds",
    )
    parser.add_argument(
        "--stats-json",
        action="store_true",
        help="Emit machine-readable runtime snapshots",
    )
    auth_group = parser.add_mutually_exclusive_group()
    auth_group.add_argument(
        "--psk-file",
        help="Path to a 32+ byte pre-shared key file (never pass the key itself)",
    )
    auth_group.add_argument(
        "--insecure-diagnostic",
        action="store_true",
        help="Run without a secret; diagnostic use only",
    )
    parser.add_argument(
        "--max-clients",
        type=int,
        default=16,
        help="Maximum number of authenticated clients",
    )
    parser.add_argument(
        "--max-total-mbps",
        type=float,
        default=100.0,
        help="Maximum configured aggregate server egress in decimal Mbps",
    )
    parser.add_argument(
        "--max-handshakes-per-second",
        type=int,
        default=20,
        help="Global cap on authenticated handshake frames per second",
    )

    args = parser.parse_args()

    try:
        psk = None if args.insecure_diagnostic else load_psk(args.psk_file)
        server = MaskingTrafficServer(
            args.host,
            args.port,
            args.mbps,
            min_mbps=args.min_mbps,
            max_mbps=args.max_mbps,
            profile=args.profile,
            shape_mode=args.shape_mode,
            padding=args.padding,
            mtu=args.mtu,
            stats_interval=args.stats_interval,
            stats_json=args.stats_json,
            psk=psk,
            insecure_diagnostic=args.insecure_diagnostic,
            max_clients=args.max_clients,
            max_total_mbps=args.max_total_mbps,
            max_handshakes_per_second=args.max_handshakes_per_second,
        )
    except ValueError as exc:
        parser.error(str(exc))

    shutdown_requested = threading.Event()

    def request_shutdown(_signum, _frame):
        shutdown_requested.set()

    signal.signal(signal.SIGINT, request_shutdown)
    signal.signal(signal.SIGTERM, request_shutdown)

    try:
        server.start()
        shutdown_requested.wait()
    finally:
        print("\n[*] Stopping server...", flush=True)
        server.stop()


if __name__ == "__main__":
    main()
