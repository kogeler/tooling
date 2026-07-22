#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# Copyright © 2025 kogeler
# SPDX-License-Identifier: Apache-2.0

"""Authenticated UDP cover-traffic client with optional uplink responses."""

import argparse
import json
import math
import os
import random
import signal
import socket
import struct
import threading
import time
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
    decode_frame,
    derive_session_key,
    encode_frame,
    inspect_frame,
    load_psk,
    make_padding,
)
from masking_lib import (
    Packetizer,
    PayloadPadder,
    RatioBudget,
    init_udp_socket,
)


def _env_default(name, fallback):
    return os.environ.get(name, fallback)


@dataclass(frozen=True, slots=True)
class ClientSnapshot:
    timestamp: float
    connected: bool
    handshake_accepted: bool
    server_address: tuple[str, int] | None
    bytes_received: int
    bytes_sent: int
    packets_received: int
    packets_sent: int
    received_rate_mbps: float
    uplink_ratio: float


class AdaptiveTrafficClient:
    """Adaptive traffic masking client"""

    def __init__(
        self,
        server_host,
        server_port,
        response_ratio=0.0,
        padding="none",
        mtu=1200,
        stats_interval=5.0,
        stats_json=False,
        rng=None,
        byte_source=None,
        psk=None,
        insecure_diagnostic=False,
        keepalive_jitter=0.2,
        keepalive_interval=5.0,
        receive_timeout=10.0,
        reconnect_delay_min=1.0,
        reconnect_delay_max=30.0,
        monotonic_clock=None,
    ):
        # Validate configuration up front; fail fast on invalid inputs.
        try:
            response_ratio = float(response_ratio)
            stats_interval = float(stats_interval)
        except (TypeError, ValueError):
            raise ValueError("response and stats-interval must be numbers") from None
        if not math.isfinite(response_ratio) or not 0.0 <= response_ratio <= 1.0:
            raise ValueError("response ratio must be in [0.0, 1.0]")
        if padding not in PayloadPadder.STRATEGIES:
            raise ValueError(f"unknown padding strategy: {padding}")
        original_mtu = mtu
        if isinstance(original_mtu, bool):
            raise ValueError("mtu must be a positive integer")
        try:
            mtu = int(original_mtu)
        except (TypeError, ValueError, OverflowError):
            raise ValueError("mtu must be a positive integer") from None
        if not isinstance(original_mtu, str) and original_mtu != mtu:
            raise ValueError("mtu must be a positive integer")
        if mtu > MAX_DATAGRAM_SIZE:
            raise ValueError(f"mtu must not exceed {MAX_DATAGRAM_SIZE}")
        if mtu < MIN_CONTROL_MTU:
            raise ValueError(
                f"mtu must be at least {MIN_CONTROL_MTU} bytes "
                "for authenticated control framing"
            )
        if not math.isfinite(stats_interval) or stats_interval <= 0:
            raise ValueError("stats-interval must be a positive finite number")
        try:
            keepalive_jitter = float(keepalive_jitter)
        except (TypeError, ValueError):
            raise ValueError("keepalive jitter must be a number") from None
        if not math.isfinite(keepalive_jitter) or not 0.0 <= keepalive_jitter < 1.0:
            raise ValueError("keepalive jitter must be in [0.0, 1.0)")
        timing_values = {
            "keepalive-interval": keepalive_interval,
            "receive-timeout": receive_timeout,
            "reconnect-delay-min": reconnect_delay_min,
            "reconnect-delay-max": reconnect_delay_max,
        }
        for name, value in timing_values.items():
            try:
                value = float(value)
            except (TypeError, ValueError):
                raise ValueError(f"{name} must be a positive finite number") from None
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be a positive finite number")
            timing_values[name] = value
        keepalive_interval = timing_values["keepalive-interval"]
        receive_timeout = timing_values["receive-timeout"]
        reconnect_delay_min = timing_values["reconnect-delay-min"]
        reconnect_delay_max = timing_values["reconnect-delay-max"]
        if reconnect_delay_min > reconnect_delay_max:
            raise ValueError(
                "reconnect-delay-min must not exceed reconnect-delay-max"
            )
        if receive_timeout <= keepalive_interval * (1.0 + keepalive_jitter):
            raise ValueError(
                "receive-timeout must exceed the maximum jittered keepalive interval"
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

        self.server_host = server_host
        self.server_port = server_port
        self.server_addr = None
        self.response_ratio = response_ratio  # Response traffic ratio
        self.socket = None
        self.connected = False
        self.last_received = 0.0
        self._monotonic_clock = monotonic_clock or time.monotonic
        self._state_lock = threading.RLock()
        self._stats_lock = threading.Lock()
        self._socket_lock = threading.Lock()
        self._lifecycle_lock = threading.RLock()
        self._stop_event = threading.Event()
        self._threads = []
        self.stats = {
            "bytes_received": 0,
            "bytes_sent": 0,
            "packets_received": 0,
            "packets_sent": 0,
        }
        self.received_rate = 0
        self._rate_window_started = self._monotonic_clock()
        self._rate_window_bytes = 0
        self.sequence = 0
        self._rng = rng or random.Random()
        self._byte_source = byte_source or os.urandom
        self.base_key = psk if psk is not None else INSECURE_DIAGNOSTIC_KEY
        self.insecure_diagnostic = bool(insecure_diagnostic)
        self.keepalive_jitter = keepalive_jitter
        self.keepalive_interval = keepalive_interval
        self.receive_timeout = receive_timeout
        self.reconnect_delay_min = reconnect_delay_min
        self.reconnect_delay_max = reconnect_delay_max
        self.mtu = mtu
        self.packetizer = Packetizer(mtu, FRAME_OVERHEAD)
        self.data_payload_ceiling = self.packetizer.payload_ceiling
        self._send_lock = threading.RLock()
        self.client_nonce = ZERO_NONCE
        self.session_nonce = ZERO_NONCE
        self.pending_send_key = None
        self.pending_receive_key = None
        self.session_send_key = None
        self.session_receive_key = None
        self.handshake_sequence = 0
        self.control_send_sequence = 0
        self.control_receive_sequence = -1
        self.handshake_accepted = False
        self.stats_interval = stats_interval
        self.stats_json = bool(stats_json)
        self.uplink_budget = RatioBudget(response_ratio)
        self.padder = PayloadPadder(
            strategy=padding,
            ceiling=self.data_payload_ceiling,
            rng=self._rng,
            byte_source=self._byte_source,
        )

    def _create_socket(self):
        """Create a socket, then atomically install it with fresh session state."""
        addresses = socket.getaddrinfo(
            self.server_host,
            self.server_port,
            family=socket.AF_INET,
            type=socket.SOCK_DGRAM,
        )
        if not addresses:
            raise OSError(f"could not resolve server {self.server_host}")
        server_addr = addresses[0][4][:2]
        client_socket = init_udp_socket(
            socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        )
        client_socket.settimeout(0.5)
        with self._lifecycle_lock:
            if self._stop_event.is_set():
                client_socket.close()
                return False
            with self._send_lock, self._state_lock, self._socket_lock:
                old_socket = self.socket
                self.socket = client_socket
                self.server_addr = server_addr
                self._reset_protocol_state_locked()
        if old_socket is not None:
            try:
                old_socket.close()
            except OSError:
                pass
        return True

    @property
    def worker_threads(self):
        return tuple(self._threads)

    def _current_socket(self):
        with self._socket_lock:
            return self.socket

    def _socket_is_current(self, client_socket):
        with self._socket_lock:
            return self.socket is client_socket

    def _close_socket(self):
        with self._socket_lock:
            client_socket = self.socket
            self.socket = None
        if client_socket is not None:
            try:
                client_socket.close()
            except OSError:
                pass

    def _random_nonce(self):
        nonce = bytes(self._byte_source(NONCE_SIZE))
        if len(nonce) != NONCE_SIZE:
            raise ValueError("byte source returned the wrong nonce length")
        return nonce if nonce != ZERO_NONCE else b"\x01" + nonce[1:]

    def _reset_protocol_state(self):
        with self._send_lock, self._state_lock:
            self._reset_protocol_state_locked()

    def _reset_protocol_state_locked(self):
        self.client_nonce = self._random_nonce()
        self.session_nonce = ZERO_NONCE
        self.pending_send_key = None
        self.pending_receive_key = None
        self.session_send_key = None
        self.session_receive_key = None
        self.handshake_sequence = self._rng.randint(1, 2**63 - 1)
        self.control_send_sequence = self.handshake_sequence
        self.control_receive_sequence = -1
        self.handshake_accepted = False
        self.connected = False
        self.uplink_budget = RatioBudget(self.response_ratio)
        self._rate_window_started = self._monotonic_clock()
        self._rate_window_bytes = 0

    def _control_padding(self):
        return make_padding(
            self._rng, self._byte_source, 0, CONTROL_PADDING_MAX
        )

    def _send_registration(self):
        """Send an authenticated HELLO (UDP: no delivery guarantee)."""
        with self._send_lock, self._state_lock:
            try:
                hello = encode_frame(
                    MessageType.HELLO,
                    self.client_nonce,
                    ZERO_NONCE,
                    self.handshake_sequence,
                    self.base_key,
                    padding=self._control_padding(),
                )
                client_socket = self._current_socket()
                if client_socket is None or self.server_addr is None:
                    return False
                client_socket.sendto(hello, self.server_addr)
                print(
                    f"[*] Handshake HELLO sent to {self.server_addr[0]}:"
                    f"{self.server_addr[1]}",
                    flush=True,
                )
                return True
            except OSError as exc:
                if not self._stop_event.is_set():
                    print(f"[!] Registration failed: {exc}", flush=True)
                return False

    def _process_datagram(self, datagram, addr):
        """Authenticate one server datagram and return its DATA payload or None."""
        with self._send_lock, self._state_lock:
            return self._process_datagram_locked(datagram, addr)

    def _process_datagram_locked(self, datagram, addr):
        if self.server_addr is None or addr[:2] != self.server_addr:
            return None
        try:
            inspected = inspect_frame(datagram)
        except ProtocolError:
            return None

        if inspected.message_type is MessageType.CHALLENGE:
            try:
                challenge = decode_frame(datagram, self.base_key)
            except ProtocolError:
                return None
            if (
                challenge.client_nonce != self.client_nonce
                or challenge.session_nonce == ZERO_NONCE
                or challenge.sequence != self.handshake_sequence
            ):
                return None
            self.session_nonce = challenge.session_nonce
            self.pending_send_key = derive_session_key(
                self.base_key,
                self.client_nonce,
                self.session_nonce,
                CLIENT_TO_SERVER,
            )
            self.pending_receive_key = derive_session_key(
                self.base_key,
                self.client_nonce,
                self.session_nonce,
                SERVER_TO_CLIENT,
            )
            self.control_send_sequence = self.handshake_sequence + 1
            auth = encode_frame(
                MessageType.AUTH,
                self.client_nonce,
                self.session_nonce,
                self.control_send_sequence,
                self.base_key,
                payload=challenge.payload,
                padding=self._control_padding(),
            )
            try:
                client_socket = self._current_socket()
                if client_socket is None:
                    return None
                client_socket.sendto(auth, self.server_addr)
            except OSError:
                return None
            return None

        if inspected.message_type is MessageType.ACCEPT:
            if self.pending_receive_key is None or self.pending_send_key is None:
                return None
            try:
                accept = decode_frame(datagram, self.pending_receive_key)
            except ProtocolError:
                return None
            if (
                accept.client_nonce != self.client_nonce
                or accept.session_nonce != self.session_nonce
                or accept.sequence != 0
            ):
                return None
            self.session_send_key = self.pending_send_key
            self.session_receive_key = self.pending_receive_key
            self.pending_send_key = None
            self.pending_receive_key = None
            self.control_receive_sequence = accept.sequence
            self.handshake_accepted = True
            print("[*] Authenticated session accepted", flush=True)
            return None

        if inspected.message_type is not MessageType.DATA:
            return None
        if not self.handshake_accepted or self.session_receive_key is None:
            return None
        if (
            inspected.client_nonce != self.client_nonce
            or inspected.session_nonce != self.session_nonce
        ):
            return None
        try:
            frame = decode_frame(datagram, self.session_receive_key)
        except ProtocolError:
            return None
        if frame.sequence <= self.control_receive_sequence:
            return None
        self.control_receive_sequence = frame.sequence
        return frame.payload

    def _send_session_message(
        self, message_type, payload=b"", allow_budget_debt=False
    ):
        with self._send_lock, self._state_lock:
            if not self.handshake_accepted or self.session_send_key is None:
                return 0
            next_sequence = self.control_send_sequence + 1
            datagram = encode_frame(
                message_type,
                self.client_nonce,
                self.session_nonce,
                next_sequence,
                self.session_send_key,
                payload=payload,
                padding=(
                    self._control_padding()
                    if message_type is MessageType.KEEPALIVE
                    else b""
                ),
            )
            if not self.uplink_budget.allows(
                len(datagram), allow_debt=allow_budget_debt
            ):
                return 0
            self.control_send_sequence = next_sequence
            try:
                client_socket = self._current_socket()
                if client_socket is None or self.server_addr is None:
                    return 0
                sent = client_socket.sendto(datagram, self.server_addr)
            except OSError as exc:
                if not self._stop_event.is_set():
                    print(f"[!] Send error: {exc}", flush=True)
                return 0
            if sent != len(datagram):
                return 0
            with self._stats_lock:
                self.stats["bytes_sent"] += sent
                self.stats["packets_sent"] += 1
            self.uplink_budget.record_uplink(sent)
            return sent

    def _next_keepalive_delay(self):
        with self._state_lock:
            factor = 1.0 + self._rng.uniform(
                -self.keepalive_jitter, self.keepalive_jitter
            )
            return self.keepalive_interval * factor

    def _wait_for_server(self, timeout=5.0):
        """Wait for actual data from the server to confirm connection"""
        deadline = self._monotonic_clock() + timeout
        while self._monotonic_clock() < deadline and not self._stop_event.is_set():
            with self._state_lock:
                if self.connected:
                    return True
            self._stop_event.wait(min(0.2, timeout))
        with self._state_lock:
            return self.connected

    def _reconnect(self):
        """Reconnect to the server with exponential backoff"""
        delay = self.reconnect_delay_min
        while not self._stop_event.is_set():
            print(
                f"[*] Attempting reconnect in {delay:.1f}s...",
                flush=True,
            )
            if self._stop_event.wait(delay):
                break
            try:
                if not self._create_socket():
                    break
                self._send_registration()
                # Wait for actual server response to confirm connection
                if self._wait_for_server(timeout=self.receive_timeout):
                    print("[*] Reconnected successfully", flush=True)
                    with self._state_lock:
                        self.received_rate = 0
                    return
                else:
                    print("[!] No response from server", flush=True)
            except Exception as e:
                print(f"[!] Reconnect failed: {e}", flush=True)
            delay = min(delay * 2, self.reconnect_delay_max)

    def connect(self):
        """Create the session socket and start managed worker threads."""
        with self._lifecycle_lock:
            if any(thread.is_alive() for thread in self._threads):
                raise RuntimeError("client is already running")
            self._stop_event.clear()
            if not self._create_socket():
                raise RuntimeError("client shutdown was requested during connect")

            print(
                f"[*] Traffic masking client connecting to {self.server_addr[0]}:"
                f"{self.server_addr[1]}",
                flush=True,
            )
            auth_mode = (
                "INSECURE DIAGNOSTIC" if self.insecure_diagnostic else "PSK"
            )
            print(f"[*] Control authentication: {auth_mode}", flush=True)
            if self.padder.strategy != "none":
                print(
                    f"[*] Uplink padding: {self.padder.strategy} | mtu={self.mtu}",
                    flush=True,
                )

            self._send_registration()
            with self._state_lock:
                self.last_received = self._monotonic_clock()

            workers = (
                ("traffic-masking-client-receive", self.receive_loop),
                ("traffic-masking-client-send", self.send_loop),
                ("traffic-masking-client-keepalive", self.keepalive_loop),
                ("traffic-masking-client-stats", self.stats_loop),
            )
            self._threads = [
                threading.Thread(name=name, target=target, daemon=False)
                for name, target in workers
            ]
            for thread in self._threads:
                thread.start()

    def generate_response_packet(self, size=None):
        """Generate uplink response packet"""
        with self._state_lock:
            if size is None:
                size = self._rng.choice(
                    [
                        self._rng.randint(64, 200),
                        self._rng.randint(200, 600),
                        self._rng.randint(600, 1200),
                    ]
                )
            self.sequence += 1
            seq_bytes = struct.pack("!I", self.sequence)
        timestamp = struct.pack("!Q", int(time.time() * 1_000_000))
        data_size = max(0, size - 13)
        random_data = self._byte_source(data_size)
        return b"\x02" + seq_bytes + timestamp + random_data

    def send_packet(self, packet):
        """Send packet to the server"""
        sent_bytes = 0
        try:
            packet = self.padder.transform(packet)
            for fragment in self.packetizer.packetize(packet):
                sent = self._send_session_message(MessageType.DATA, fragment)
                if not sent:
                    break
                sent_bytes += sent
        except Exception as e:
            print(f"[!] Send error: {e}", flush=True)
        return sent_bytes

    def receive_loop(self):
        """Receive packets from the server"""
        while not self._stop_event.is_set():
            client_socket = self._current_socket()
            if client_socket is None:
                break
            try:
                data, addr = client_socket.recvfrom(MAX_DATAGRAM_SIZE)

                payload = self._process_datagram(data, addr)
                if payload is None:
                    continue

                self._record_received_data(len(data))

            except socket.timeout:
                continue
            except OSError as exc:
                if self._stop_event.is_set() or not self._socket_is_current(
                    client_socket
                ):
                    continue
                print(f"[!] Receive error: {exc}", flush=True)
                self._stop_event.wait(0.1)
            except Exception as exc:
                if not self._stop_event.is_set():
                    print(f"[!] Receive error: {exc}", flush=True)
                    self._stop_event.wait(0.1)

    def _record_received_data(self, byte_count):
        now = self._monotonic_clock()
        with self._state_lock:
            with self._stats_lock:
                self.stats["bytes_received"] += byte_count
                self.stats["packets_received"] += 1
            self.last_received = now
            self.connected = True
            self.uplink_budget.record_downlink(byte_count)
            self._rate_window_bytes += byte_count
            elapsed = now - self._rate_window_started
            if elapsed >= 1.0:
                self.received_rate = (
                    self._rate_window_bytes * 8 / (elapsed * 1_000_000)
                )
                self._rate_window_started = now
                self._rate_window_bytes = 0

    def keepalive_loop(self):
        """Send periodic keepalives and handle reconnection"""
        while not self._stop_event.is_set():
            if self._stop_event.wait(self._next_keepalive_delay()):
                break

            with self._state_lock:
                connection_lost = (
                    self.last_received > 0
                    and (self._monotonic_clock() - self.last_received)
                    > self.receive_timeout
                )
            if connection_lost:
                print(
                    "[!] Connection lost (no data received), reconnecting...",
                    flush=True,
                )
                with self._state_lock:
                    self.connected = False
                    self.received_rate = 0
                self._reconnect()
                continue

            with self._state_lock:
                handshake_accepted = self.handshake_accepted
            if handshake_accepted:
                self._send_session_message(
                    MessageType.KEEPALIVE, allow_budget_debt=True
                )
            else:
                self._send_registration()

    def send_loop(self):
        """Spend response credit on framed DATA without bypass traffic."""
        while not self._stop_event.is_set():
            with self._state_lock:
                available_datagram_bytes = int(self.uplink_budget.available_bytes)
            available_payload_bytes = available_datagram_bytes - FRAME_OVERHEAD
            if available_payload_bytes >= 13:
                with self._state_lock:
                    packet_size = min(
                        available_payload_bytes,
                        self.data_payload_ceiling,
                        self._rng.randint(200, 1000),
                    )
                packet = self.generate_response_packet(packet_size)
                self.send_packet(packet)
            self._stop_event.wait(0.01)

    def snapshot(self, now=None):
        """Return an immutable runtime snapshot for metrics and tests."""
        with self._state_lock:
            with self._stats_lock:
                timestamp = self._monotonic_clock() if now is None else float(now)
                bytes_received = self.stats["bytes_received"]
                bytes_sent = self.stats["bytes_sent"]
                packets_received = self.stats["packets_received"]
                packets_sent = self.stats["packets_sent"]
            return ClientSnapshot(
                timestamp=timestamp,
                connected=self.connected,
                handshake_accepted=self.handshake_accepted,
                server_address=self.server_addr,
                bytes_received=bytes_received,
                bytes_sent=bytes_sent,
                packets_received=packets_received,
                packets_sent=packets_sent,
                received_rate_mbps=self.received_rate,
                uplink_ratio=self.uplink_budget.observed_ratio,
            )

    def stats_loop(self):
        """Print instantaneous monotonic-window runtime statistics."""
        previous = self.snapshot()
        while not self._stop_event.wait(self.stats_interval):
            current = self.snapshot()
            elapsed = current.timestamp - previous.timestamp
            if elapsed <= 0:
                previous = current
                continue
            recv_mbps = (
                (current.bytes_received - previous.bytes_received)
                * 8
                / (elapsed * 1_000_000)
            )
            send_mbps = (
                (current.bytes_sent - previous.bytes_sent)
                * 8
                / (elapsed * 1_000_000)
            )
            recv_pps = (
                current.packets_received - previous.packets_received
            ) / elapsed
            send_pps = (current.packets_sent - previous.packets_sent) / elapsed
            conn_status = "connected" if current.connected else "disconnected"
            if self.stats_json:
                payload = {
                    "kind": "client",
                    "timestamp": current.timestamp,
                    "connected": current.connected,
                    "handshake_accepted": current.handshake_accepted,
                    "server_address": (
                        list(current.server_address)
                        if current.server_address is not None
                        else None
                    ),
                    "totals": {
                        "bytes_received": current.bytes_received,
                        "bytes_sent": current.bytes_sent,
                        "packets_received": current.packets_received,
                        "packets_sent": current.packets_sent,
                    },
                    "window": {
                        "duration_seconds": elapsed,
                        "rx_mbps": recv_mbps,
                        "tx_mbps": send_mbps,
                        "rx_pps": recv_pps,
                        "tx_pps": send_pps,
                    },
                    "received_rate_mbps": current.received_rate_mbps,
                    "uplink_ratio": current.uplink_ratio,
                }
                print(
                    "[SNAPSHOT] "
                    + json.dumps(payload, sort_keys=True, separators=(",", ":")),
                    flush=True,
                )
            else:
                print(
                    f"[STATS client window] Rx: {recv_mbps:.2f} Mbps "
                    f"({recv_pps:.0f} pps) | "
                    f"Tx: {send_mbps:.2f} Mbps ({send_pps:.0f} pps) | "
                    f"Uplink ratio: {current.uplink_ratio:.3f} | "
                    f"Status: {conn_status}",
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
    parser = argparse.ArgumentParser(description="Traffic masking client")
    parser.add_argument("--server", required=True, help="Server IP address")
    parser.add_argument("--port", type=int, default=8888, help="Server UDP port")
    parser.add_argument(
        "--response",
        type=float,
        default=0.0,
        help="Uplink response ratio (0.0-1.0); default 0.0 keeps the flow "
        "download-dominant. Non-zero uplink is an explicit choice.",
    )
    parser.add_argument(
        "--padding",
        choices=["random", "fixed_buckets", "progressive", "none"],
        default="none",
        help="Observable uplink payload padding (default: none)",
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
    parser.add_argument(
        "--keepalive-interval",
        type=float,
        default=_env_default("TRAFFIC_MASKING_KEEPALIVE_INTERVAL", 5.0),
        help="Base keepalive interval in seconds",
    )
    parser.add_argument(
        "--keepalive-jitter",
        type=float,
        default=_env_default("TRAFFIC_MASKING_KEEPALIVE_JITTER", 0.2),
        help="Fractional keepalive jitter in [0.0, 1.0)",
    )
    parser.add_argument(
        "--receive-timeout",
        type=float,
        default=_env_default("TRAFFIC_MASKING_RECEIVE_TIMEOUT", 10.0),
        help="Seconds without authenticated data before reconnecting",
    )
    parser.add_argument(
        "--reconnect-delay-min",
        type=float,
        default=_env_default("TRAFFIC_MASKING_RECONNECT_DELAY_MIN", 1.0),
        help="Initial reconnect delay in seconds",
    )
    parser.add_argument(
        "--reconnect-delay-max",
        type=float,
        default=_env_default("TRAFFIC_MASKING_RECONNECT_DELAY_MAX", 30.0),
        help="Maximum reconnect delay in seconds",
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

    args = parser.parse_args()

    try:
        psk = None if args.insecure_diagnostic else load_psk(args.psk_file)
        client = AdaptiveTrafficClient(
            args.server,
            args.port,
            args.response,
            padding=args.padding,
            mtu=args.mtu,
            stats_interval=args.stats_interval,
            stats_json=args.stats_json,
            psk=psk,
            insecure_diagnostic=args.insecure_diagnostic,
            keepalive_jitter=args.keepalive_jitter,
            keepalive_interval=args.keepalive_interval,
            receive_timeout=args.receive_timeout,
            reconnect_delay_min=args.reconnect_delay_min,
            reconnect_delay_max=args.reconnect_delay_max,
        )
    except ValueError as exc:
        parser.error(str(exc))

    shutdown_requested = threading.Event()

    def request_shutdown(_signum, _frame):
        shutdown_requested.set()

    signal.signal(signal.SIGINT, request_shutdown)
    signal.signal(signal.SIGTERM, request_shutdown)

    try:
        client.connect()
        shutdown_requested.wait()
    finally:
        print("\n[*] Stopping client...", flush=True)
        client.stop()


if __name__ == "__main__":
    main()
