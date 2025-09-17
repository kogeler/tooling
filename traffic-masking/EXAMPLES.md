# Traffic Masking System - Examples

## Quick Start

```bash
# Test the system
python test_traffic_masking.py --quick

# Basic server and client
python traffic_masking_server.py --mbps 5
python traffic_masking_client.py --server 127.0.0.1 --response 0.3

# Floating rate (recommended)
python traffic_masking_server.py --min-mbps 2 --max-mbps 8 --advanced
python traffic_masking_client.py --server 127.0.0.1 --response 0.3 --advanced
```

## Use Cases

### Mask Video Calls
```bash
# Google Meet / Zoom / Teams
python traffic_masking_server.py --min-mbps 1 --max-mbps 5 --advanced --profile video

# WhatsApp / Telegram voice calls
python traffic_masking_server.py --min-mbps 0.5 --max-mbps 1.5 --advanced --profile voip
```

### Mask Web Browsing
```bash
python traffic_masking_server.py --min-mbps 1 --max-mbps 4 --advanced --profile web --header quic
```

### Maximum Security Configuration
```bash
# Server
python traffic_masking_server.py \
  --min-mbps 3 --max-mbps 10 \
  --advanced --profile mixed \
  --header rtp --padding random \
  --entropy 1.0

# Client
python traffic_masking_client.py \
  --server SERVER_IP --response 0.4 \
  --advanced --uplink-profile mixed \
  --header rtp --padding random
```

### Performance Optimized
```bash
# Lower CPU usage, good throughput
python traffic_masking_server.py \
  --mbps 8 --advanced --profile web \
  --header none --padding none --entropy 0.7
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
docker run -d --name tm-server --network host traffic-masking \
  traffic_masking_server.py --min-mbps 2 --max-mbps 8 --advanced

# Client
docker run -d --name tm-client --network host traffic-masking \
  traffic_masking_client.py --server SERVER_IP --response 0.3 --advanced
```

### Docker Compose
```yaml
version: '3.8'
services:
  server:
    build: .
    network_mode: host
    command: traffic_masking_server.py --min-mbps 2 --max-mbps 8 --advanced
    restart: unless-stopped

  client:
    build: .
    network_mode: host
    command: traffic_masking_client.py --server ${SERVER_IP} --response 0.3 --advanced
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
sudo nice -n -10 python traffic_masking_server.py --min-mbps 5 --max-mbps 15 --advanced

# CPU affinity (cores 0,1)
taskset -c 0,1 python traffic_masking_server.py --min-mbps 5 --max-mbps 15 --advanced
```

### PyPy for Better Performance
```bash
sudo apt-get install pypy3
pypy3 -m pip install numpy
pypy3 traffic_masking_server.py --min-mbps 5 --max-mbps 15 --advanced
```

## Monitoring

```bash
# Traffic analysis
sudo tcpdump -i any -n udp port 8888 -c 100
sudo iftop -i eth0 -f "udp port 8888"

# Process monitoring
htop
pidstat -p $(pgrep -f traffic_masking_server) 1

# Extract rates from logs
grep "Rate:" server.log | awk '{print $4}' | sort -n

# Average rate
grep "Rate:" server.log | awk '{sum+=$4; count++} END {print sum/count}'
```

## Troubleshooting

```bash
# Test connectivity
nc -u -v SERVER_IP 8888
echo "TEST" | nc -u -w1 SERVER_IP 8888

# Debug mode with verbose output
PYTHONUNBUFFERED=1 python -u traffic_masking_server.py \
  --mbps 5 --advanced --stats-interval 1 2>&1 | tee server.log

# Network statistics
netstat -su | grep -A 5 Udp:
ss -u -a -n | grep 8888
```
