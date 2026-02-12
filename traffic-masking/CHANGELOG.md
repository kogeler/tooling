# Changelog

All notable changes to the Traffic Masking System will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.4] - 2025-02-12

### Fixed
- Client no longer falsely reports "Reconnected successfully" when server is unreachable
- Connection status now reflects actual data flow, not just successful UDP send
- Exponential backoff now works correctly (was resetting every cycle due to false success)

### Changed
- Reconnection logic now waits for actual server response before confirming connection
- Keepalive is only sent when connected; on timeout the client enters reconnect loop first
- All Russian comments in source code translated to English

### Improved
- Reconnection test now validates no false reconnection reports during server downtime
- Reconnection test verifies client shows `disconnected` status while server is down
- Reconnection test requires `Reconnected successfully` message only after real server recovery

## [1.0.3] - 2025-02-12

### Fixed
- Client now automatically reconnects after server restart or network loss
- Previously, the client sent `INIT_CLIENT` only once at startup; after server restart the client was permanently forgotten and traffic never resumed

### Added
- Periodic keepalive packets (every 5s) to maintain server registration
- Connection loss detection (10s receive timeout)
- Automatic reconnection with exponential backoff (1s to 30s)
- Socket timeout on client to prevent indefinite blocking in receive loop
- Connection status (`connected`/`disconnected`) in client stats output
- Reconnection test (`run_reconnection_test`) in the test suite — 3-phase test covering initial connection, server kill, and recovery after server restart

## [1.0.1] - 2024-12-20

### Added
- Rate limiting patterns
- A test script for rate limiting patterns

## [1.0.0] - 2024-12-20

### Added

#### Core Features
- UDP-based cover traffic generation system with server and client components
- Dynamic traffic patterns: constant bitrate, burst, wave, random walk, and media-like patterns
- Variable packet sizes and inter-packet intervals with correlation awareness
- Bidirectional traffic flow with adaptive response ratio
- High throughput capability (8-10 Mbps achieved on modest hardware)
- Floating rate mode with configurable minimum and maximum traffic rates
- Natural traffic variations that follow realistic rate change patterns

#### Protocol Mimicry
- Traffic profiles: web browsing, video streaming, VoIP, file transfer, gaming, mixed
- Protocol-specific packet generation patterns
- Session phase modeling and lifecycle simulation

#### Advanced Obfuscation
- Dynamic packet obfuscation with multiple strategies
- Padding strategies: random, fixed buckets, progressive, none
- Pseudo-headers support: RTP-like and QUIC-like headers
- MTU-aware fragmentation
- Entropy control for payload generation (0.0-1.0 scale)
- Timing jitter and delay variation

#### Enhanced Modules
- **Adaptive Timing Model**: Realistic network delay simulation with congestion modeling, correlated jitter, packet loss simulation
- **Correlation Breaker**: Markov chain-based packet size generation to disrupt statistical analysis
- **ML-Resistant Generator**: Adversarial packet generation to evade machine learning detection
- **Entropy Enhancer**: Realistic encrypted payload generation mimicking various cipher types
- **Protocol State Machines**: Accurate protocol behavior simulation (TLS, QUIC, WebRTC, SSH, HTTP/2, HTTP/3)

#### Operational Features
- Multi-client support in server mode
- Real-time statistics reporting with configurable intervals
- Batch packet processing for improved throughput
- Socket buffer optimization for high-speed operation
- Graceful degradation when enhanced modules unavailable

### Performance
- **Throughput**: 8-10 Mbps sustained rate (112-125% efficiency)
- **Stability**: Consistent performance over extended periods
- **CPU Usage**: Optimized with batch processing and selective enhancement
- **Memory**: ~50MB typical usage
- **Latency**: Minimal added delay with adaptive timing

### Configuration
- Command-line interface with extensive options
- Docker support with included Dockerfile
- Systemd service configuration examples
- Integration examples with VPN solutions (WireGuard)

### Testing
- Comprehensive test suite with automated testing
- Performance benchmarking tools
- Real data transmission verification
- Progress monitoring during tests

### Documentation
- Complete README with usage examples and best practices
- Performance benchmarks and optimization tips
- Troubleshooting guide
- Security considerations documentation

### Security Features
- Designed to defeat heuristic and ML-based traffic analysis
- Timing correlation attack resistance
- Size-based traffic analysis prevention
- Continuous pattern variation to prevent fingerprinting

## [Unreleased]

### Planned
- Traffic-aware adaptive mode with tunnel interface monitoring
- Aggregate profile validation and real-time adjustment
- Upload/download ratio compensation
- Realistic session scheduling with idle periods
- Performance optimization with Cython/Rust modules
- Additional protocol profiles
- Built-in traffic analysis tools
