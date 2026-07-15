#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# Copyright © 2025 kogeler
# SPDX-License-Identifier: Apache-2.0

"""
Traffic Masking Client - cover traffic client
Receives and generates reverse traffic to create a bidirectional, realistic stream.
"""

import argparse
import math
import os
import random
import socket
import struct
import threading
import time

import numpy as np
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
    ObfuscationConfig,
    Packetizer,
    build_obfuscator,
    init_udp_socket,
    mbps_to_bytes_per_second,
    parse_profile,
)


class AdaptiveTrafficClient:
    """Adaptive traffic masking client"""

    KEEPALIVE_INTERVAL = 5.0  # Send keepalive every 5 seconds
    RECEIVE_TIMEOUT = 10.0  # Consider connection lost after 10s without data
    RECONNECT_DELAY_MIN = 1.0
    RECONNECT_DELAY_MAX = 30.0

    def __init__(
        self,
        server_host,
        server_port,
        response_ratio=0.0,
        advanced=False,
        uplink_profile="mixed",
        header="none",
        padding="random",
        mtu=1200,
        entropy=1.0,
        stats_interval=5.0,
        rng=None,
        byte_source=None,
        psk=None,
        insecure_diagnostic=False,
        keepalive_jitter=0.2,
    ):
        # Validate configuration up front; fail fast on invalid inputs.
        try:
            response_ratio = float(response_ratio)
            entropy = float(entropy)
            stats_interval = float(stats_interval)
        except (TypeError, ValueError):
            raise ValueError(
                "response, entropy, and stats-interval must be numbers"
            ) from None
        if not math.isfinite(response_ratio) or not 0.0 <= response_ratio <= 1.0:
            raise ValueError("response ratio must be in [0.0, 1.0]")
        if not math.isfinite(entropy) or not 0.0 <= entropy <= 1.0:
            raise ValueError("entropy must be in [0.0, 1.0]")
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
        if advanced and mtu - FRAME_OVERHEAD < 256:
            raise ValueError(
                f"mtu must be at least {FRAME_OVERHEAD + 256} bytes "
                "in advanced mode"
            )
        if not math.isfinite(stats_interval) or stats_interval <= 0:
            raise ValueError("stats-interval must be a positive finite number")
        try:
            keepalive_jitter = float(keepalive_jitter)
        except (TypeError, ValueError):
            raise ValueError("keepalive jitter must be a number") from None
        if not math.isfinite(keepalive_jitter) or not 0.0 <= keepalive_jitter < 1.0:
            raise ValueError("keepalive jitter must be in [0.0, 1.0)")
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
        self.running = False
        self.connected = False
        self.last_received = 0.0
        self.stats = {
            "bytes_received": 0,
            "bytes_sent": 0,
            "packets_received": 0,
            "packets_sent": 0,
            "start_time": time.time(),
        }
        self.received_rate = 0
        self.rate_window = []
        self.sequence = 0
        self._rng = rng or random.Random()
        self._byte_source = byte_source or os.urandom
        self.base_key = psk if psk is not None else INSECURE_DIAGNOSTIC_KEY
        self.insecure_diagnostic = bool(insecure_diagnostic)
        self.keepalive_jitter = keepalive_jitter
        self.mtu = mtu
        self.packetizer = Packetizer(mtu, FRAME_OVERHEAD)
        self.data_payload_ceiling = self.packetizer.payload_ceiling
        self._send_lock = threading.Lock()
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
        # Advanced obfuscation settings
        self.advanced = bool(advanced)
        self.obf_cfg = ObfuscationConfig(
            padding_strategy=padding,
            header_mode=header,
            mtu=self.data_payload_ceiling,
            entropy=entropy,
            timing_jitter=0.002,
        )
        self.uplink_profile = parse_profile(uplink_profile)
        self.obfuscator = None

    def _create_socket(self):
        """Create and configure a new UDP socket"""
        if self.socket:
            try:
                self.socket.close()
            except Exception:
                pass
        addresses = socket.getaddrinfo(
            self.server_host,
            self.server_port,
            family=socket.AF_INET,
            type=socket.SOCK_DGRAM,
        )
        if not addresses:
            raise OSError(f"could not resolve server {self.server_host}")
        self.server_addr = addresses[0][4][:2]
        self.socket = init_udp_socket(socket.socket(socket.AF_INET, socket.SOCK_DGRAM))
        self.socket.settimeout(2.0)
        self._reset_protocol_state()

    def _random_nonce(self):
        nonce = bytes(self._byte_source(NONCE_SIZE))
        if len(nonce) != NONCE_SIZE:
            raise ValueError("byte source returned the wrong nonce length")
        return nonce if nonce != ZERO_NONCE else b"\x01" + nonce[1:]

    def _reset_protocol_state(self):
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

    def _control_padding(self):
        return make_padding(
            self._rng, self._byte_source, 0, CONTROL_PADDING_MAX
        )

    def _send_registration(self):
        """Send an authenticated HELLO (UDP: no delivery guarantee)."""
        try:
            hello = encode_frame(
                MessageType.HELLO,
                self.client_nonce,
                ZERO_NONCE,
                self.handshake_sequence,
                self.base_key,
                padding=self._control_padding(),
            )
            self.socket.sendto(hello, self.server_addr)
            print(
                f"[*] Handshake HELLO sent to {self.server_addr[0]}:"
                f"{self.server_addr[1]}",
                flush=True,
            )
            return True
        except Exception as e:
            print(f"[!] Registration failed: {e}", flush=True)
            return False

    def _process_datagram(self, datagram, addr):
        """Authenticate one server datagram and return its DATA payload or None."""
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
                self.socket.sendto(auth, self.server_addr)
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

    def _send_session_message(self, message_type, payload=b""):
        if not self.handshake_accepted or self.session_send_key is None:
            return False
        with self._send_lock:
            self.control_send_sequence += 1
            datagram = encode_frame(
                message_type,
                self.client_nonce,
                self.session_nonce,
                self.control_send_sequence,
                self.session_send_key,
                payload=payload,
                padding=(
                    self._control_padding()
                    if message_type is MessageType.KEEPALIVE
                    else b""
                ),
            )
            try:
                sent = self.socket.sendto(datagram, self.server_addr)
            except OSError as exc:
                print(f"[!] Send error: {exc}", flush=True)
                return False
            if sent != len(datagram):
                return False
            self.stats["bytes_sent"] += sent
            self.stats["packets_sent"] += 1
            return True

    def _next_keepalive_delay(self):
        factor = 1.0 + self._rng.uniform(
            -self.keepalive_jitter, self.keepalive_jitter
        )
        return self.KEEPALIVE_INTERVAL * factor

    def _wait_for_server(self, timeout=5.0):
        """Wait for actual data from the server to confirm connection"""
        deadline = time.time() + timeout
        while time.time() < deadline and self.running:
            if self.connected:
                return True
            time.sleep(0.2)
        return self.connected

    def _reconnect(self):
        """Reconnect to the server with exponential backoff"""
        delay = self.RECONNECT_DELAY_MIN
        while self.running:
            print(
                f"[*] Attempting reconnect in {delay:.1f}s...",
                flush=True,
            )
            time.sleep(delay)
            if not self.running:
                break
            try:
                self._create_socket()
                self._send_registration()
                # Wait for actual server response to confirm connection
                if self._wait_for_server(timeout=delay + 2.0):
                    print("[*] Reconnected successfully", flush=True)
                    self.received_rate = 0
                    self.rate_window.clear()
                    return
                else:
                    print("[!] No response from server", flush=True)
            except Exception as e:
                print(f"[!] Reconnect failed: {e}", flush=True)
            delay = min(delay * 2, self.RECONNECT_DELAY_MAX)

    def connect(self):
        """Connect to the server"""
        self._create_socket()
        self.running = True

        print(
            f"[*] Traffic masking client connecting to {self.server_addr[0]}:"
            f"{self.server_addr[1]}",
            flush=True,
        )
        auth_mode = "INSECURE DIAGNOSTIC" if self.insecure_diagnostic else "PSK"
        print(f"[*] Control authentication: {auth_mode}", flush=True)
        # Initialize obfuscator in advanced mode
        if self.advanced:
            self.obfuscator = build_obfuscator(
                self.obf_cfg, rng=self._rng, byte_source=self._byte_source
            )
            print(
                f"[*] Advanced client mode: uplink_profile={self.uplink_profile.value}, header={self.obf_cfg.header_mode}, padding={self.obf_cfg.padding_strategy}, mtu={self.obf_cfg.mtu}, entropy={self.obf_cfg.entropy}",
                flush=True,
            )

        # Send initial registration packet
        self._send_registration()
        self.last_received = time.time()  # Grace period for initial connection

        # Start threads
        threading.Thread(target=self.receive_loop, daemon=True).start()
        threading.Thread(target=self.send_loop, daemon=True).start()
        threading.Thread(target=self.keepalive_loop, daemon=True).start()
        threading.Thread(target=self.stats_loop, daemon=True).start()

    def generate_response_packet(self, size=None):
        """Generate uplink response packet"""
        if size is None:
            # Vary response size
            size = self._rng.choice(
                [
                    self._rng.randint(64, 200),  # Small ACK-like
                    self._rng.randint(200, 600),  # Medium
                    self._rng.randint(600, 1200),  # Large
                ]
            )

        self.sequence += 1

        # Format: [type(1)] [sequence(4)] [timestamp(8)] [random_data]
        packet_type = b"\x02"  # Type: response packet
        seq_bytes = struct.pack("!I", self.sequence)
        timestamp = struct.pack("!Q", int(time.time() * 1000000))

        header_size = 1 + 4 + 8
        data_size = max(0, size - header_size)

        # Bulk CSPRNG payload (no per-byte Python RNG in the hot path).
        random_data = self._byte_source(data_size)

        return packet_type + seq_bytes + timestamp + random_data

    def send_packet(self, packet):
        """Send packet to the server"""
        try:
            if self.advanced and self.obfuscator is not None:
                packet = self.obfuscator.transform(
                    packet, profile=self.uplink_profile
                )
            for fragment in self.packetizer.packetize(packet):
                self._send_session_message(MessageType.DATA, fragment)
        except Exception as e:
            print(f"[!] Send error: {e}", flush=True)

    def receive_loop(self):
        """Receive packets from the server"""
        window_start = time.time()
        window_bytes = 0

        while self.running:
            try:
                data, addr = self.socket.recvfrom(MAX_DATAGRAM_SIZE)

                payload = self._process_datagram(data, addr)
                if payload is None:
                    continue

                self.last_received = time.time()
                self.connected = True
                self.stats["bytes_received"] += len(data)
                self.stats["packets_received"] += 1

                # Calculate receive rate
                window_bytes += len(data)
                current_time = time.time()
                if current_time - window_start >= 1.0:  # 1 second window
                    self.received_rate = window_bytes * 8 / 1_000_000  # decimal Mbps
                    self.rate_window.append(self.received_rate)
                    if len(self.rate_window) > 10:
                        self.rate_window.pop(0)
                    window_start = current_time
                    window_bytes = 0

                # Occasionally send echo to simulate interactivity
                if random.random() < 0.01:  # 1% probability
                    echo_packet = self.generate_response_packet(len(payload) // 4)
                    self.send_packet(echo_packet)

            except socket.timeout:
                continue
            except Exception as e:
                if self.running:
                    print(f"[!] Receive error: {e}", flush=True)
                    time.sleep(0.1)

    def keepalive_loop(self):
        """Send periodic keepalives and handle reconnection"""
        while self.running:
            time.sleep(self._next_keepalive_delay())
            if not self.running:
                break

            # Check if we've lost the connection
            if (
                self.last_received > 0
                and (time.time() - self.last_received) > self.RECEIVE_TIMEOUT
            ):
                print(
                    "[!] Connection lost (no data received), reconnecting...",
                    flush=True,
                )
                self.connected = False
                self.received_rate = 0
                self._reconnect()
                # After _reconnect returns (success), resume keepalive loop
                continue

            if self.handshake_accepted:
                self._send_session_message(MessageType.KEEPALIVE)
            else:
                self._send_registration()

    def send_loop(self):
        """Generate uplink traffic"""
        while self.running:
            # Adaptive generation based on received traffic
            if self.received_rate > 0:
                # Send percentage of received rate
                target_send_rate = mbps_to_bytes_per_second(
                    self.received_rate * self.response_ratio
                )  # bytes/sec

                # Add random bursts
                if random.random() < 0.05:  # 5% burst probability
                    target_send_rate *= random.uniform(1.5, 3)

                # Generate packets
                bytes_to_send = int(target_send_rate / 100)  # Divide by send frequency

                while bytes_to_send > 0:
                    packet_size = min(bytes_to_send, random.randint(200, 1000))
                    packet = self.generate_response_packet(packet_size)
                    self.send_packet(packet)
                    bytes_to_send -= len(packet)
                    time.sleep(random.uniform(0.001, 0.005))

            time.sleep(0.01)  # 100 Hz main loop

    def stats_loop(self):
        """Print runtime statistics"""
        while self.running:
            time.sleep(self.stats_interval)
            elapsed = time.time() - self.stats["start_time"]
            if elapsed > 0:
                recv_mbps = (self.stats["bytes_received"] * 8) / (elapsed * 1_000_000)
                send_mbps = (self.stats["bytes_sent"] * 8) / (elapsed * 1_000_000)
                recv_pps = self.stats["packets_received"] / elapsed
                send_pps = self.stats["packets_sent"] / elapsed

                avg_rate = np.mean(self.rate_window) if self.rate_window else 0
                conn_status = "connected" if self.connected else "disconnected"

                print(
                    f"[STATS] Rx: {recv_mbps:.2f} Mbps ({recv_pps:.0f} pps) | "
                    f"Tx: {send_mbps:.2f} Mbps ({send_pps:.0f} pps) | "
                    f"Avg rate: {avg_rate:.2f} Mbps | "
                    f"Status: {conn_status}",
                    flush=True,
                )

    def stop(self):
        """Stop the client"""
        self.running = False
        if self.socket:
            self.socket.close()


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
        "--advanced",
        action="store_true",
        help="Enable advanced obfuscation for uplink packets",
    )
    parser.add_argument(
        "--uplink-profile",
        choices=["web", "video", "voip", "file", "gaming", "mixed"],
        default="mixed",
        help="Uplink traffic profile for advanced mode",
    )
    parser.add_argument(
        "--header",
        choices=["none", "rtp", "quic"],
        default="none",
        help="Pseudo-header type for advanced mode",
    )
    parser.add_argument(
        "--padding",
        choices=["random", "fixed_buckets", "progressive", "none"],
        default="random",
        help="Padding strategy for advanced mode",
    )
    parser.add_argument(
        "--mtu", type=int, default=1200, help="MTU for fragmentation in advanced mode"
    )
    parser.add_argument(
        "--entropy",
        type=float,
        default=1.0,
        help="Payload entropy (0..1) for advanced mode",
    )
    parser.add_argument(
        "--stats-interval",
        type=float,
        default=5.0,
        help="Stats print interval in seconds",
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
            advanced=args.advanced,
            uplink_profile=args.uplink_profile,
            header=args.header,
            padding=args.padding,
            mtu=args.mtu,
            entropy=args.entropy,
            stats_interval=args.stats_interval,
            psk=psk,
            insecure_diagnostic=args.insecure_diagnostic,
        )
    except ValueError as exc:
        parser.error(str(exc))

    try:
        client.connect()
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[*] Stopping client...", flush=True)
        client.stop()


if __name__ == "__main__":
    main()
