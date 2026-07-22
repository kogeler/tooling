# Changelog

All notable changes to Traffic Masking will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2.0.0] - 2026-07-22

This release replaces the previously advertised advanced stack with one tested,
authenticated cover-traffic path. Historical entries below describe older
releases and are not claims about the current implementation.

### Security

- Added a versioned, length-checked UDP control and DATA protocol authenticated
  with HMAC-SHA256 and direction-specific session keys.
- Added source-bound expiring challenge cookies, replay protection, bounded
  pre-validation replies, handshake limits, client limits, and an aggregate
  egress cap.
- Production mode now requires a restrictive 32-4096 byte PSK file. The explicit
  `--insecure-diagnostic` mode uses a public built-in HMAC key and is only for
  isolated testing.
- Unknown, malformed, wrong-key, and replayed datagrams cannot enroll a cover
  traffic destination.

### Changed

- Split shaping into explicit `rate` and `profile` modes. Fixed/floating rate
  values are per validated client; profile `--max-mbps` is a ceiling only.
- Defined Mbps as decimal framed application-datagram throughput and made MTU,
  packetization, padding, per-client pacing, and aggregate pacing account the
  same byte layer.
- Replaced boundary-snapping rate patterns with independent slope-limited rate
  state per client and round-robin service under the server-wide cap.
- Changed the default client response ratio to `0.0`. Nonzero response traffic,
  DATA framing, padding, and keepalives now share one measured uplink budget.
- Added configurable health/reconnect timings, immutable runtime snapshots,
  optional JSON statistics, synchronized state, and bounded SIGINT/SIGTERM
  shutdown of non-daemon workers.
- Reduced the runtime to the Python standard library and updated the container
  base to Python 3.14 Alpine running as an unprivileged user.

### Removed

- Removed `--advanced`, `--header`, `--entropy`, and `--uplink-profile` without a
  compatibility parser. Configurations containing them now fail as invalid.
- Removed the unconnected enhanced timing, correlation, entropy, state-machine,
  and ML-resistance modules and their unsupported security claims.
- Removed the separately executable legacy test runners; all tests are native
  pytest tests with bounded live-process coverage.

### Operations

- Added hardened systemd templates using credential files and documented PSK
  rotation by coordinated stop, replacement, and restart.
- Added observer-trace metrics with explicit capture point, direction, connection,
  byte layer, and encapsulation overhead. Packet acquisition and outer encrypted
  multiplex validation remain deployment responsibilities.
- Existing service definitions must use the current rate/profile CLI, provide a
  PSK file, then be reloaded and restarted. No state or data migration is needed.

## [1.0.4] - 2025-02-12

### Fixed
- Client no longer falsely reports "Reconnected successfully" when server is unreachable
- Connection status now reflects actual data flow, not just successful UDP send
- Exponential backoff now works correctly (was resetting every cycle due to false success)

### Changed
- Reconnection logic now waits for actual server response before confirming connection
- Keepalive is only sent when connected; on timeout the client enters reconnect loop first
- All Russian comments in source code translated to English

### Improved
- Reconnection test now validates no false reconnection reports during server downtime
- Reconnection test verifies client shows `disconnected` status while server is down
- Reconnection test requires `Reconnected successfully` message only after real server recovery

## [1.0.3] - 2025-02-12

### Fixed
- Client now automatically reconnects after server restart or network loss
- Previously, the client sent `INIT_CLIENT` only once at startup; after server restart the client was permanently forgotten and traffic never resumed

### Added
- Periodic keepalive packets (every 5s) to maintain server registration
- Connection loss detection (10s receive timeout)
- Automatic reconnection with exponential backoff (1s to 30s)
- Socket timeout on client to prevent indefinite blocking in receive loop
- Connection status (`connected`/`disconnected`) in client stats output
- Reconnection test (`run_reconnection_test`) in the test suite — 3-phase test covering initial connection, server kill, and recovery after server restart

## [1.0.1] - 2024-12-20

### Added
- Rate limiting patterns
- A test script for rate limiting patterns

## [1.0.0] - 2024-12-20

### Added
- Initial UDP cover-traffic server and client.
- Floating rate mode with configurable minimum and maximum traffic rates
- Bidirectional response traffic and experimental web, video, VoIP, file,
  gaming, and mixed profiles.
- Payload padding, application packetization, multi-client operation, runtime
  statistics, Docker packaging, and systemd examples.
