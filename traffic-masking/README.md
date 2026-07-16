# Traffic Masking System

UDP-based cover traffic generator designed to mask traffic patterns inside encrypted tunnels and defeat traffic analysis including ML-based detection.

## Features

- **Dynamic traffic patterns**: CBR, burst, wave, random walk, media-like
- **Floating rate mode**: Smooth traffic variations between min/max bounds
- **High throughput**: 8-10 Mbps sustained rate
- **ML resistance**: Advanced obfuscation techniques
- **Protocol mimicry**: 6 traffic profiles (web, video, voip, file, gaming, mixed)
- **Bidirectional flow**: Adaptive client uplink response
- **Auto-reconnection**: Client recovers automatically after server restart or network loss
- **Authenticated enrollment**: Source-bound challenge cookies and HMAC-SHA256
  session framing prevent unauthenticated cover-traffic amplification

## Installation

```bash
python3 -m venv venv
source venv/bin/activate  # Linux/macOS
pip install -r requirements.txt
```

## Quick Start

Create one binary PSK and install the same file on both endpoints. Keep the file
out of source control and do not pass the key value on the command line.

```bash
umask 077
openssl rand 32 > traffic-masking.psk
```

### Basic Usage

```bash
# Server with fixed rate
python traffic_masking_server.py --mbps 5 --psk-file ./traffic-masking.psk

# Server with floating rate (recommended)
python traffic_masking_server.py --min-mbps 2 --max-mbps 8 \
  --psk-file ./traffic-masking.psk

# Client
python traffic_masking_client.py --server <SERVER_IP> \
  --psk-file ./traffic-masking.psk
```

### Experimental Profile Mode

Profile mode preserves each handcrafted profile's native event volumes and
gaps. `--max-mbps` is only a ceiling; it does not raise a low-rate profile to
the cap. These profiles remain experimental pending reference-trace validation.

```bash
# Server with native mixed-profile load and a 10 Mbps ceiling
python traffic_masking_server.py \
  --shape-mode profile --profile mixed --max-mbps 10 \
  --header rtp --padding random \
  --psk-file ./traffic-masking.psk

# Client with matching configuration
# A nonzero response is an explicit diagnostic/profile uplink choice.
python traffic_masking_client.py \
  --server <SERVER_IP> --response 0.3 \
  --advanced --uplink-profile mixed \
  --psk-file ./traffic-masking.psk
```

## Testing

```bash
# Run fast unit tests
make test-fast

# Run bounded live process/network tests
make test-live

# Run the complete pytest suite
make test
```

## Key Parameters

- `--shape-mode rate|profile`: Select an explicit offered-load contract. The
  default is `rate`.
- `--mbps`: Fixed target in decimal Mbps of authenticated application datagram
  bytes for rate mode (default 5)
- `--min-mbps/--max-mbps`: Floating range in rate mode
- `--profile`: Required experimental pattern in profile mode
- `--max-mbps`: In profile mode, an optional ceiling that only adds delay
- `--advanced`: Deprecated warning-emitting alias for profile mode
- `--response`: Optional diagnostic/profile uplink setting (0.0-1.0, default
  0.0). The client budgets successfully submitted framed uplink bytes as this
  fraction of authenticated downlink datagram bytes. DATA, framing, padding and
  keepalives share the budget; mandatory keepalives can create temporary debt.
- `--header`: Pseudo-headers (none/rtp/quic)
- `--padding`: Padding strategy (none/random/fixed_buckets/progressive)
- `--entropy`: Payload entropy (0.0-1.0)
- `--mtu`: Maximum application UDP datagram size after protocol framing and
  padding. This is application packetization, not IP fragmentation; account for
  IP and outer encrypted-transport overhead when selecting a path-safe value.
- `--psk-file`: Path to the shared 32-4096 byte binary key. The file must not
  grant group or other permissions.
- `--max-clients`, `--max-total-mbps`: Bound authenticated enrollment and actual
  aggregate server egress. The configured rate is per client; a round-robin
  global limiter shares a binding total cap between validated clients.
- `--max-handshakes-per-second`: Bound global handshake processing. Pending and
  replay state expires with the cookie window; full state refuses new enrollment
  rather than evicting an authenticated client.
- `--keepalive-interval`, `--keepalive-jitter`, `--receive-timeout`: Control
  client health checks. The receive timeout must exceed the maximum jittered
  keepalive interval.
- `--reconnect-delay-min`, `--reconnect-delay-max`: Bound exponential reconnect
  backoff.
- `--stats-interval`: Controls reporting on both endpoints. Server reports
  explicitly labelled total and per-client framed application-datagram rates.

Client timing defaults can also be set with
`TRAFFIC_MASKING_KEEPALIVE_INTERVAL`, `TRAFFIC_MASKING_KEEPALIVE_JITTER`,
`TRAFFIC_MASKING_RECEIVE_TIMEOUT`, `TRAFFIC_MASKING_RECONNECT_DELAY_MIN`, and
`TRAFFIC_MASKING_RECONNECT_DELAY_MAX`. `TRAFFIC_MASKING_STATS_INTERVAL` applies
to either endpoint. CLI values override environment defaults.

`--insecure-diagnostic` uses a public built-in key and is only for local
diagnostics. Production startup fails closed when the PSK is missing,
unreadable, too short, too large, or has permissive file modes.

## Key Rotation

There is no multi-key grace period. Generate a replacement file with mode
`0600`, stop both endpoints, atomically replace the old file on both hosts, and
restart both processes. Never log the key or put its value in a service command.

## Documentation

- [Examples](EXAMPLES.md) - Usage examples and deployment scenarios
- [Technical Summary](SUMMARY.md) - Implementation details and architecture
- [Changelog](CHANGELOG.md) - Version history

## Docker

```bash
docker build -t traffic-masking .
docker run --network host \
  --mount type=bind,src="$PWD/traffic-masking.psk",dst=/run/secrets/traffic-masking.psk,readonly \
  traffic-masking traffic_masking_server.py --shape-mode rate \
  --min-mbps 2 --max-mbps 8 \
  --psk-file /run/secrets/traffic-masking.psk
```

The mounted secret must be readable by container UID 1000 while retaining mode
`0400` or `0600` and no group/other permission bits.

## Systemd

See [systemd/](systemd/) directory for service unit files.

## License

Apache-2.0
