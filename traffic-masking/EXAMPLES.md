# Traffic Masking System - Examples

## Quick Start

```bash
umask 077
openssl rand 32 > traffic-masking.psk

# Test the system without live process/network tests
make test-fast

# Basic server and client
python traffic_masking_server.py --mbps 5 --psk-file ./traffic-masking.psk
python traffic_masking_client.py --server 127.0.0.1 --psk-file ./traffic-masking.psk

# Floating rate
python traffic_masking_server.py --shape-mode rate --min-mbps 2 --max-mbps 8 \
  --psk-file ./traffic-masking.psk
python traffic_masking_client.py --server 127.0.0.1 --response 0.3 --advanced \
  --psk-file ./traffic-masking.psk
```

Mbps values are decimal Mbit/s of application UDP payload. The client defaults
to no scheduled uplink (`--response 0.0`). A nonzero response is an explicit
diagnostic/profile choice. Its ratio covers successfully submitted DATA,
framing, padding and keepalive bytes relative to authenticated downlink datagram
bytes; mandatory keepalives can temporarily exceed the target and are repaid by
pausing DATA.

Server `rate` mode supplies demand to reach its configured target. Experimental
`profile` mode preserves native event sizes and gaps; `--max-mbps` only caps it.
Rates are per validated client. `--max-total-mbps` is an actual aggregate cap;
when it binds, validated clients share it in round-robin order.

Client health and reporting timings support CLI flags and environment defaults:

```bash
TRAFFIC_MASKING_KEEPALIVE_INTERVAL=2 \
TRAFFIC_MASKING_RECEIVE_TIMEOUT=8 \
TRAFFIC_MASKING_RECONNECT_DELAY_MIN=0.5 \
TRAFFIC_MASKING_RECONNECT_DELAY_MAX=10 \
TRAFFIC_MASKING_STATS_INTERVAL=2 \
python traffic_masking_client.py --server SERVER_IP \
  --psk-file ./traffic-masking.psk
```

`TRAFFIC_MASKING_KEEPALIVE_JITTER` sets the fractional jitter (default `0.2`).
Equivalent CLI flags override these defaults. The receive timeout must be
greater than `keepalive interval * (1 + jitter)`.

## Use Cases

### Mask Video Calls
```bash
# Google Meet / Zoom / Teams
python traffic_masking_server.py --shape-mode profile --max-mbps 5 \
  --profile video --psk-file ./traffic-masking.psk

# WhatsApp / Telegram voice calls
python traffic_masking_server.py --shape-mode profile --max-mbps 1.5 \
  --profile voip --psk-file ./traffic-masking.psk
```

### Mask Web Browsing
```bash
python traffic_masking_server.py --shape-mode profile --max-mbps 4 \
  --profile web --header quic --psk-file ./traffic-masking.psk
```

### Maximum Security Configuration
```bash
# Server
python traffic_masking_server.py \
  --shape-mode profile --max-mbps 10 \
  --profile mixed \
  --header rtp --padding random \
  --entropy 1.0 --psk-file ./traffic-masking.psk

# Client
python traffic_masking_client.py \
  --server SERVER_IP --response 0.4 \
  --advanced --uplink-profile mixed \
  --header rtp --padding random \
  --psk-file ./traffic-masking.psk
```

### Performance Optimized
```bash
# Lower CPU usage, good throughput
python traffic_masking_server.py \
  --shape-mode profile --max-mbps 8 --profile web \
  --header none --padding none --entropy 0.7 \
  --psk-file ./traffic-masking.psk
```

## Integration

### WireGuard
```ini
# /etc/wireguard/wg0.conf
[Interface]
PostUp = systemctl start traffic-masking-server
PreDown = systemctl stop traffic-masking-server
```

### Docker
```bash
# Build and run
docker build -t traffic-masking .

# Server
docker run -d --name tm-server --network host \
  --mount type=bind,src="$PWD/traffic-masking.psk",dst=/run/secrets/traffic-masking.psk,readonly \
  traffic-masking traffic_masking_server.py --shape-mode rate \
  --min-mbps 2 --max-mbps 8 --psk-file /run/secrets/traffic-masking.psk

# Client
docker run -d --name tm-client --network host \
  --mount type=bind,src="$PWD/traffic-masking.psk",dst=/run/secrets/traffic-masking.psk,readonly \
  traffic-masking traffic_masking_client.py --server SERVER_IP --response 0.3 \
  --advanced --psk-file /run/secrets/traffic-masking.psk
```

### Docker Compose
```yaml
version: '3.8'
services:
  server:
    build: .
    network_mode: host
    command: traffic_masking_server.py --shape-mode rate --min-mbps 2 --max-mbps 8 --psk-file /run/secrets/traffic-masking.psk
    volumes:
      - ./traffic-masking.psk:/run/secrets/traffic-masking.psk:ro
    restart: unless-stopped

  client:
    build: .
    network_mode: host
    command: traffic_masking_client.py --server ${SERVER_IP} --response 0.3 --advanced --psk-file /run/secrets/traffic-masking.psk
    volumes:
      - ./traffic-masking.psk:/run/secrets/traffic-masking.psk:ro
    restart: unless-stopped
    depends_on:
      - server
```

## Performance Tuning

### Network Buffers (Linux)
```bash
# Temporary
sudo sysctl -w net.core.rmem_max=134217728
sudo sysctl -w net.core.wmem_max=134217728

# Permanent
echo "net.core.rmem_max=134217728" | sudo tee -a /etc/sysctl.conf
echo "net.core.wmem_max=134217728" | sudo tee -a /etc/sysctl.conf
sudo sysctl -p
```

### Process Priority
```bash
# High priority
sudo nice -n -10 python traffic_masking_server.py --shape-mode rate \
  --min-mbps 5 --max-mbps 15 --psk-file ./traffic-masking.psk

# CPU affinity (cores 0,1)
taskset -c 0,1 python traffic_masking_server.py --shape-mode rate \
  --min-mbps 5 --max-mbps 15 --psk-file ./traffic-masking.psk
```

### PyPy for Better Performance
```bash
sudo apt-get install pypy3
pypy3 -m pip install numpy
pypy3 traffic_masking_server.py --shape-mode rate --min-mbps 5 --max-mbps 15 \
  --psk-file ./traffic-masking.psk
```

## Monitoring

```bash
# Traffic analysis
sudo tcpdump -i any -n udp port 8888 -c 100
sudo iftop -i eth0 -f "udp port 8888"

# Process monitoring
htop
pidstat -p $(pgrep -f traffic_masking_server) 1

# Total and per-client rates are labelled separately
grep "Total Rate:" server.log
grep "Per-client:" server.log
```

## Troubleshooting

```bash
# Test authenticated connectivity
python traffic_masking_client.py --server SERVER_IP \
  --psk-file ./traffic-masking.psk --stats-interval 1

# Debug mode with verbose output
PYTHONUNBUFFERED=1 python -u traffic_masking_server.py \
  --shape-mode rate --mbps 5 --stats-interval 1 \
  --psk-file ./traffic-masking.psk 2>&1 | tee server.log

# Network statistics
netstat -su | grep -A 5 Udp:
ss -u -a -n | grep 8888
```
