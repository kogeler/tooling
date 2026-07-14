# Changelog

## [1.2.0] — 2026-07-14

### Added
- Confirmation window for IP changes: a new external IP must be observed in
  `CF_DDNS_CONFIRM_CYCLES` (default 2) consecutive checks before DNS is
  written; first-run creation included.
- Periodic reconciliation (`CF_DDNS_RECONCILE_INTERVAL`, default 3600s):
  repairs external record edits, deletions, and id changes; ttl/proxied config
  drift is converged at startup.
- Graceful shutdown on SIGTERM/SIGINT (PID-1 safe): interruptible waits, no
  new HTTP calls or DNS mutations after the signal, sessions and metrics
  server closed; container stops in well under a second.
- Failure policy: permanent API refusals (auth/validation) and ambiguous
  record ownership exit immediately; transient failures exit after the
  `CF_DDNS_MAX_FAILURES` (default 10) consecutive-failure budget.
- New environment variables: `CF_DDNS_MAX_FAILURES`,
  `CF_DDNS_RECONCILE_INTERVAL`, `CF_DDNS_CONFIRM_CYCLES`,
  `CF_DDNS_METRICS_ADDR`.
- New metrics: `cf_ddns_ip_changes_total` (real IP changes only — the
  restart-safe alerting signal), `cf_ddns_unconfirmed_ip_readings_total`,
  `cf_ddns_record_modified_timestamp_seconds`, `cf_ddns_build_info{version}`.
- Dockerfile: `EXPOSE 9101` and a liveness `HEALTHCHECK` on the metrics
  endpoint.
- `Makefile` (`venv`/`test`/`lint`/`clean`) and a pytest-native test suite
  with branch coverage; `ruff` linting.
- README: restart-safe Prometheus alerting rules, hardened docker-compose
  example, AMBIGUOUS runbook.

### Changed
- Record writes use PATCH instead of PUT, sending only owned fields — record
  comments, tags, and settings survive updates.
- Cloudflare API errors are classified (permanent / gone / exists / ambiguous
  / transient): permanent 4xx are no longer retried; 429 honors `Retry-After`
  within a 60s budget; retries use backoff with jitter over persistent HTTP
  sessions.
- The Cloudflare bearer token is isolated to a dedicated session and is never
  sent to check-IP services; check-IP responses are size-capped (64 bytes)
  and strictly validated.
- Retrieved IPs must be globally routable unicast IPv4 — private, loopback,
  link-local, CGNAT, multicast, reserved, and broadcast ranges are rejected,
  as are malformed forms (leading zeros, whitespace, signs).
- Strict configuration validation: `CF_DDNS_PROXIED` accepts only
  `true`/`false`; TTL accepts `1` (Auto) or 30–86400 and is forced to Auto for
  proxied records; the hostname is IDNA-normalized and label-validated.
- `cf_ddns_ip_info` is a single series (the current managed IP); old-IP
  series are removed instead of being kept at 0.
- `cf_ddns_last_ip_update_timestamp_seconds` now strictly means "last write
  by this process"; provider state is exposed separately.
- Base image bumped to `python:3.14-alpine`; the module was renamed
  `cf-ddns.py` → `cf_ddns.py`.

### Fixed
- Transient Cloudflare API errors could trigger the creation of duplicate
  A records (public DNS round-robin between a dead and a live IP) or a
  permanent create-failure loop: a record is now created only after a
  confirmed "no record exists" read, and create is never blindly retried.
- An invalid token or zone id caused an infinite retry loop; it now exits 1
  with a clear message.
- The container ignored SIGTERM as PID 1 and was always SIGKILLed after the
  stop grace period.
- Deleted-record detection relied on an unreachable code path checking the
  wrong Cloudflare error code (81058 instead of 81044).
- Metrics used `prometheus_client` private internals that could break on
  library upgrades; a busy metrics port produced a raw traceback instead of a
  clean error.

### Removed
- `run_tests.sh` (replaced by the Makefile).
- `TEST_DOCUMENTATION.md` (still-relevant content merged into README).
