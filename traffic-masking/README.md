# Traffic Masking System

UDP-based cover traffic generator designed to mask traffic patterns inside encrypted tunnels (VPN/WireGuard) and defeat traffic analysis including ML-based detection.

## Features

- **Dynamic traffic patterns**: CBR, burst, wave, random walk, media-like
- **Floating rate mode**: Smooth traffic variations between min/max bounds
- **High throughput**: 8-10 Mbps sustained rate
- **ML resistance**: Advanced obfuscation techniques
- **Protocol mimicry**: 6 traffic profiles (web, video, voip, file, gaming, mixed)
- **Bidirectional flow**: Adaptive client uplink response

## Installation

```bash
python3 -m venv venv
source venv/bin/activate  # Linux/macOS
pip install -r requirements.txt
```

## Quick Start

### Basic Usage

```bash
# Server with fixed rate
python traffic_masking_server.py --mbps 5

# Server with floating rate (recommended)
python traffic_masking_server.py --min-mbps 2 --max-mbps 8

# Client
python traffic_masking_client.py --server <SERVER_IP> --response 0.3
```

### Advanced Mode

```bash
# Server with full obfuscation
python traffic_masking_server.py \
  --min-mbps 3 --max-mbps 10 \
  --advanced --profile mixed \
  --header rtp --padding random

# Client with matching configuration
python traffic_masking_client.py \
  --server <SERVER_IP> --response 0.3 \
  --advanced --uplink-profile mixed
```

## Testing

```bash
# Run complete test suite
python test_traffic_masking.py

# Quick 10-second test
python test_traffic_masking.py --quick
```

## Key Parameters

- `--mbps`: Fixed target rate in Mbps
- `--min-mbps/--max-mbps`: Floating rate range (more realistic)
- `--advanced`: Enable ML-resistant features
- `--profile`: Traffic pattern (web/video/voip/file/gaming/mixed)
- `--response`: Client uplink ratio (0.0-1.0)
- `--header`: Pseudo-headers (none/rtp/quic)
- `--padding`: Padding strategy (none/random/fixed_buckets/progressive)
- `--entropy`: Payload entropy (0.0-1.0)

## Documentation

- [Examples](EXAMPLES.md) - Usage examples and deployment scenarios
- [Technical Summary](SUMMARY.md) - Implementation details and architecture
- [Changelog](CHANGELOG.md) - Version history

## Docker

```bash
docker build -t traffic-masking .
docker run --network host traffic-masking traffic_masking_server.py --min-mbps 2 --max-mbps 8
```

## Systemd

See [systemd/](systemd/) directory for service unit files.

## License

Apache-2.0