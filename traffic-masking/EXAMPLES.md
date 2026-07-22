# Traffic Masking Examples

All examples require the same restrictive PSK file on the server and client:

```bash
umask 077
openssl rand 32 > traffic-masking.psk
```

Run these UDP processes only inside the intended external encrypted transport.
Direct raw-UDP use is suitable for local diagnostics, not confidentiality.

## Fixed Rate

```bash
python traffic_masking_server.py \
  --host 0.0.0.0 --port 8888 \
  --shape-mode rate --mbps 5 \
  --max-clients 4 --max-total-mbps 20 \
  --psk-file ./traffic-masking.psk

python traffic_masking_client.py \
  --server SERVER_IP --port 8888 \
  --psk-file ./traffic-masking.psk
```

The 5 Mbps target is per validated client. The server-wide cap is 20 Mbps.

## Floating Rate

```bash
python traffic_masking_server.py \
  --shape-mode rate --min-mbps 2 --max-mbps 8 \
  --max-total-mbps 16 \
  --psk-file ./traffic-masking.psk
```

Each client receives an independent bounded rate sequence.

## Native Profile With Padding

```bash
python traffic_masking_server.py \
  --shape-mode profile --profile web --max-mbps 4 \
  --padding fixed_buckets \
  --psk-file ./traffic-masking.psk

python traffic_masking_client.py \
  --server SERVER_IP --response 0.05 --padding random \
  --psk-file ./traffic-masking.psk
```

Profile timings and event sizes are experimental. The cap only delays offered
load above 4 Mbps; it does not force the profile to reach 4 Mbps.

## Local Diagnostic

```bash
python traffic_masking_server.py \
  --host 127.0.0.1 --mbps 1 --insecure-diagnostic

python traffic_masking_client.py \
  --server 127.0.0.1 --insecure-diagnostic
```

The diagnostic mode uses no secret. Keep it on an isolated local interface.

## Short Timing Values For Testing

```bash
python traffic_masking_client.py \
  --server 127.0.0.1 \
  --keepalive-interval 0.5 \
  --keepalive-jitter 0 \
  --receive-timeout 2 \
  --reconnect-delay-min 0.2 \
  --reconnect-delay-max 1 \
  --psk-file ./traffic-masking.psk
```

The receive timeout must exceed the maximum jittered keepalive interval.

## Environment Defaults

```bash
TRAFFIC_MASKING_KEEPALIVE_INTERVAL=2 \
TRAFFIC_MASKING_RECEIVE_TIMEOUT=8 \
TRAFFIC_MASKING_RECONNECT_DELAY_MIN=0.5 \
TRAFFIC_MASKING_RECONNECT_DELAY_MAX=10 \
TRAFFIC_MASKING_STATS_INTERVAL=2 \
python traffic_masking_client.py \
  --server SERVER_IP --psk-file ./traffic-masking.psk
```

Set `TRAFFIC_MASKING_KEEPALIVE_JITTER` to override the default fractional jitter
of `0.2`.

## Docker

```bash
VERSION="$(cat .version)"
docker build --build-arg VERSION="$VERSION" -t "traffic-masking:$VERSION" .

docker run --network host \
  --mount type=bind,src="$PWD/traffic-masking.psk",dst=/run/secrets/traffic-masking.psk,readonly \
  "traffic-masking:$VERSION" traffic_masking_server.py \
  --shape-mode profile --profile mixed --max-mbps 8 --padding random \
  --psk-file /run/secrets/traffic-masking.psk
```

With rootless Podman, use the same command as `podman run` and add
`--userns=keep-id:uid=1000,gid=1000` so container UID 1000 can read the
host-owned mode `0600` PSK.

## Monitoring

```bash
sudo tcpdump -i any -n udp port 8888 -c 100
grep "Total Rate:" server.log
grep "Per-client:" server.log
grep "Uplink ratio:" client.log
grep '^\[SNAPSHOT\] ' structured.log
```

The log commands inspect application counters. The direct UDP capture is useful
for diagnostics but does not represent an enclosing encrypted transport; capture
that transport separately at the declared observer boundary.
