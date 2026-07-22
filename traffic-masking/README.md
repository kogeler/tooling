# Traffic Masking

Experimental authenticated UDP cover-traffic generator. The server emits a
controlled stream to validated clients; clients may return a configured fraction
of the received volume.

This repository does not implement an encrypted tunnel or multiplexer. A real
deployment must place both user traffic and this cover stream inside the same
external encrypted transport. Running the programs directly over the Internet
creates a separate, identifiable UDP flow and provides no payload confidentiality.

The handcrafted profiles have not been validated against reference captures.
This project does not claim statistical indistinguishability from legitimate
traffic or resistance to traffic analysis.

## Runtime Contracts

- Production enrollment uses a shared PSK, source-bound challenge cookies,
  authenticated session framing, and monotonic sequence numbers.
- Decimal Mbps means framed application UDP bytes successfully submitted by the
  process, converted with `1 Mbps = 1,000,000 bit/s`.
- Configured rates and profile caps are per validated client.
- `--max-total-mbps` applies a round-robin aggregate server egress cap.
- `--mtu` is the final application datagram ceiling after authenticated framing.
  IP, UDP, and enclosing transport overhead are outside this value.
- Payload padding adds observable volume before packetization. It is not a claim
  about the plaintext format inside the encrypted transport.

## Installation

The runtime uses only the Python standard library.

```bash
python3 -m venv venv
source venv/bin/activate
python traffic_masking_server.py --help
python traffic_masking_client.py --help
```

`requirements.txt` is the intentionally empty freeze of the runtime environment.
The canonical release version is stored in `.version`.

Create one binary PSK and install the same file on both endpoints:

```bash
umask 077
openssl rand 32 > traffic-masking.psk
```

The key must contain 32-4096 bytes and must not grant group or other access.

## Rate Mode

Rate mode supplies enough demand to target either a fixed per-client rate or a
bounded floating rate.

```bash
# Fixed 5 Mbps per validated client
python traffic_masking_server.py \
  --shape-mode rate --mbps 5 \
  --psk-file ./traffic-masking.psk

# Smooth bounded rate process between 2 and 8 Mbps per client
python traffic_masking_server.py \
  --shape-mode rate --min-mbps 2 --max-mbps 8 \
  --max-total-mbps 20 \
  --psk-file ./traffic-masking.psk

python traffic_masking_client.py \
  --server SERVER_IP \
  --psk-file ./traffic-masking.psk
```

## Profile Mode

Profile mode preserves the native logical sizes and gaps of a selected
handcrafted profile. `--max-mbps` is an optional ceiling; it does not raise a
profile's offered load to that value.

```bash
python traffic_masking_server.py \
  --shape-mode profile --profile mixed --max-mbps 8 \
  --padding random \
  --psk-file ./traffic-masking.psk

python traffic_masking_client.py \
  --server SERVER_IP --response 0.05 --padding random \
  --psk-file ./traffic-masking.psk
```

Available profiles are `web`, `video`, `voip`, `file`, `gaming`, and `mixed`.
Available padding strategies are `none`, `random`, `fixed_buckets`, and
`progressive`.

## Uplink Accounting

`--response` is the requested ratio of successfully submitted framed uplink
bytes to authenticated downlink datagram bytes. DATA framing, payload padding,
and keepalives share one budget. Mandatory keepalives may create temporary debt;
scheduled DATA pauses until received volume repays it. The default is `0.0`.

## Authentication

`--psk-file` is required on both endpoints. Missing, unreadable, short, large, or
permissively-mode files fail closed. The PSK is used for authentication, not
payload encryption; confidentiality still depends on the external transport.

`--insecure-diagnostic` uses a public built-in key. It is intended only for
isolated local diagnostics and remains subject to handshake, client, and rate
limits. `--max-clients` bounds validated sessions,
`--max-handshakes-per-second` bounds handshake work, and `--max-total-mbps`
bounds aggregate server egress.

## Timing And Metrics

Client health timings are configurable with:

- `--keepalive-interval` / `TRAFFIC_MASKING_KEEPALIVE_INTERVAL`
- `--keepalive-jitter` / `TRAFFIC_MASKING_KEEPALIVE_JITTER`
- `--receive-timeout` / `TRAFFIC_MASKING_RECEIVE_TIMEOUT`
- `--reconnect-delay-min` / `TRAFFIC_MASKING_RECONNECT_DELAY_MIN`
- `--reconnect-delay-max` / `TRAFFIC_MASKING_RECONNECT_DELAY_MAX`

`--stats-interval` or `TRAFFIC_MASKING_STATS_INTERVAL` controls reporting on
either endpoint. CLI values override environment defaults. Both endpoints report
instantaneous monotonic windows. Server logs label total and per-client rates
separately. `--stats-json` switches periodic output to `[SNAPSHOT]`-prefixed JSON
with cumulative counters, state, and the current monotonic window.

`MaskingTrafficServer.snapshot()` and `AdaptiveTrafficClient.snapshot()` return
immutable counter/state snapshots for tests and operational integrations. The
process workers are non-daemon threads; SIGINT and SIGTERM close the active socket
and join those workers with a bounded timeout.

## Observer Metrics

`observer_metrics.py` defines `ObserverEvent` for captures made at the external
observer boundary. Every event declares timestamp, direction, outer datagram
bytes, connection ID, capture point, and encapsulation overhead. Helpers compute
fixed windows, idle-gap distributions, direction ratios, burst summaries, and
size autocorrelation using either outer bytes or bytes after declared overhead.

The module analyzes supplied events; it does not capture packets or establish
that an application datagram maps one-to-one to an outer datagram. Operators must
collect the trace at the actual enclosing transport boundary.

## Testing

```bash
make test-fast
make lint
make test-live
make test
```

The live suite starts real loopback server/client processes and requires local
UDP sockets and process creation. On Linux with procfs it also verifies that each
process owns only its declared UDP socket. This application-level smoke does not
replace a capture at the enclosing encrypted transport boundary.

## Docker

```bash
VERSION="$(cat .version)"
docker build --build-arg VERSION="$VERSION" -t "traffic-masking:$VERSION" .
docker run --network host \
  --mount type=bind,src="$PWD/traffic-masking.psk",dst=/run/secrets/traffic-masking.psk,readonly \
  "traffic-masking:$VERSION" traffic_masking_server.py \
  --shape-mode rate --min-mbps 2 --max-mbps 8 \
  --psk-file /run/secrets/traffic-masking.psk
```

The mounted secret must be readable by container UID 1000 while retaining mode
`0400` or `0600` and no group/other permission bits. The build fails when its
version argument differs from `.version`. The image intentionally has no
healthcheck: process liveness would not prove authenticated data flow, while an
active protocol probe would create session state.

For rootless Podman, add `--userns=keep-id:uid=1000,gid=1000` to `podman run` so
the host-owned mode `0600` bind mount maps to the image user. With Docker Engine
without user-namespace remapping, ensure the mounted file is owned by numeric UID
1000; other mappings require an equivalent ownership adjustment.

## Systemd

See [systemd/](systemd/) for unit templates and installation notes.

## License

Apache-2.0
