# Systemd Installation

The unit templates run the authenticated UDP server and client. They do not
provide encryption or multiplexing; deploy them only within the intended external
encrypted transport.

## Install Files

```bash
sudo useradd --system --home-dir /nonexistent --shell /usr/sbin/nologin \
  traffic-masking
sudo install -d -o root -g root -m 0755 \
  /opt/traffic-masking /etc/traffic-masking
sudo install -m 0755 ../traffic_masking_server.py /opt/traffic-masking/
sudo install -m 0755 ../traffic_masking_client.py /opt/traffic-masking/
sudo install -m 0644 ../control_protocol.py /opt/traffic-masking/
sudo install -m 0644 ../masking_lib.py /opt/traffic-masking/
sudo install -m 0644 ../observer_metrics.py /opt/traffic-masking/

sudo install -m 0644 traffic-masking-server.service /etc/systemd/system/
sudo install -m 0644 traffic-masking-client.service /etc/systemd/system/
```

The runtime uses only the Python standard library.

## Install The Shared Key

Generate the key on one endpoint, transfer the same binary file securely to the
other endpoint, and leave the installed source readable only by root:

```bash
umask 077
openssl rand 32 > control.psk
sudo install -o root -g root -m 0400 control.psk \
  /etc/traffic-masking/control.psk
```

The units load this file through `LoadCredential=` and pass the protected runtime
copy at `%d/control.psk` to the process. Never place the key value in a unit
command or environment variable.

## Configure

The server template uses the experimental `mixed` native profile with random
padding and an 80 Mbps aggregate cap.

Set the client server address with a drop-in:

```bash
sudo systemctl edit traffic-masking-client.service
```

```ini
[Service]
Environment="SERVER_IP=192.0.2.10"
```

Rate-mode server override example:

```bash
sudo systemctl edit traffic-masking-server.service
```

```ini
[Service]
ExecStart=
ExecStart=/usr/bin/python3 /opt/traffic-masking/traffic_masking_server.py --host 0.0.0.0 --port 8888 --shape-mode rate --min-mbps 2 --max-mbps 8 --max-total-mbps 40 --psk-file %d/control.psk
```

## Start

```bash
sudo systemctl daemon-reload
sudo systemd-analyze verify \
  /etc/systemd/system/traffic-masking-server.service \
  /etc/systemd/system/traffic-masking-client.service
sudo systemctl enable --now traffic-masking-server.service
sudo systemctl enable --now traffic-masking-client.service
```

Use the server unit on the emitting endpoint and the client unit on receiving
endpoints as appropriate for the deployment.

Both units use `/usr/bin/python3`, run as the dedicated unprivileged
`traffic-masking` account, and allow five seconds for the process to handle
SIGTERM and join its workers.

## Inspect

```bash
sudo systemctl status traffic-masking-server.service
sudo systemctl status traffic-masking-client.service
sudo journalctl -u traffic-masking-server.service -f
sudo journalctl -u traffic-masking-client.service -f
```

Server statistics label total and per-client framed application rates. Client
statistics label client-total receive/transmit rates and the observed uplink
ratio.

## Rotate The Key

There is no multi-key grace period. Stop both endpoints, atomically replace the
key file with the same new value and restrictive mode, then restart both units.
