# Traffic Masking System - Technical Summary

## Architecture

### Core Components

**masking_lib.py**
- `stream_generator()`: Main traffic generation with fixed/floating rate support
- `DynamicObfuscator`: Packet obfuscation and fragmentation
- `ShapeEvent`, `Packetizer`, `RateLimiter`: Explicit offered-load,
  application-packetization, and framed-byte pacing contracts
- `FloatingRate`: Bounded slope-limited per-client rate state
- `RatioBudget`: Successful framed uplink accounting against downlink bytes
- `ProtocolMimicry`: Pattern generation for different traffic profiles
- `TrafficProfile`: Enum for supported profiles (web, video, voip, file, gaming, mixed)

**control_protocol.py**
- Versioned binary envelope for control and data datagrams
- HMAC-SHA256 authentication with direction-specific session keys and monotonic
  sequences
- Stateless, source-bound challenge cookies with bounded pre-validation replies
- Restrictive PSK file validation

**traffic_masking_server.py**
- Multi-client UDP server with independent generator, limiter, RNG and counters
- Authenticated client enrollment with client and handshake-rate caps
- Round-robin per-client pacing under an actual aggregate egress limiter
- Explicit fixed/floating rate mode and experimental native profile mode
- Real-time statistics monitoring

**traffic_masking_client.py**
- Authenticated challenge/response handshake and source validation
- Monotonic receive-rate windows and configurable health/reconnect timings
- Response ratio control over DATA, framing, padding and control bytes

### Enhanced Modules (optional)
- `enhanced/timing.py`: Adaptive timing with congestion modeling
- `enhanced/correlation.py`: Markov chain-based size generation
- `enhanced/ml_resistance.py`: Adversarial packet generation
- `enhanced/entropy.py`: Realistic encrypted payload simulation
- `enhanced/state_machine.py`: Protocol state machines (TLS, QUIC, WebRTC, SSH, HTTP/2)

## Key Algorithms

### Floating Rate Algorithm
```python
# Low-pass random slope with midpoint reversion
desired_slope = midpoint_force + bounded_noise
slope += (desired_slope - slope) * elapsed / response_time
slope = clamp(slope, -max_slope, max_slope)
current_mbps += slope * elapsed

# Soft reflection avoids exact-boundary dwell
current_mbps = reflect_inside(current_mbps, min_mbps, max_mbps)
```

- Monotonic-clock updates with injected RNG for deterministic tests
- Bounded derivative and nonzero long-run variance
- Independent state and sequence for every validated client

### Performance Optimizations
- Batch processing: 10 packets per batch
- Selective enhancement: 10% of large packets use advanced features
- Adaptive delay adjustment based on actual vs target rate
- Socket buffer optimization (4MB send/receive)

## Traffic Profiles

| Profile | Characteristics | Use Case |
|---------|----------------|----------|
| web | Bursty with idle periods | HTTP/HTTPS browsing |
| video | Steady high rate with buffering | Streaming services |
| voip | Low steady rate, bidirectional | Voice calls |
| file | Maximum throughput bursts | Downloads/uploads |
| gaming | Low latency, small packets | Real-time games |
| mixed | Combination of patterns | General purpose |

## Obfuscation Techniques

1. **Padding Strategies**
   - Random: Variable padding 0-MTU
   - Fixed buckets: Quantized sizes (64, 128, 256, 512, 1024)
   - Progressive: Gradually increasing sizes

2. **Pseudo-headers**
   - RTP-like: 12-byte header mimicking RTP
   - QUIC-like: Variable header mimicking QUIC

3. **Entropy Control**
   - Adjustable payload randomness (0.0-1.0)
   - Pattern injection for protocol mimicry

## Performance Characteristics

- **Throughput**: 8-10 Mbps sustained
- **Efficiency**: 100-125% of target rate
- **Latency**: <1ms added delay in basic mode
- **CPU**: ~45% single core at 8 Mbps
- **Memory**: ~50MB typical usage

## Deployment Modes

### Basic Mode
- Simple traffic generation
- Minimal CPU usage
- No enhanced features

### Advanced Mode
- Full obfuscation stack
- ML resistance features
- Protocol state tracking
- Higher CPU usage

## Security Analysis

### Attack Resistance
- **Statistical Analysis**: Correlation breaking via Markov chains
- **Machine Learning**: Adversarial generation patterns
- **Timing Analysis**: Adaptive jitter and delays
- **Size Analysis**: Dynamic size distributions
- **Protocol Analysis**: State machine simulation

### Limitations
- No encryption (requires encrypted tunnel)
- Single-threaded (Python GIL)
- Detectable as cover traffic under deep inspection

## Integration Points

- **VPN**: PostUp/PreDown hooks
- **Docker**: Network host mode required for UDP
- **Systemd**: Service units for automatic startup
- **Monitoring**: Real-time statistics via stdout

## Future Improvements

- Multi-threading support
- Rust/C++ performance modules
- Distributed operation mode
- Cross-layer coordination with VPN
