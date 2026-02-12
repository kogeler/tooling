#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# Copyright © 2025 kogeler
# SPDX-License-Identifier: Apache-2.0

"""
Traffic Masking Client - cover traffic client
Receives and generates reverse traffic to create a bidirectional, realistic stream.
"""

import argparse
import random
import socket
import struct
import threading
import time

import numpy as np
from masking_lib import (
    ObfuscationConfig,
    build_obfuscator,
    init_udp_socket,
    parse_profile,
    send_fragments,
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
        response_ratio=0.3,
        advanced=False,
        uplink_profile="mixed",
        header="none",
        padding="random",
        mtu=1200,
        entropy=1.0,
        stats_interval=5.0,
    ):
        self.server_host = server_host
        self.server_port = server_port
        self.server_addr = (server_host, server_port)
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
        self.stats_interval = float(stats_interval)
        # Advanced obfuscation settings
        self.advanced = bool(advanced)
        self.obf_cfg = ObfuscationConfig(
            padding_strategy=padding,
            header_mode=header,
            mtu=int(mtu),
            entropy=float(entropy),
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
        self.socket = init_udp_socket(socket.socket(socket.AF_INET, socket.SOCK_DGRAM))
        self.socket.settimeout(2.0)

    def _register(self):
        """Send registration packet to the server"""
        try:
            self.socket.sendto(b"INIT_CLIENT", self.server_addr)
            self.connected = True
            self.last_received = time.time()
            print(
                f"[*] Registration sent to {self.server_host}:{self.server_port}",
                flush=True,
            )
        except Exception as e:
            print(f"[!] Registration failed: {e}", flush=True)
            self.connected = False

    def _reconnect(self):
        """Reconnect to the server with exponential backoff"""
        delay = self.RECONNECT_DELAY_MIN
        while self.running:
            print(
                f"[*] Reconnecting in {delay:.1f}s...",
                flush=True,
            )
            time.sleep(delay)
            if not self.running:
                break
            try:
                self._create_socket()
                self._register()
                if self.connected:
                    print("[*] Reconnected successfully", flush=True)
                    # Reset rate tracking for fresh start
                    self.received_rate = 0
                    self.rate_window.clear()
                    return
            except Exception as e:
                print(f"[!] Reconnect failed: {e}", flush=True)
            delay = min(delay * 2, self.RECONNECT_DELAY_MAX)

    def connect(self):
        """Connect to the server"""
        self._create_socket()
        self.running = True

        print(
            f"[*] Traffic masking client connecting to {self.server_host}:{self.server_port}",
            flush=True,
        )
        # Initialize obfuscator in advanced mode
        if self.advanced:
            self.obfuscator = build_obfuscator(self.obf_cfg)
            print(
                f"[*] Advanced client mode: uplink_profile={self.uplink_profile.value}, header={self.obf_cfg.header_mode}, padding={self.obf_cfg.padding_strategy}, mtu={self.obf_cfg.mtu}, entropy={self.obf_cfg.entropy}",
                flush=True,
            )

        # Send initial registration packet
        self._register()

        # Start threads
        threading.Thread(target=self.receive_loop, daemon=True).start()
        threading.Thread(target=self.send_loop, daemon=True).start()
        threading.Thread(target=self.keepalive_loop, daemon=True).start()
        threading.Thread(target=self.stats_loop, daemon=True).start()

    def generate_response_packet(self, size=None):
        """Generate uplink response packet"""
        if size is None:
            # Vary response size
            size = random.choice(
                [
                    random.randint(64, 200),  # Small ACK-like
                    random.randint(200, 600),  # Medium
                    random.randint(600, 1200),  # Large
                ]
            )

        self.sequence += 1

        # Format: [type(1)] [sequence(4)] [timestamp(8)] [random_data]
        packet_type = b"\x02"  # Type: response packet
        seq_bytes = struct.pack("!I", self.sequence)
        timestamp = struct.pack("!Q", int(time.time() * 1000000))

        header_size = 1 + 4 + 8
        data_size = max(0, size - header_size)

        # Generate data with entropy
        random_data = bytes([random.randint(0, 255) for _ in range(data_size)])

        return packet_type + seq_bytes + timestamp + random_data

    def send_packet(self, packet):
        """Send packet to the server"""
        try:
            if self.advanced and self.obfuscator is not None:
                fragments, delay = self.obfuscator.obfuscate(
                    packet, profile=self.uplink_profile, base_delay=0.0
                )
                if delay > 0:
                    time.sleep(delay)

                def _on_sent(n: int):
                    self.stats["bytes_sent"] += n
                    self.stats["packets_sent"] += 1

                send_fragments(
                    self.socket, self.server_addr, fragments, on_sent=_on_sent
                )
            else:
                self.socket.sendto(packet, self.server_addr)
                self.stats["bytes_sent"] += len(packet)
                self.stats["packets_sent"] += 1
        except Exception as e:
            print(f"[!] Send error: {e}", flush=True)

    def receive_loop(self):
        """Receive packets from the server"""
        window_start = time.time()
        window_bytes = 0

        while self.running:
            try:
                data, addr = self.socket.recvfrom(65535)

                self.last_received = time.time()
                self.connected = True
                self.stats["bytes_received"] += len(data)
                self.stats["packets_received"] += 1

                # Calculate receive rate
                window_bytes += len(data)
                current_time = time.time()
                if current_time - window_start >= 1.0:  # 1 second window
                    self.received_rate = window_bytes * 8 / 1024 / 1024  # Mbps
                    self.rate_window.append(self.received_rate)
                    if len(self.rate_window) > 10:
                        self.rate_window.pop(0)
                    window_start = current_time
                    window_bytes = 0

                # Occasionally send echo to simulate interactivity
                if random.random() < 0.01:  # 1% probability
                    echo_packet = self.generate_response_packet(len(data) // 4)
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
            time.sleep(self.KEEPALIVE_INTERVAL)
            if not self.running:
                break

            # Send keepalive to stay registered on the server
            try:
                self.socket.sendto(b"KEEPALIVE", self.server_addr)
            except Exception:
                pass

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

    def send_loop(self):
        """Generate uplink traffic"""
        while self.running:
            # Adaptive generation based on received traffic
            if self.received_rate > 0:
                # Send percentage of received rate
                target_send_rate = (
                    self.received_rate * self.response_ratio * 1024 * 1024 / 8
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
                recv_mbps = (self.stats["bytes_received"] * 8) / (elapsed * 1024 * 1024)
                send_mbps = (self.stats["bytes_sent"] * 8) / (elapsed * 1024 * 1024)
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
        "--response", type=float, default=0.3, help="Uplink response ratio (0.0-1.0)"
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

    args = parser.parse_args()

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
    )

    try:
        client.connect()
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[*] Stopping client...", flush=True)
        client.stop()


if __name__ == "__main__":
    main()
