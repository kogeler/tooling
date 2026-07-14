# cf-ddns — agent entry point

Plan status: stage 0 in progress

## What this is

A single-module Python service that keeps one Cloudflare DNS A record pointed at
this host's external IPv4 address. It polls public check-IP services, updates the
record through the Cloudflare v4 API when the address changes, and exposes
Prometheus metrics on an HTTP endpoint. It runs as a long-lived container process.

## File map

- `cf_ddns.py` — the entire service (config, API client, main loop, metrics).
- `test_ddns.py` — legacy test suite (being replaced in plan Stage 1).
- `requirements.txt` / `requirements-dev.txt` — pinned runtime / dev dependencies.
- `Makefile` — venv, test, lint, clean targets (see below).
- `Dockerfile` — `python:3.14-alpine`, non-root user, runs `cf_ddns.py`.
- `.version` — release version; must match the Dockerfile `LABEL version`.
- `tokens.sh` — **gitignored** real credentials for manual live checks against the
  dedicated test record; never commit, never echo its values.
- `TEST_DOCUMENTATION.md` — stale; scheduled for deletion in plan Stage 7.

## How to work on it

```sh
make venv    # create ./venv and install pinned deps (idempotent)
make test    # pytest with branch coverage
make lint    # ruff check
make clean   # remove venv and caches
# --format docker is required for podman to keep the HEALTHCHECK
# (OCI images do not support it); plain `docker build` keeps it natively
podman build --format docker -t cf-ddns cf-ddns/
```

Live manual check (mutates only the dedicated test host):
`. tokens.sh && ./venv/bin/python cf_ddns.py`

## Graceful shutdown

SIGTERM and SIGINT set a global shutdown event. Every wait (loop interval, retry
backoff, Retry-After) and every new HTTP attempt observes it; after the signal no
new request starts and no DNS mutation is issued. The shutdown bound is **one
in-flight read timeout (5s) plus a small margin** — a synchronous request already
on the wire cannot be cancelled, only awaited. Normal (interval-wait) stops
return in well under a second; the container's default 10s grace period is
always sufficient. Failures caused by the shutdown itself do not count toward
the failure budget, so a stop during degraded conditions still exits 0.

## Configuration (env vars)

| Variable | Required | Default | Meaning |
|---|---|---|---|
| `CF_DDNS_TOKEN` | yes | — | Cloudflare API token with DNS edit rights |
| `CF_DDNS_ZONE_ID` | yes | — | Cloudflare zone ID |
| `CF_DDNS_HOST` | yes | — | FQDN of the managed A record |
| `CF_DDNS_INTERVAL` | no | `10` | seconds between IP checks (≥1) |
| `CF_DDNS_TTL` | no | `120` | record TTL: `1` (Auto) or 30–86400; forced to `1` when proxied |
| `CF_DDNS_PROXIED` | no | `false` | strictly `true`/`false`; typos exit with an error |
| `CF_DDNS_LOGLEVEL` | no | `INFO` | DEBUG/INFO/WARNING/ERROR/CRITICAL |
| `CF_DDNS_METRICS_PORT` | no | `9101` | Prometheus endpoint port |
| `CF_DDNS_METRICS_ADDR` | no | `0.0.0.0` | Prometheus endpoint bind address (must be a valid IP) |
| `CF_DDNS_MAX_FAILURES` | no | `10` | consecutive-failure budget (IP retrieval / DNS updates) before exit 1 |
| `CF_DDNS_RECONCILE_INTERVAL` | no | `3600` | seconds between full DNS re-reads (external-drift repair); `0` disables |
| `CF_DDNS_CONFIRM_CYCLES` | no | `2` | consecutive readings required before a new IP is written; `1` = immediate |

## Ongoing refactor

This module is being reworked in staged commits. The authoritative plan (stage
ordering, frozen contracts, invariants, per-stage acceptance criteria) lives in
`plans/cf-ddns-review/cf-ddns-fix-plan.md` (local, gitignored); background findings
in `plans/cf-ddns-review/cf-ddns-review.md`. Follow the plan's execution rules —
stages are strictly sequential and each ends with green `make test` + clean
`make lint`.
