# Traffic Masking System - Technical Summary

## Architecture

### Core Components

**masking_lib.py**
- `stream_generator()`: Main traffic generation with fixed/floating rate support
- `DynamicObfuscator`: Packet obfuscation and fragmentation
- `ProtocolMimicry`: Pattern generation for different traffic profiles
- `TrafficProfile`: Enum for supported profiles (web, video, voip, file, gaming, mixed)

**traffic_masking_server.py**
- Multi-client UDP server with batch processing
- Adaptive rate control with floating mode
- Real-time statistics monitoring

**traffic_masking_client.py**
- Adaptive uplink generation based on downlink rate
- Response ratio control (0-100% of received traffic)

### Enhanced Modules (optional)
- `enhanced/timing.py`: Adaptive timing with congestion modeling
- `enhanced/correlation.py`: Markov chain-based size generation
- `enhanced/ml_resistance.py`: Adversarial packet generation
- `enhanced/entropy.py`: Realistic encrypted payload simulation
- `enhanced/state_machine.py`: Protocol state machines (TLS, QUIC, WebRTC, SSH, HTTP/2)

## Key Algorithms

### Floating Rate Algorithm
```python
# Physics-based smooth rate transitions
rate_acceleration = random.uniform(-0.5, 0.5)  # Major pattern changes
rate_velocity += rate_acceleration * dt
rate_velocity *= 0.95  # Natural damping
current_mbps += rate_velocity * dt

# Elastic boundaries
if current_mbps < min_mbps:
    current_mbps = min_mbps + elastic_bounce
    rate_velocity = abs(rate_velocity) * 0.5
```

- Pattern changes every 2-8 seconds
- Momentum-based transitions for realistic traffic
- Elastic collision at min/max boundaries

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