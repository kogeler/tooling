#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# Copyright © 2025 kogeler
# SPDX-License-Identifier: Apache-2.0

"""
Traffic Masking Server - cover traffic generator
Creates a variable, realistic-looking stream to mask media traffic patterns from heuristic analysis on encrypted tunnels.
"""

import argparse
import hashlib
import random
import socket
import struct
import threading
import time

import numpy as np
from masking_lib import DynamicObfuscator, TrafficProfile, stream_generator


class TrafficPattern:
    """Generator of diverse traffic rate patterns (CBR, bursts, waves, random-walk, media-like)"""

    def __init__(self):
        self.patterns = [
            self.constant_bitrate,
            self.burst_pattern,
            self.wave_pattern,
            self.random_walk,
            self.media_like_pattern,
        ]
        self.current_pattern = random.choice(self.patterns)
        self.pattern_duration = random.uniform(5, 30)  # seconds
        self.pattern_start = time.time()

    def should_switch_pattern(self):
        """Check if pattern switch is needed"""
        return time.time() - self.pattern_start > self.pattern_duration

    def switch_pattern(self):
        """Switch to a new pattern"""
        self.current_pattern = random.choice(self.patterns)
        self.pattern_duration = random.uniform(5, 30)
        self.pattern_start = time.time()

    def constant_bitrate(self, base_rate):
        """Constant bitrate with small fluctuations"""
        return base_rate * random.uniform(0.95, 1.05)

    def burst_pattern(self, base_rate):
        """Traffic bursts"""
        if random.random() < 0.1:  # 10% chance of burst
            return base_rate * random.uniform(2, 4)
        return base_rate * random.uniform(0.5, 0.8)

    def wave_pattern(self, base_rate):
        """Wave-like pattern"""
        t = time.time()
        wave = np.sin(t / 5) * 0.5 + 1  # Sine wave with period ~31 sec
        return base_rate * wave * random.uniform(0.9, 1.1)

    def random_walk(self, base_rate):
        """Random walk"""
        if not hasattr(self, "walk_value"):
            self.walk_value = base_rate
        change = random.uniform(-0.1, 0.1) * base_rate
        self.walk_value = max(
            base_rate * 0.3, min(base_rate * 2, self.walk_value + change)
        )
        return self.walk_value

    def media_like_pattern(self, base_rate):
        """Media-like stream (video/audio)"""
        # Base flow + periodic key frames
        base = base_rate * 0.7
        if random.random() < 0.05:  # 5% - "key frames"
            return base + base_rate * random.uniform(0.5, 1.5)
        return base + random.uniform(-0.1, 0.1) * base_rate

    def get_current_rate(self, base_rate):
        """Get current bitrate"""
        if self.should_switch_pattern():
            self.switch_pattern()
        return self.current_pattern(base_rate)


class PacketGenerator:
    """Packet generator with variable sizes and pseudo-random payload characteristics"""

    def __init__(self, min_size=64, max_size=1400):
        self.min_size = min_size
        self.max_size = max_size
        self.sequence = 0

    def generate_packet(self, target_size=None):
        """Generate a data packet"""
        if target_size is None:
            # Packet size distribution (simulate realistic traffic)
            weights = [0.1, 0.15, 0.5, 0.15, 0.1]  # Favor medium packets
            sizes = [
                random.randint(self.min_size, 200),  # Small
                random.randint(200, 500),  # Small-medium
                random.randint(500, 1000),  # Medium
                random.randint(1000, 1300),  # Medium-large
                random.randint(1300, self.max_size),  # Large
            ]
            size = random.choices(sizes, weights=weights)[0]
        else:
            size = min(max(target_size, self.min_size), self.max_size)

        # Packet layout: [sequence(4)] [timestamp(8)] [checksum(16)] [random_data]
        self.sequence += 1
        timestamp = struct.pack("!Q", int(time.time() * 1000000))  # microseconds
        seq_bytes = struct.pack("!I", self.sequence)

        # Pseudo-random payload with a time-based pattern
        pattern_seed = int(time.time() / 10)  # changes every 10 seconds
        random.seed(pattern_seed)
        data_size = size - 28  # 4 + 8 + 16 = 28 bytes header
        random_data = bytes([random.randint(0, 255) for _ in range(data_size)])
        random.seed()  # reset seed

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
        target_mbps=5,
        min_mbps=None,
        max_mbps=None,
        advanced=False,
        profile="mixed",
        header="none",
        padding="random",
        mtu=1200,
        entropy=1.0,
        stats_interval=5.0,
    ):
        self.host = host
        self.port = port
        self.target_mbps = target_mbps
        self.min_mbps = min_mbps
        self.max_mbps = max_mbps
        # Use floating rate if min/max specified, otherwise use target
        if min_mbps is not None and max_mbps is not None:
            self.target_bps = ((min_mbps + max_mbps) / 2) * 1024 * 1024
        else:
            self.target_bps = target_mbps * 1024 * 1024
        self.socket = None
        self.clients = {}  # {address: {'last_seen': timestamp, 'stats': {...}}}
        self.running = False
        self.pattern_gen = TrafficPattern()
        self.packet_gen = PacketGenerator()
        self.stats = {"bytes_sent": 0, "packets_sent": 0, "start_time": time.time()}
        self.last_stats = {"bytes_sent": 0, "packets_sent": 0, "time": time.time()}
        self.stats_interval = float(stats_interval)
        # Advanced masking options
        self.advanced = bool(advanced)
        # Normalize profile to TrafficProfile
        try:
            self.profile = (
                TrafficProfile(profile)
                if isinstance(profile, str)
                else (profile or TrafficProfile.MIXED)
            )
        except Exception:
            self.profile = TrafficProfile.MIXED
        self.header_mode = header
        self.padding_strategy = padding
        self.mtu = int(mtu)
        self.entropy = float(entropy)
        self.obfuscator = None
        self.generator = None

    def start(self):
        """Start the server"""
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        # Increase buffers for high throughput
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4194304)  # 4MB
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4194304)  # 4MB

        self.socket.bind((self.host, self.port))
        self.running = True

        print(
            f"[*] Traffic masking server started on {self.host}:{self.port}", flush=True
        )
        if self.min_mbps is not None and self.max_mbps is not None:
            print(
                f"[*] Floating throughput: {self.min_mbps}-{self.max_mbps} Mbps",
                flush=True,
            )
        else:
            print(f"[*] Target throughput: {self.target_mbps} Mbps", flush=True)
        if self.advanced:
            # Initialize obfuscator and generator
            self.obfuscator = DynamicObfuscator(
                padding_strategy=self.padding_strategy,
                timing_jitter=0.002,
                mtu=self.mtu,
                header_mode=self.header_mode,
            )
            self.generator = stream_generator(
                self.profile,
                target_mbps=self.target_mbps
                if (self.min_mbps is None or self.max_mbps is None)
                else None,
                min_mbps=self.min_mbps,
                max_mbps=self.max_mbps,
                obfuscator=self.obfuscator,
                entropy=self.entropy,
            )
            print(
                f"[*] Advanced mode enabled: profile={self.profile.value}, header={self.header_mode}, padding={self.padding_strategy}, mtu={self.mtu}, entropy={self.entropy}",
                flush=True,
            )

        # Start threads
        threading.Thread(target=self.receive_loop, daemon=True).start()
        threading.Thread(target=self.send_loop, daemon=True).start()
        threading.Thread(target=self.stats_loop, daemon=True).start()
        threading.Thread(target=self.cleanup_loop, daemon=True).start()

    def receive_loop(self):
        """Receive packets from clients"""
        while self.running:
            try:
                data, addr = self.socket.recvfrom(65535)

                # Update client info
                if addr not in self.clients:
                    print(f"[+] New client connected: {addr}", flush=True)
                    self.clients[addr] = {
                        "last_seen": time.time(),
                        "bytes_received": 0,
                        "packets_received": 0,
                    }

                self.clients[addr]["last_seen"] = time.time()
                self.clients[addr]["bytes_received"] += len(data)
                self.clients[addr]["packets_received"] += 1

            except Exception as e:
                if self.running:
                    print(f"[!] Receive error: {e}", flush=True)

    def send_loop(self):
        """Send cover traffic to clients"""
        last_send_time = time.time()
        bytes_accumulator = 0

        # Rate control for advanced mode
        rate_window_bytes = 0
        rate_window_start = time.time()

        while self.running:
            if not self.clients:
                time.sleep(0.1)
                continue

            # Advanced generator-driven mode with proper rate limiting
            if getattr(self, "advanced", False) and self.generator is not None:
                try:
                    frags, base_delay = next(self.generator)
                except StopIteration:
                    # Recreate generator if it ever stops
                    self.generator = stream_generator(
                        self.profile,
                        target_mbps=self.target_mbps
                        if not (self.min_mbps and self.max_mbps)
                        else None,
                        min_mbps=self.min_mbps,
                        max_mbps=self.max_mbps,
                        obfuscator=self.obfuscator,
                        entropy=self.entropy,
                    )
                    frags, base_delay = next(self.generator)

                # Send fragments and track bytes
                packet_bytes = 0
                for frag in frags:
                    for addr in list(self.clients.keys()):
                        try:
                            self.socket.sendto(frag, addr)
                            self.stats["bytes_sent"] += len(frag)
                            self.stats["packets_sent"] += 1
                            packet_bytes += len(frag)
                        except Exception as e:
                            print(f"[!] Send error to client {addr}: {e}", flush=True)

                # Update rate window
                rate_window_bytes += packet_bytes
                now = time.time()
                window_elapsed = now - rate_window_start

                # Reset window every second
                if window_elapsed > 1.0:
                    rate_window_bytes = 0
                    rate_window_start = now
                    window_elapsed = 0

                # Use the delay from generator which already implements rate limiting
                time.sleep(base_delay)

                continue

            # Legacy accumulator mode (default)
            current_time = time.time()
            elapsed = current_time - last_send_time

            # Get current target bitrate
            current_rate_bps = self.pattern_gen.get_current_rate(self.target_bps)

            # Compute target bytes to send (with buffer for smoother rate)
            target_bytes = int(current_rate_bps * elapsed * 1.1)  # 10% buffer
            bytes_accumulator += target_bytes

            # Send packets in batches for efficiency
            packets_sent_this_round = 0
            while (
                bytes_accumulator > 0 and self.clients and packets_sent_this_round < 50
            ):
                # Generate larger packets for better throughput
                packet_size = min(bytes_accumulator, random.randint(1000, 1400))
                packet = self.packet_gen.generate_packet(packet_size)

                # Send to all active clients
                for addr in list(self.clients.keys()):
                    try:
                        self.socket.sendto(packet, addr)
                        self.stats["bytes_sent"] += len(packet)
                        self.stats["packets_sent"] += 1
                    except Exception as e:
                        print(f"[!] Send error to client {addr}: {e}")

                bytes_accumulator -= len(packet)
                packets_sent_this_round += 1

                # Minimal sleep between packets in batch
                if packets_sent_this_round % 10 == 0:
                    time.sleep(0.0001)

            last_send_time = current_time

            # Adaptive pacing based on accumulator
            if bytes_accumulator > current_rate_bps * 0.1:
                # Behind schedule, don't sleep
                pass
            else:
                # On schedule, small sleep
                time.sleep(0.0005)

    def cleanup_loop(self):
        """Remove inactive clients"""
        while self.running:
            current_time = time.time()
            inactive_clients = []

            for addr, info in self.clients.items():
                if current_time - info["last_seen"] > 30:  # 30 seconds of inactivity
                    inactive_clients.append(addr)

            for addr in inactive_clients:
                print(f"[-] Client removed (inactive): {addr}", flush=True)
                del self.clients[addr]

            time.sleep(5)

    def stats_loop(self):
        """Print runtime statistics"""
        while self.running:
            time.sleep(self.stats_interval)
            now = time.time()

            # Calculate instantaneous rates based on delta since last stats
            time_delta = now - self.last_stats["time"]
            bytes_delta = self.stats["bytes_sent"] - self.last_stats["bytes_sent"]
            packets_delta = self.stats["packets_sent"] - self.last_stats["packets_sent"]

            if time_delta > 0:
                # Instantaneous rate (not cumulative average)
                mbps = (bytes_delta * 8) / (time_delta * 1024 * 1024)
                pps = packets_delta / time_delta

                pattern_desc = (
                    self.pattern_gen.current_pattern.__name__
                    if not getattr(self, "advanced", False)
                    else f"advanced:{getattr(self, 'profile', None).value}/{getattr(self, 'obfuscator', None).header_mode}"
                )
                print(
                    f"[STATS] Clients: {len(self.clients)} | "
                    f"Rate: {mbps:.2f} Mbps | "
                    f"PPS: {pps:.0f} | "
                    f"Pattern: {pattern_desc}",
                    flush=True,
                )

            # Update last stats for next iteration
            self.last_stats["bytes_sent"] = self.stats["bytes_sent"]
            self.last_stats["packets_sent"] = self.stats["packets_sent"]
            self.last_stats["time"] = now

    def stop(self):
        """Stop the server"""
        self.running = False
        if self.socket:
            self.socket.close()


def main():
    parser = argparse.ArgumentParser(description="Traffic masking server")
    parser.add_argument("--host", default="0.0.0.0", help="IP address to bind")
    parser.add_argument("--port", type=int, default=8888, help="UDP port")
    parser.add_argument(
        "--mbps",
        type=float,
        default=5,
        help="Target rate in Mbps (fixed rate if min/max not specified)",
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
        help="Maximum rate in Mbps for floating rate mode",
    )
    parser.add_argument(
        "--advanced",
        action="store_true",
        help="Enable advanced masking (generator/obfuscator)",
    )
    parser.add_argument(
        "--profile",
        choices=["web", "video", "voip", "file", "gaming", "mixed"],
        default="mixed",
        help="Traffic profile for advanced mode",
    )
    parser.add_argument(
        "--header",
        choices=["none", "rtp", "quic"],
        default="none",
        help="Pseudo-header type in advanced mode",
    )
    parser.add_argument(
        "--padding",
        choices=["random", "fixed_buckets", "progressive", "none"],
        default="random",
        help="Padding strategy in advanced mode",
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

    server = MaskingTrafficServer(
        args.host,
        args.port,
        args.mbps,
        min_mbps=args.min_mbps,
        max_mbps=args.max_mbps,
        advanced=args.advanced,
        profile=args.profile,
        header=args.header,
        padding=args.padding,
        mtu=args.mtu,
        entropy=args.entropy,
        stats_interval=args.stats_interval,
    )

    try:
        server.start()
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[*] Stopping server...", flush=True)
        server.stop()


if __name__ == "__main__":
    main()
