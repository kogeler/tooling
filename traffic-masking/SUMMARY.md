# Traffic Masking Technical Summary

## Scope

The project generates an authenticated UDP cover stream. It does not implement
the encrypted transport that must multiplex cover bytes with user traffic. Raw
UDP output is separately observable and unencrypted.

No reference dataset currently establishes that the generated aggregate is
statistically indistinguishable from legitimate traffic.

## Components

`control_protocol.py` provides:

- versioned, length-checked control and DATA framing;
- HMAC-SHA256 authentication and direction-specific session keys;
- source-bound challenge cookies and bounded pre-validation replies;
- monotonic sequence validation and restrictive PSK loading.

`masking_lib.py` provides:

- `ShapeEvent` for logical volume and intended gaps;
- `Packetizer` for final framed application datagram ceilings;
- `RateLimiter` for reservation/commit accounting of submitted bytes;
- `FloatingRate` for bounded per-client slope-limited rates;
- `RatioBudget` for framed uplink/downlink accounting;
- `PayloadPadder` for explicit observable volume addition;
- experimental handcrafted profile event generators.

`traffic_masking_server.py` provides:

- authenticated client enrollment;
- independent generator, RNG, limiter, and counters per client;
- fixed/floating rate mode and native profile mode;
- round-robin scheduling under a server-wide egress limiter;
- explicitly labelled total and per-client metrics.

`traffic_masking_client.py` provides:

- authenticated enrollment and server-source validation;
- configurable response-ratio accounting;
- keepalive, health timeout, and exponential reconnect handling;
- monotonic receive-rate windows and client-total metrics.

`observer_metrics.py` provides:

- a validated external trace event with explicit direction, connection, capture
  point, outer datagram size, and encapsulation overhead;
- fixed monotonic windows and idle-gap distributions;
- outer/inner direction ratios, burst summaries, and size autocorrelation.

The observer module consumes an existing capture. Packet acquisition and mapping
inner application datagrams to outer transport datagrams remain deployment
responsibilities.

## Shaping Modes

In `rate` mode, `--mbps` selects a fixed per-client target. A
`--min-mbps/--max-mbps` pair selects a bounded floating target. The server keeps
enough logical demand available and limiters pace final framed bytes.

In `profile` mode, `--profile` selects native event sizes and gaps.
`--max-mbps` is optional and only caps the offered load. Profiles are
handcrafted experimental inputs, not measured baselines.

## Data Units

1. A `ShapeEvent` declares logical byte volume and the gap after the event.
2. Optional padding adds byte volume with an explicit strategy.
3. `Packetizer` splits the result so authenticated UDP datagrams fit `--mtu`.
4. Per-client and aggregate limiters account the complete framed datagram.

Mbps values are decimal application rates. IP, UDP, and enclosing encrypted
transport overhead require a separate observer measurement.

Both processes expose immutable structured snapshots. Human-readable logs derive
instantaneous rates from consecutive snapshots rather than cumulative averages.
Their sockets and session counters are synchronized, and SIGINT/SIGTERM trigger a
bounded join of non-daemon workers.

## Security Boundary

The control protocol prevents arbitrary unauthenticated destinations from being
enrolled for cover volume. It does not encrypt the UDP payload. Deployment
confidentiality and flow aggregation depend on the external encrypted transport.

Cover traffic can add bytes but cannot cancel a real traffic spike or repair an
already excessive direction ratio. Statistical effectiveness must be evaluated
at the aggregate observer boundary with declared captures and metrics.
