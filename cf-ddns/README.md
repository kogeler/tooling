# Cloudflare DDNS Updater

A single-module Python service that keeps one Cloudflare DNS A record pointed at
this host's external IPv4 address. It polls public check-IP services, writes
through the Cloudflare v4 API only after the new address is confirmed, converges
the record back when it drifts or disappears, and exposes Prometheus metrics.

---

## Features

- **Confirmed dynamic DNS updates:** a changed external IP must be observed in
  consecutive checks (`CF_DDNS_CONFIRM_CYCLES`) before DNS is touched — one bogus
  reading from a check service can never rewrite production DNS.
- **Reconciliation:** ttl/proxied config drift is converged at startup; the record
  is re-read periodically (`CF_DDNS_RECONCILE_INTERVAL`) to repair external edits
  and deletions.
- **Classified error handling:** permanent API refusals (auth, validation) fail
  fast with exit 1; transient failures (network, 5xx, 429 with `Retry-After`)
  retry with bounded exponential backoff; record writes use PATCH so unmanaged
  metadata (comments, tags, settings) survives.
- **Fail-closed record management:** a record is created only after a confirmed
  "no record exists" API answer; multiple A records for the host halt the service
  instead of guessing ownership.
- **Graceful shutdown:** SIGTERM/SIGINT exit cleanly in well under a second
  (bounded by one in-flight read timeout, max ~5s).
- **Prometheus metrics** with restart-safe alerting semantics (see below).

---

## Prerequisites

- Python 3.11 or newer (the container image ships 3.14).
- Pinned dependencies from `requirements.txt` (`requests`, `prometheus_client`).

---

## Usage

Local (uses the project venv):

    make venv
    . tokens.sh                # or export CF_DDNS_* yourself
    ./venv/bin/python cf_ddns.py

Container:

    # --format docker is required for podman to keep the HEALTHCHECK
    # (OCI images do not support it); plain `docker build` keeps it natively
    podman build --format docker -t cf-ddns .
    podman run -d -e CF_DDNS_TOKEN -e CF_DDNS_ZONE_ID -e CF_DDNS_HOST cf-ddns

---

## Environment Variables

Required:

| Variable | Meaning |
|---|---|
| `CF_DDNS_TOKEN` | Cloudflare API token with DNS edit rights for the zone |
| `CF_DDNS_ZONE_ID` | Cloudflare zone ID |
| `CF_DDNS_HOST` | FQDN of the managed A record; normalized (lowercase, IDNA) and validated |

Optional:

| Variable | Default | Meaning |
|---|---|---|
| `CF_DDNS_INTERVAL` | `10` | seconds between IP checks (≥1) |
| `CF_DDNS_TTL` | `120` | record TTL: `1` (Auto) or `30`–`86400`; 30–59 needs an Enterprise zone; forced to `1` when proxied |
| `CF_DDNS_PROXIED` | `false` | strictly `true`/`false` (case-insensitive); anything else is a startup error |
| `CF_DDNS_LOGLEVEL` | `INFO` | `DEBUG`/`INFO`/`WARNING`/`ERROR`/`CRITICAL` |
| `CF_DDNS_METRICS_PORT` | `9101` | Prometheus endpoint port |
| `CF_DDNS_METRICS_ADDR` | `0.0.0.0` | Prometheus endpoint bind address (must be a valid IP) |
| `CF_DDNS_MAX_FAILURES` | `10` | consecutive-failure budget (per class: IP retrieval / DNS updates) before exit 1 |
| `CF_DDNS_RECONCILE_INTERVAL` | `3600` | seconds between full record re-reads; `0` disables reconciliation |
| `CF_DDNS_CONFIRM_CYCLES` | `2` | consecutive identical readings before a new IP is written; `1` restores immediate updates |

Retrieved IPs are accepted only when they are strictly formatted, globally
routable **unicast** IPv4 addresses — private, loopback, link-local, CGNAT,
multicast, reserved, and broadcast ranges are rejected.

---

## Error handling

Every Cloudflare API exchange is classified:

| Class | Examples | Behavior |
|---|---|---|
| permanent | 400/401/403, invalid token/zone | no retry; `CRITICAL` + exit 1 (a config error cannot heal by retrying) |
| gone | HTTP 404 / error 81044 on update | record vanished: re-read, then update or recreate |
| exists | error 81057/81058 on create | adopt the existing record; never create twice |
| ambiguous | multiple A records for the host | no mutation; `CRITICAL` + exit 1 (see runbook below) |
| transient | network errors, 5xx, 429 | bounded retries with backoff; 429 honors `Retry-After` up to a 60s budget |

Create (POST) is not idempotent, so it gets exactly one attempt; after an
uncertain result the service re-reads state instead of blindly re-sending.
A record is only ever created after a confirmed empty read in the same pass.

### Runbook: AMBIGUOUS halt

With `restart: unless-stopped` the fail-closed exit becomes a visible restart
loop — intentionally: better a crash loop than mutating a record the service
does not own. To resolve: list the A records for the host (dashboard, API, or
`dig +short <host>`), delete the stale duplicates, restart the service.

---

## Prometheus Metrics

Exposed under `http://<CF_DDNS_METRICS_ADDR>:<CF_DDNS_METRICS_PORT>/metrics`:

| Metric | Meaning |
|---|---|
| `cf_ddns_ip_changes_total` | successful writes that replaced a known, **different** previous IP — the "my IP actually changed" signal; restart re-sync, first-run creation, and settings rewrites do not count |
| `cf_ddns_ip_updates_total` | every successful DNS write (creations, changes, reconciliation rewrites) |
| `cf_ddns_ip_info{cf_host, ip}` | **single series**: the current managed IP, value 1; the previous IP's series is removed on change (Prometheus keeps history server-side) |
| `cf_ddns_unconfirmed_ip_readings_total` | new-IP readings discarded before confirmation — a rising rate flags a flaky check service |
| `cf_ddns_ip_retrieval_errors_total{check_ip_service_host}` | failures per check-IP service |
| `cf_ddns_cloudflare_api_errors_total` | Cloudflare API errors (including retried transients) |
| `cf_ddns_last_ip_check_timestamp_seconds` | when the loop last checked the external IP |
| `cf_ddns_last_ip_update_timestamp_seconds` | last successful write **by this process** (0 after a restart until it writes) |
| `cf_ddns_record_modified_timestamp_seconds` | the record's `modified_on` as of the last read — provider state, includes manual edits |
| `cf_ddns_build_info{version}` | build/version marker |

## Monitoring & alerting (restart-safe)

Process metrics necessarily reset when the service restarts. These rules do not
page on restart artifacts:

```yaml
# The managed IP actually changed
- alert: CfDdnsIpChanged
  expr: increase(cf_ddns_ip_changes_total[15m]) > 0

# Scrape target is down
- alert: CfDdnsDown
  expr: up{job="cf-ddns"} == 0
  for: 5m

# Service stopped checking (guarded against the startup-zero artifact)
- alert: CfDdnsStalled
  expr: >
    (time() - cf_ddns_last_ip_check_timestamp_seconds > 300)
    and (time() - process_start_time_seconds > 300)
  for: 5m

# Cloudflare API errors observed (warning level; includes retried transients)
- alert: CfDdnsApiErrors
  expr: increase(cf_ddns_cloudflare_api_errors_total[15m]) > 0

# A check-IP service keeps returning unconfirmed readings
- alert: CfDdnsFlakyIpSource
  expr: increase(cf_ddns_unconfirmed_ip_readings_total[1h]) > 3
```

**Anti-patterns** — do *not* alert on `changes(cf_ddns_ip_info[...])`, on series
appearance/disappearance, or on `cf_ddns_ip_updates_total`: all of them still
see restarts and reconciliation rewrites.

The container healthcheck proves only process liveness (the metrics endpoint
answers), not that DNS is synchronized.

---

## Deployment

The failure policy relies on the supervisor restarting the process — a restart
policy is required. Exactly **one active writer** may manage a given
`(zone, host, record type)`; rolling updates must avoid overlap when instances
can observe different egress IPs.

```yaml
# docker-compose.yml
services:
  cf-ddns:
    image: cf-ddns
    restart: unless-stopped
    read_only: true
    cap_drop: [ALL]
    security_opt: [no-new-privileges:true]
    mem_limit: 64m
    ports:
      - "127.0.0.1:9101:9101"   # publish metrics on an internal interface only
    environment:
      CF_DDNS_TOKEN: "..."
      CF_DDNS_ZONE_ID: "..."
      CF_DDNS_HOST: "host.example.com"
```

---

## Testing

```sh
make venv    # create ./venv and install pinned runtime+dev deps
make test    # pytest with branch coverage
make lint    # ruff check

# run a single test
./venv/bin/python -m pytest test_logic.py -k reconcile -v
```

Test layout (pytest-native; every test exercises production code):

| File | Covers |
|---|---|
| `test_config.py` | `parse_env` validation and normalization |
| `test_validation.py` | IP validation, check-IP retrieval |
| `test_api.py` | Cloudflare API layer: outcome classification, retries, payloads |
| `test_logic.py` | orchestration: decision table, flap damping, reconciliation, failure policy |
| `test_lifecycle.py` | signals, interruptible waits, `main()` cleanup |
| `test_metrics.py` | metric semantics against a fresh registry, restart-safe contract |
| `conftest.py` | shared fixtures (`FakeResponse`, `FakeSession`, metric mocks) |

ERROR/WARNING log lines during a test run are expected — they are the output of
failure-path tests, not real failures.

CI integration:

```yaml
# Example GitHub Actions
- name: Test cf-ddns
  run: |
    cd tooling/cf-ddns
    make test
    make lint
```

---

## License

This project is licensed under the [Apache License 2.0](../LICENSE).

---

## Notes

- The external IP is fetched via HTTPS from `checkip.amazonaws.com` and
  `api.ipify.org` (response bodies are size-capped and strictly validated).
- The Cloudflare bearer token is sent only to `api.cloudflare.com` — the
  check-IP services are queried by a separate, credential-free HTTP session.
- Only IPv4 A records are managed; IPv6/AAAA is out of scope.
