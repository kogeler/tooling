# Traffic Masking Repository Guide

## Architecture

This project is an experimental authenticated UDP cover-traffic generator. The
server and client are expected to run beside a user tunnel and have both streams
multiplexed into the same external encrypted transport.

The enclosing multiplexer is not part of this repository. Direct UDP execution
is useful for tests and diagnostics but exposes a separate flow and plaintext
application framing.

## Current Contracts

- `rate` mode targets fixed or bounded floating framed-byte rates per validated
  client.
- `profile` mode preserves native handcrafted event sizes and gaps; an optional
  maximum is a ceiling only.
- A `ShapeEvent` becomes padded logical bytes, then `Packetizer` fragments it,
  then rate limiters account successfully submitted framed datagrams.
- Each validated client owns independent generator, RNG, pacing, and counters.
- The server-wide limiter caps aggregate egress with round-robin service.
- Client response accounting includes DATA framing, padding, and keepalives.
- Production enrollment requires a restrictive PSK file. Diagnostic mode is
  explicit and uses no secret.

## Claims Boundary

The traffic profiles are handcrafted and are not reference-backed models. Do not
describe them as statistically indistinguishable from normal traffic or as proven
resistance to traffic analysis.

Payload contents are opaque cover bytes intended for an encrypted outer
transport. Internal plaintext formats are not useful evidence about the observer
boundary. Only transformations with a defined effect on submitted byte volume or
timing belong in the runtime path.

Cover traffic can fill a volume deficit. It cannot remove user bytes, cancel a
spike, or guarantee a target aggregate when user traffic already exceeds it.

## Development

- Keep rate units as decimal Mbit/s of successfully submitted framed application
  datagrams.
- Keep stochastic tests deterministic through injected clocks and RNGs.
- Preserve the PSK, anti-amplification, sequence, MTU, per-client, and aggregate
  cap tests when changing the data path.
- Derive runtime rates from consecutive structured snapshots; do not parse logs
  when a snapshot is available to a test.
- Treat observer traces as external measurements with explicit capture point,
  connection ID, direction, byte layer, and encapsulation overhead.
- Run `make test-fast`, `make lint`, and `make test-live` for changes that affect
  process or network behavior.
- Runtime code must remain importable with only the standard library.
