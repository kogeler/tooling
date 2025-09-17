# Systemd Service Units

This directory contains systemd service units for running the Traffic Masking System as system services.

## Installation

1. Copy service files to systemd directory:
```bash
sudo cp traffic-masking-*.service /etc/systemd/system/
```

2. Create working directory and copy application files:
```bash
sudo mkdir -p /opt/traffic-masking
sudo cp ../*.py /opt/traffic-masking/
sudo cp -r ../enhanced /opt/traffic-masking/
sudo chown -R nobody:nogroup /opt/traffic-masking
```

3. Install Python dependencies:
```bash
sudo python3 -m venv /opt/traffic-masking/venv
sudo /opt/traffic-masking/venv/bin/pip install numpy
```

4. Update service files to use venv Python:
```bash
sudo sed -i 's|/usr/bin/python3|/opt/traffic-masking/venv/bin/python|g' \
    /etc/systemd/system/traffic-masking-*.service
```

## Configuration

### Server Configuration

The server service is configured with maximum security features:
- Floating rate: 3-10 Mbps
- Advanced mode with ML resistance
- RTP headers and random padding
- Maximum entropy (1.0)

To modify server settings, create a drop-in override:
```bash
sudo systemctl edit traffic-masking-server.service
```

Example override to change rate:
```ini
[Service]
ExecStart=
ExecStart=/opt/traffic-masking/venv/bin/python /opt/traffic-masking/traffic_masking_server.py \
    --min-mbps 1 --max-mbps 5 --advanced --profile video
```

### Client Configuration

The client connects to localhost by default. To connect to a remote server:

1. Create drop-in override:
```bash
sudo systemctl edit traffic-masking-client.service
```

2. Add server IP:
```ini
[Service]
Environment="SERVER_IP=192.168.1.100"
```

## Usage

### Start Services
```bash
# Server only
sudo systemctl start traffic-masking-server.service

# Client only
sudo systemctl start traffic-masking-client.service

# Both
sudo systemctl start traffic-masking-server.service traffic-masking-client.service
```

### Enable at Boot
```bash
sudo systemctl enable traffic-masking-server.service
sudo systemctl enable traffic-masking-client.service
```

### Check Status
```bash
sudo systemctl status traffic-masking-server.service
sudo systemctl status traffic-masking-client.service
```

### View Logs
```bash
# Follow server logs
sudo journalctl -u traffic-masking-server.service -f

# Follow client logs
sudo journalctl -u traffic-masking-client.service -f

# Last 100 lines
sudo journalctl -u traffic-masking-server.service -n 100
```

### Stop Services
```bash
sudo systemctl stop traffic-masking-server.service traffic-masking-client.service
```

## Security Features

Both services include security hardening:
- Run as `nobody:nogroup` user
- Private /tmp directory
- Read-only system directories
- No new privileges
- Resource limits

## Troubleshooting

### Service Won't Start
- Check logs: `sudo journalctl -u traffic-masking-server.service -e`
- Verify Python path: `which python3`
- Check permissions: `ls -la /opt/traffic-masking/`

### High CPU Usage
- Reduce `--entropy` to 0.5-0.7
- Use `--padding none`
- Lower max rate in floating mode

### Connection Issues
- Check firewall: `sudo ufw status`
- Verify server is listening: `sudo ss -ulnp | grep 8888`
- Test connectivity: `nc -u -v SERVER_IP 8888`
