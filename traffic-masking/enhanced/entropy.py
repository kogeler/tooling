#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# Copyright © 2025 kogeler
# SPDX-License-Identifier: Apache-2.0

"""
Enhanced entropy module for generating realistic encrypted payloads
"""

import os
import struct
import random
import hashlib
import math
from typing import Dict, Any, List, Optional, Tuple
from collections import Counter
from enum import Enum


class ContentType(Enum):
    """Content type enumeration for payload generation"""
    COMPRESSED_VIDEO = "compressed_video"
    COMPRESSED_AUDIO = "compressed_audio"
    TLS_RECORD = "tls_record"
    WEBRTC_SRTP = "webrtc_srtp"
    QUIC_PACKET = "quic_packet"
    SSH_PACKET = "ssh_packet"
    MIXED = "mixed"


class CipherType(Enum):
    """Cipher type for encryption simulation"""
    STREAM = "stream"
    BLOCK = "block"
    AEAD = "aead"


class EntropyEnhancer:
    """
    Enhances entropy characteristics of generated data to match real encrypted traffic.
    Simulates various encryption methods and content types.
    """

    def __init__(self):
        """Initialize entropy enhancer with cipher simulators."""
        self.cipher_blocks = {}
        self.stream_state = os.urandom(32)  # Stream cipher state
        self.block_counter = 0

        # Cache for performance
        self.entropy_cache = {}
        self.payload_cache = {}

        # Statistics
        self.generated_bytes = 0
        self.entropy_measurements = []

    def generate_realistic_encrypted_payload(self, size: int,
                                            content_type: str = 'mixed',
                                            cipher_type: Optional[CipherType] = None) -> bytes:
        """
        Generate payload resembling real encrypted data.

        Args:
            size: Payload size in bytes
            content_type: Type of content being "encrypted"
            cipher_type: Optional cipher type to simulate

        Returns:
            Realistic encrypted-looking payload
        """
        if size <= 0:
            return b""

        # Choose cipher type if not specified
        if cipher_type is None:
            cipher_type = self._select_cipher_type(content_type)

        # Generate base payload based on content type
        if content_type == ContentType.COMPRESSED_VIDEO.value:
            payload = self._generate_compressed_video_payload(size)
        elif content_type == ContentType.COMPRESSED_AUDIO.value:
            payload = self._generate_compressed_audio_payload(size)
        elif content_type == ContentType.TLS_RECORD.value:
            payload = self._generate_tls_record_payload(size)
        elif content_type == ContentType.WEBRTC_SRTP.value:
            payload = self._generate_webrtc_srtp_payload(size)
        elif content_type == ContentType.QUIC_PACKET.value:
            payload = self._generate_quic_packet_payload(size)
        elif content_type == ContentType.SSH_PACKET.value:
            payload = self._generate_ssh_packet_payload(size)
        else:  # mixed or unknown
            payload = self._generate_mixed_payload(size)

        # Apply cipher simulation
        payload = self._apply_cipher_simulation(payload, cipher_type)

        # Update statistics
        self.generated_bytes += len(payload)

        return payload

    def _select_cipher_type(self, content_type: str) -> CipherType:
        """Select appropriate cipher type based on content."""
        cipher_map = {
            ContentType.TLS_RECORD.value: CipherType.AEAD,
            ContentType.QUIC_PACKET.value: CipherType.AEAD,
            ContentType.WEBRTC_SRTP.value: CipherType.STREAM,
            ContentType.SSH_PACKET.value: CipherType.BLOCK,
            ContentType.COMPRESSED_VIDEO.value: CipherType.STREAM,
            ContentType.COMPRESSED_AUDIO.value: CipherType.STREAM,
        }
        return cipher_map.get(content_type, random.choice(list(CipherType)))

    def _generate_compressed_video_payload(self, size: int) -> bytes:
        """Generate payload simulating encrypted compressed video."""
        # Video has structure even after encryption due to frame boundaries
        header_size = min(32, size // 10)

        # NAL unit headers (lower entropy due to patterns)
        header = bytearray()
        for _ in range(header_size):
            # Simulate H.264/H.265 NAL headers
            if random.random() < 0.3:
                # Start code patterns
                header.append(random.choice([0x00, 0x01, 0x41, 0x61]))
            else:
                header.append(random.randint(0x20, 0x7F))

        # Body with variable entropy based on frame type
        body_parts = []
        remaining = size - header_size

        while remaining > 0:
            # Simulate different frame types
            frame_type = random.choices(
                ['i_frame', 'p_frame', 'b_frame'],
                weights=[0.1, 0.6, 0.3]
            )[0]

            nal_size = min(remaining, random.choice([188, 1316, 1400]))

            if frame_type == 'i_frame':
                # I-frames have more structure (lower entropy)
                if random.random() < 0.3:
                    # Repeated macroblocks
                    pattern = os.urandom(16)
                    nal_data = bytearray()
                    for _ in range(nal_size // 16):
                        if random.random() < 0.7:
                            nal_data.extend(pattern)
                        else:
                            nal_data.extend(os.urandom(16))
                    nal_data.extend(os.urandom(nal_size % 16))
                else:
                    nal_data = os.urandom(nal_size)
            elif frame_type == 'p_frame':
                # P-frames have medium entropy
                nal_data = os.urandom(nal_size)
                # Add some structure
                for i in range(0, min(nal_size - 4, 100), 20):
                    nal_data = nal_data[:i] + bytes([0x00, 0x00, 0x01]) + nal_data[i+3:]
            else:  # b_frame
                # B-frames have high entropy (most compressed)
                nal_data = os.urandom(nal_size)

            body_parts.append(bytes(nal_data))
            remaining -= nal_size

        return bytes(header) + b''.join(body_parts)

    def _generate_compressed_audio_payload(self, size: int) -> bytes:
        """Generate payload simulating encrypted compressed audio."""
        # Audio codecs have frame structure
        frame_sizes = {
            'opus': [20, 40, 60],
            'aac': [128, 256, 512],
            'mp3': [144, 288, 576]
        }

        codec = random.choice(list(frame_sizes.keys()))
        frame_size = random.choice(frame_sizes[codec])

        payload = bytearray()

        # Generate pattern for voice (has repetition)
        voice_pattern = os.urandom(frame_size)

        while len(payload) < size:
            if random.random() < 0.7:
                # Voice activity - similar frames
                frame = bytearray(voice_pattern)
                # Add small variations
                for _ in range(min(10, len(frame))):
                    idx = random.randint(0, len(frame) - 1)
                    frame[idx] ^= random.randint(1, 255)
                payload.extend(frame)
            else:
                # Silence or noise - different pattern
                if random.random() < 0.5:
                    # Silence (low entropy)
                    silence_frame = bytes([random.randint(0, 15)]) * frame_size
                    payload.extend(silence_frame)
                else:
                    # Noise (high entropy)
                    payload.extend(os.urandom(frame_size))

        return bytes(payload[:size])

    def _generate_tls_record_payload(self, size: int) -> bytes:
        """Generate payload simulating TLS 1.3 encrypted records."""
        records = []
        remaining = size

        while remaining > 0:
            # TLS record sizes (max 2^14 + 256 for TLS 1.3)
            max_record = 16384 + 256
            record_size = min(remaining, random.choice([
                16384,  # Full record
                8192,   # Half record
                4096,   # Quarter record
                1024,   # Small record
                random.randint(100, 1400)  # Variable
            ]))

            # TLS 1.3 encrypted record structure
            # 5-byte header + encrypted content + 16-byte AEAD tag

            # Record header
            record_type = 0x17  # Application data (everything is 0x17 in TLS 1.3)
            tls_version = 0x0303  # TLS 1.2 for compatibility
            content_length = min(record_size, max_record)

            header = struct.pack('!BHH', record_type, tls_version, content_length)

            # Encrypted content
            if content_length > 16:
                # Content + 1-byte content type + padding
                actual_content = content_length - 16  # Reserve for AEAD tag

                # High entropy encrypted data
                content = os.urandom(actual_content)

                # AEAD tag (appears random but is deterministic in real TLS)
                tag = os.urandom(16)

                record = header + content + tag
            else:
                # Small record, all random
                record = header + os.urandom(content_length)

            records.append(record)
            remaining -= len(record)

        result = b''.join(records)
        return result[:size]

    def _generate_webrtc_srtp_payload(self, size: int) -> bytes:
        """Generate payload simulating WebRTC SRTP packets."""
        # SRTP header (12 bytes) + encrypted payload + auth tag (10 bytes typically)
        if size < 22:
            return os.urandom(size)

        # RTP header
        version = 2
        padding = 0
        extension = random.randint(0, 1)
        cc = 0  # CSRC count
        marker = random.randint(0, 1)
        payload_type = random.choice([111, 96, 97, 98])  # Common WebRTC PTs

        sequence = random.randint(0, 65535)
        timestamp = random.randint(0, 2**32 - 1)
        ssrc = random.randint(0, 2**32 - 1)

        header = struct.pack('!BBHII',
                            (version << 6) | (padding << 5) | (extension << 4) | cc,
                            (marker << 7) | payload_type,
                            sequence, timestamp, ssrc)

        # Extension header if present
        if extension:
            ext_profile = random.randint(0, 65535)
            ext_length = random.randint(1, 10)
            ext_header = struct.pack('!HH', ext_profile, ext_length)
            ext_data = os.urandom(ext_length * 4)
            header = header + ext_header + ext_data

        # Encrypted payload
        payload_size = max(0, size - len(header) - 10)
        encrypted_payload = os.urandom(payload_size)

        # SRTP auth tag
        auth_tag = os.urandom(10)

        return header + encrypted_payload + auth_tag

    def _generate_quic_packet_payload(self, size: int) -> bytes:
        """Generate payload simulating QUIC encrypted packets."""
        # QUIC has complex header structure but payload is AEAD encrypted

        # Short header for 1-RTT packets (most common)
        flags = 0x40  # Short header, key phase 0

        # Destination connection ID (variable length)
        dcid_len = random.choice([0, 8, 16])
        dcid = os.urandom(dcid_len) if dcid_len > 0 else b''

        # Packet number (encrypted)
        pn_length = random.choice([1, 2, 3, 4])
        pn_encrypted = os.urandom(pn_length)

        header = bytes([flags]) + dcid + pn_encrypted

        # AEAD encrypted payload + tag
        remaining = max(0, size - len(header))
        if remaining > 16:
            payload = os.urandom(remaining - 16)
            auth_tag = os.urandom(16)
            return header + payload + auth_tag
        else:
            return header + os.urandom(remaining)

    def _generate_ssh_packet_payload(self, size: int) -> bytes:
        """Generate payload simulating SSH encrypted packets."""
        # SSH uses block ciphers with MAC or AEAD

        # Decide if using MAC
        use_mac = random.random() < 0.5
        mac_size = 32 if use_mac else 0  # HMAC-SHA256

        # Calculate content size (excluding MAC if present)
        content_size = max(16, size - mac_size)  # At least one block

        # Ensure block alignment (common block size is 16)
        block_size = 16
        aligned_size = ((content_size + block_size - 1) // block_size) * block_size

        # Generate blocks with subtle patterns (CBC mode characteristics)
        blocks = []
        for i in range(aligned_size // block_size):
            if i == 0:
                # First block is IV (random)
                block = os.urandom(block_size)
            else:
                # Subsequent blocks have CBC chaining effect
                if random.random() < 0.1:
                    # Occasionally similar blocks (repeated commands)
                    block = blocks[-1]
                    # XOR with something to simulate CBC
                    block = bytes(a ^ b for a, b in zip(block, os.urandom(block_size)))
                else:
                    block = os.urandom(block_size)
            blocks.append(block)

        payload = b''.join(blocks)

        # Add MAC if using it
        if use_mac:
            mac = os.urandom(mac_size)
            payload = payload[:content_size] + mac

        # Ensure exactly size bytes
        if len(payload) < size:
            payload = payload + os.urandom(size - len(payload))
        elif len(payload) > size:
            payload = payload[:size]

        return payload

    def _generate_mixed_payload(self, size: int) -> bytes:
        """Generate mixed encrypted payload."""
        # Mix different encryption patterns
        chunks = []
        remaining = size

        while remaining > 0:
            chunk_type = random.choices(
                ['stream', 'block', 'aead', 'structured'],
                weights=[0.3, 0.3, 0.3, 0.1]
            )[0]

            chunk_size = min(remaining, random.randint(64, 1400))

            if chunk_type == 'stream':
                # Pure random (stream cipher)
                chunk = os.urandom(chunk_size)
            elif chunk_type == 'block':
                # Block-aligned with padding
                blocks = chunk_size // 16
                chunk = os.urandom(blocks * 16)
                if len(chunk) < chunk_size:
                    # PKCS#7 padding
                    pad_len = chunk_size - len(chunk)
                    chunk += bytes([pad_len]) * pad_len
            elif chunk_type == 'aead':
                # AEAD with tag
                if chunk_size > 16:
                    chunk = os.urandom(chunk_size - 16) + os.urandom(16)
                else:
                    chunk = os.urandom(chunk_size)
            else:  # structured
                # Some structure (like TLS records)
                if chunk_size > 5:
                    header = struct.pack('!BHH', 0x17, 0x0303, chunk_size - 5)
                    chunk = header + os.urandom(chunk_size - 5)
                else:
                    chunk = os.urandom(chunk_size)

            chunks.append(chunk[:chunk_size])
            remaining -= chunk_size

        return b''.join(chunks)

    def _apply_cipher_simulation(self, payload: bytes, cipher_type: CipherType) -> bytes:
        """Apply cipher-specific characteristics to payload."""
        if cipher_type == CipherType.STREAM:
            # Stream cipher - XOR with keystream
            keystream = self._generate_keystream(len(payload))
            return bytes(a ^ b for a, b in zip(payload, keystream))

        elif cipher_type == CipherType.BLOCK:
            # Block cipher - ensure alignment and add patterns
            block_size = 16
            aligned_len = ((len(payload) + block_size - 1) // block_size) * block_size

            if len(payload) < aligned_len:
                # Add PKCS#7 padding
                pad_len = aligned_len - len(payload)
                payload = payload + bytes([pad_len]) * pad_len

            # Simulate ECB/CBC patterns
            result = bytearray()
            for i in range(0, len(payload), block_size):
                block = payload[i:i+block_size]
                if random.random() < 0.05:  # 5% repeated blocks (ECB weakness)
                    encrypted = hashlib.md5(b'ecb' + bytes([self.block_counter % 256])).digest()
                else:
                    encrypted = hashlib.md5(block + bytes([self.block_counter])).digest()
                result.extend(encrypted[:block_size])
                self.block_counter += 1

            return bytes(result[:len(payload)])

        else:  # AEAD
            # AEAD - add authentication tag
            if len(payload) > 16:
                return payload
            else:
                # Too small, just return high entropy
                return os.urandom(len(payload))

    def _generate_keystream(self, length: int) -> bytes:
        """Generate keystream for stream cipher simulation."""
        keystream = bytearray()
        state = self.stream_state

        while len(keystream) < length:
            # Simple PRNG-based keystream
            state = hashlib.sha256(state).digest()
            keystream.extend(state)

        self.stream_state = state  # Update state
        return bytes(keystream[:length])

    def calculate_entropy(self, data: bytes) -> float:
        """
        Calculate Shannon entropy of data.

        Args:
            data: Input data

        Returns:
            Normalized entropy (0.0 to 1.0)
        """
        if not data:
            return 0.0

        # Use cache for performance
        data_hash = hashlib.md5(data).hexdigest()
        if data_hash in self.entropy_cache:
            return self.entropy_cache[data_hash]

        # Count byte frequencies
        byte_counts = Counter(data)
        data_len = len(data)

        # Calculate Shannon entropy
        entropy = 0.0
        for count in byte_counts.values():
            if count > 0:
                p = count / data_len
                entropy -= p * math.log2(p)

        # Normalize to 0-1 range (max entropy is 8 bits)
        normalized = entropy / 8.0

        # Cache result
        self.entropy_cache[data_hash] = normalized

        # Record measurement
        self.entropy_measurements.append(normalized)
        if len(self.entropy_measurements) > 1000:
            self.entropy_measurements.pop(0)

        return normalized

    def analyze_payload_characteristics(self, payload: bytes) -> Dict[str, Any]:
        """
        Analyze characteristics of a payload.

        Args:
            payload: Payload to analyze

        Returns:
            Dictionary of characteristics
        """
        if not payload:
            return {'error': 'Empty payload'}

        # Basic metrics
        entropy = self.calculate_entropy(payload)

        # Byte distribution analysis
        byte_counts = Counter(payload)
        most_common = byte_counts.most_common(10)

        # Chi-square test for randomness
        expected = len(payload) / 256
        chi_square = sum((count - expected) ** 2 / expected
                        for count in byte_counts.values())

        # Block cipher detection (look for repeated 16-byte blocks)
        block_size = 16
        blocks = [payload[i:i+block_size]
                 for i in range(0, len(payload) - block_size + 1, block_size)]
        unique_blocks = len(set(blocks))
        repeated_blocks = len(blocks) - unique_blocks

        # Pattern detection
        patterns = self._detect_patterns(payload)

        return {
            'size': len(payload),
            'entropy': entropy,
            'entropy_quality': self._classify_entropy(entropy),
            'most_common_bytes': most_common[:5],
            'chi_square': chi_square,
            'randomness_quality': 'good' if chi_square < 300 else 'poor',
            'repeated_blocks': repeated_blocks,
            'block_cipher_likely': repeated_blocks > 2,
            'patterns_detected': patterns
        }

    def _classify_entropy(self, entropy: float) -> str:
        """Classify entropy level."""
        if entropy < 0.5:
            return 'very_low'
        elif entropy < 0.7:
            return 'low'
        elif entropy < 0.9:
            return 'medium'
        elif entropy < 0.95:
            return 'high'
        else:
            return 'very_high'

    def _detect_patterns(self, data: bytes) -> List[str]:
        """Detect common patterns in data."""
        patterns = []

        # Check for TLS-like headers
        if len(data) >= 5:
            if data[0] in [0x14, 0x15, 0x16, 0x17] and data[1:3] == b'\x03\x03':
                patterns.append('tls_like')

        # Check for null bytes
        null_ratio = data.count(0) / len(data)
        if null_ratio > 0.1:
            patterns.append('high_null_bytes')

        # Check for repeating sequences
        for pattern_len in [2, 4, 8, 16]:
            if len(data) >= pattern_len * 2:
                for i in range(len(data) - pattern_len * 2):
                    if data[i:i+pattern_len] == data[i+pattern_len:i+pattern_len*2]:
                        patterns.append(f'repeat_{pattern_len}')
                        break

        return list(set(patterns))

    def get_statistics(self) -> Dict[str, Any]:
        """Get entropy enhancer statistics."""
        stats = {
            'generated_bytes': self.generated_bytes,
            'generated_mb': self.generated_bytes / (1024 * 1024),
            'cache_size': len(self.entropy_cache),
            'measurements': len(self.entropy_measurements)
        }

        if self.entropy_measurements:
            stats['avg_entropy'] = sum(self.entropy_measurements) / len(self.entropy_measurements)
            stats['min_entropy'] = min(self.entropy_measurements)
            stats['max_entropy'] = max(self.entropy_measurements)

        return stats

    def reset(self):
        """Reset enhancer state."""
        self.cipher_blocks.clear()
        self.stream_state = os.urandom(32)
        self.block_counter = 0
        self.entropy_cache.clear()
        self.payload_cache.clear()
        self.generated_bytes = 0
        self.entropy_measurements.clear()
