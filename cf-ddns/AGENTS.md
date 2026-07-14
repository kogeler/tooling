# cf-ddns — agent entry point

## What this is

A single-module Python service that keeps one Cloudflare DNS A record pointed at
this host's external IPv4 address. It polls public check-IP services, writes
through the Cloudflare v4 API only after the new address is confirmed over
consecutive readings, periodically reconciles the record against the desired
state, and exposes Prometheus metrics with restart-safe alerting semantics.

## Architecture

`main()` is wiring only: parse config → register signal handlers → bind the
metrics endpoint → build HTTP clients → `startup_state()` → loop of
`run_iteration(config, state, clients)` with `enforce_failure_policy()` between
iterations and an interruptible wait.

- **`HttpClients`** — two persistent `requests.Session`s: `cloudflare` (carries
  the bearer header, talks only to `api.cloudflare.com`) and `check_ip`
  (credential-free). The token never leaves the Cloudflare session.
- **`DdnsState`** — the loop state: `last_ip`, `record_id`, `ip_failures`,
  `cf_failures`, `fatal`, `force_update`, `pending_ip`/`pending_seen`
  (flap-damping window), `last_reconcile` (monotonic).
- **`Outcome`** — classification of every API call, produced by the shared
  `_cf_request()` helper:

| Outcome | Meaning | Reaction |
|---|---|---|
| `OK` | success | proceed |
| `ABSENT` | confirmed empty read (HTTP 200, empty list) | the only thing that authorizes a create |
| `GONE` | record id invalid (404 / code 81044) | re-read, then update or recreate |
| `EXISTS` | create refused (81057/81058) | adopt the existing record; never create twice |
| `AMBIGUOUS` | multiple A records | fail closed: no mutation, exit 1 |
| `TRANSIENT` | network / 5xx / 429 | bounded retries, then counted toward the failure budget |
| `PERMANENT` | other 4xx (auth, validation) | no retry; exit 1 immediately |

## Invariants (do not break; tests assert them)

- `create_dns_record()` is called only after a confirmed `ABSENT` in the same
  pass; `TRANSIENT`/`PERMANENT`/`AMBIGUOUS` never mutate anything.
- Create POST is non-idempotent: exactly one attempt; uncertain results re-read
  state instead of re-sending.
- A *new* IP is written only after `confirm_cycles` consecutive identical
  readings (first-run creation included); reconciliation/`force_update` writes
  reuse the already-confirmed IP and are exempt.
- `cf_ddns_ip_info` has **at most one series** — the current managed IP; old
  series are `remove()`d, never left at 0.
- `cf_ddns_ip_changes_total` moves only when a known, different previous IP was
  replaced — restarts, first-run creation, and settings rewrites do not count.
- `cf_ddns_last_ip_update_timestamp_seconds` is process-local (0 after restart);
  provider state lives in `cf_ddns_record_modified_timestamp_seconds`.
- After the shutdown event is set: no new HTTP request, no DNS mutation, and
  shutdown-caused failures do not count toward the failure budget. Shutdown is
  bounded by one in-flight read timeout (5s) + margin.
- No `prometheus_client` private APIs (`._value` is banned; a test greps for it).
- Public contracts (function signatures, `Outcome`, `DdnsState`, `HttpClients`)
  may be extended, not renamed.

## Alerting contract (restart-safe)

Restarts stay observable but must never be classified as DNS changes. Alert on
`increase(cf_ddns_ip_changes_total[...])` for real IP changes; guard staleness
alerts with `process_start_time_seconds`; use `up == 0` for availability. Do
**not** alert on `changes(cf_ddns_ip_info[...])`, series appearance, or
`cf_ddns_ip_updates_total`. Ready-made rules live in README's "Monitoring &
alerting" section. A change that breaks these semantics is a regression.

## How to work on it

```sh
make venv    # create ./venv and install pinned deps (idempotent)
make test    # pytest with branch coverage
make lint    # ruff check
make clean   # remove venv and caches
# run a single test
./venv/bin/python -m pytest test_logic.py -k reconcile -v
# --format docker is required for podman to keep the HEALTHCHECK
podman build --format docker -t cf-ddns .
```

Live manual check (mutates only the dedicated test host `test123.gametheory.me`):
`. tokens.sh && ./venv/bin/python cf_ddns.py` — `tokens.sh` is **gitignored**
real credentials; never commit it, never echo its values, never point it at a
production host. Automated tests stay fully offline.

## Testing philosophy

pytest-native: plain functions, fixtures, `monkeypatch`, `parametrize`; no
`unittest.TestCase`, no custom runners. Every test calls production code — no
tautologies (re-implementing logic inside the test), no mocking the function
under test. Shared helpers (`FakeResponse`, `FakeSession`, metric mocks) live in
`conftest.py`; metric assertions use a fresh `CollectorRegistry` via
`create_metrics(registry)`. File map is in README's Testing section.

## Configuration

Full `CF_DDNS_*` table with defaults and semantics: README "Environment
Variables". Notable: `CF_DDNS_PROXIED` is strictly `true`/`false`; TTL is `1`
(Auto) or 30–86400 and is forced to Auto for proxied records; the host is
IDNA-normalized and label-validated at startup.

## Release procedure

Bump **all three together** — they must match:
1. `.version`
2. `Dockerfile` `LABEL version`
3. `cf_ddns.__version__` (feeds `cf_ddns_build_info{version=...}`)

Then: `make clean && make test && make lint`, container build + smoke run, and
a live pass against the test host.

## Deployment constraints

Restart policy required (the failure policy exits on purpose); exactly one
active writer per `(zone, host, record type)`; healthcheck is liveness-only.
Hardened compose example in README.
