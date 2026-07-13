# sms-to-telegram — Agent Context

## Project Overview

A single-binary Go daemon that polls a USB GSM modem (SIM800C-class) over a serial
port using AT commands in **PDU mode**, decodes incoming SMS (GSM 7-bit, UCS-2,
alphanumeric senders, multipart/concatenated), forwards them to one or more
Telegram chats via the Bot API, and deletes each SMS from the SIM only after that
SMS reached every configured chat. It also runs modem diagnostics (SIM, signal,
network registration), monitors SIM storage usage, and pushes alert/recovery
notifications to the same chats.

Runs unattended on small Linux hosts (Raspberry Pi etc.) under systemd. There is no
database and no state on disk — **the SIM card is the durable message queue**, and
the 10-second poll cycle is the outer retry loop for anything transient.

## Architecture

```
sms-to-telegram/
  main.go        Config (env vars), outer retry loop with session-failure
                 counting, mandatory session init (initModemSession),
                 diagnostics (runModemDiagnostics), poll loop, strict CMGL
                 transcript parsing (ListResult/PendingSMS), per-message deletion
  at.go          SimpleAT: synchronous AT session with a persistent line framer,
                 partial-line reassembly, URC filtering (single- and two-line),
                 and a poisoned-session model (after a deadline/transport failure
                 every later command fails with ErrSessionPoisoned until the
                 port is reopened)
  pdu.go         PDU parser with typed outcomes (*NotDeliverError,
                 *MalformedPDUError, *UnsupportedEncodingError); DCS coding
                 groups, strict UDL/UDH bounds, alphanumeric OA (TON 0b101),
                 validated SCTS; MultipartCollector keyed by
                 sender+refKind+ref+total+alphabet with duplicate/conflict handling
  telegram.go    Deliverer: chunking below the 4096 visible-char limit, error
                 classification (transient / 429 / content-rejected /
                 destination-failed), per-chat cooldowns, plain-text fallback,
                 once-per-message rejected alerts
  errors.go      DiagnosticError (typed, alerting) vs SessionError (quiet reopen);
                 ErrorNotifier with per-chat delivered-state and storage alerts
  seams.go       TelegramSender / ATCommander / Clock interfaces; package-level
                 `clk` clock (swapped by tests)
  *_test.go      Unit tests: scripted serial port, fake AT/sender/clock,
                 CMGL transcript fixtures, captured PDU vectors, FuzzParsePDU
  livesend_test.go  SMS-SUBMIT PDU encoder for the live suite (untagged: its
                 round-trip unit tests run on every go test)
  live_test.go   //go:build live — live loopback suite against the real modem
                 and real Telegram (see "Live loopback suite" below)
  Dockerfile     Multi-stage build (runs go test), final alpine image
  .dockerignore  Keeps local-only files (tokens.sh, .gocache, binary) out of context
  .version       Release version — bumping this file triggers CI release
  tokens.sh      LOCAL ONLY (gitignored): live bot token for manual testing
  docs/
    README.md                 User documentation (config, install, error handling)
    install.sh                curl|bash installer/updater: env-file secrets (0600),
                              input validation, sha256 verification, update rollback
    sms-to-telegram.service   Hardened systemd unit; secrets via EnvironmentFile=
```

### Control flow

`main` → `run` (outer loop: creates the Telegram sender with
`bot.WithSkipGetMe()` — startup must not depend on Telegram availability — plus
`ErrorNotifier` and `Deliverer`, counts consecutive `SessionError`s and alerts
only at ≥3, decides `AT+CFUN` reset for SIM-class errors) → `runModemLoop`
(opens the port, `initModemSession` **must** succeed: sync, `ATE0`,
`AT+CMGF=0` + verify, `AT+CPMS` + capacity, `AT+CNMI` to suppress delivery
URCs; then diagnostics, then recovery notification, then two tickers: 10s poll,
60s health ping + `AT+CPMS?` storage check) → `processMessages` →
`listSMSMessages` (`AT+CMGL=4` with a 20s timeout; every header/PDU pair is
validated: hex-ness and byte count against the header `<length>` — any
inconsistency returns `ErrCMGLCorrupted` and nothing is sent or deleted) →
`Deliverer.Deliver` per message → `deleteBatch` of exactly that message's
`PartIndices`.

Everything runs in **one goroutine** (plus the signal handler). `SimpleAT` is not
concurrency-safe and the modem cannot multiplex commands — do not add goroutines
that touch the serial port, and do not add a background reader.

### Error model

Two distinct error families — keep them separate when changing code:

- `*SessionError` (wraps `ErrModemTimeout` / `ErrSessionPoisoned` /
  `ErrModemDisconnect` / `ErrWriteFailed`): the response stream cannot be
  trusted. The session is closed and reopened **quietly** (5s retry); an alert
  fires only after 3 consecutive failed sessions. Never map these to SIM/modem
  diagnostics — a timeout is not a SIM failure.
- `*DiagnosticError` (typed): modem-level condition worth alerting (SIM
  missing/PIN/PUK, registration denied, no signal / not registered after the
  grace window, init failure, serial port). SIM-class errors, registration
  denial and init failures set `needReset` (see `needsModemReset`) →
  `AT+CFUN=0/1` cycle on the next attempt — required for a hot-inserted SIM
  to be re-read. An init command failing with a modem ERROR is re-probed via
  `AT+CPIN?` and reported as SIM Not Detected when the SIM is absent, so one
  physical event keeps one error type (dedup → single alert).
- Telegram delivery never produces loop errors: `deliveryDeferred` retains
  everything for the next poll, `deliveryRejected` retains + alerts once +
  skips that message (in-memory set), `deliveryDone` deletes.

## Key invariants — do not break

1. **Never delete an SMS from the SIM before that SMS (all chunks, all parts)
   was delivered to all configured chats** — the only exceptions are status
   reports (delivery receipts, deleted silently) and stale multipart cleanup
   via `MULTIPART_MAX_AGE`. Losing an SMS is the worst failure mode;
   duplicates are acceptable, loss is not.
2. **DRY_RUN must never send to Telegram and never delete from SIM.**
3. Deletion authority is per message: a `PendingSMS` owns its `PartIndices`;
   never reintroduce a batch-level "delete everything at the end" model.
4. Nothing from a corrupted CMGL transcript may be forwarded or deleted.
5. A poisoned AT session must not issue further commands; reopen the port.
   An unacknowledged `AT+CMGD` (transport error) aborts all further deletes.
6. PDU mode only (`AT+CMGF=0`, verified at init); text-mode parsing is
   deliberately not supported.
7. All dynamic text going into Telegram HTML (SMS bodies, sender IDs,
   hostnames, modem output inside alerts) must pass `escapeHTML`.
8. Single-threaded modem access (see above).
9. SMS content (bodies, raw PDUs, full ICCID) must only appear in logs at
   DEBUG level — forwarded SMS regularly contain 2FA codes.
10. Time and timers in testable paths go through the package-level `clk`
    (seams.go), not `time.Now`/`time.After` directly.

## Configuration

Env vars only, parsed and validated in `loadConfig` (main.go):
`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_IDS` (comma-separated non-zero int64,
deduplicated), `SERIAL_PORT` (default `/dev/ttyUSB0`), `BAUD_RATE` (115200,
must be > 0), `LOG_LEVEL`, `DRY_RUN` (`true`/`yes`/`1`, case-insensitive),
`TELEGRAM_SEND_TIMEOUT` (20s), `NETWORK_REG_GRACE` (90s, shared by signal and
registration checks), `MULTIPART_MAX_AGE` (0 = disabled). Full table:
`docs/README.md`. In DRY_RUN the Telegram vars are optional.

## Build, test, run

Go toolchain runs directly on the host (this is a Go project — the podman-container
rule in the user's global CLAUDE.md applies to JS/TS toolchains only).

```bash
cd sms-to-telegram
go vet ./...
go test ./...          # no hardware needed; must stay hardware-free
go test -race ./...
go build -o sms-to-telegram .
# optional deeper parser fuzzing (seeds always run as part of plain go test):
go test -run=XXX -fuzz=FuzzParsePDU -fuzztime=30s .
```

- A local `GOCACHE` may live in `./.gocache/` (gitignored); using it is optional.
- The compiled binary `./sms-to-telegram` and `tokens.sh` are gitignored — never
  commit binaries or secrets.
- Live smoke test against real hardware:
  `DRY_RUN=true LOG_LEVEL=DEBUG SERIAL_PORT=/dev/ttyUSB0 ./sms-to-telegram`
  (`source tokens.sh` first for a non-dry-run test; that file holds a live token —
  keep it out of logs and commits). After changing at.go/pdu.go/main.go pipeline
  code, a hardware smoke test should cover: cold boot, unplug/replug, an SMS
  arriving during a poll, a long multipart SMS, and alert/recovery ordering.

### Testing conventions

- Tests are table-driven stdlib `testing`, no external test deps.
- Fakes live in `testutil_test.go`: `scriptedPort` (byte-level serial with
  per-read chunks and idle EOFs — a real port returns `io.EOF` on a 0-byte
  VTIME timeout because tarm/serial wraps `os.File`), `fakeAT` (command-level),
  `fakeSender`, `fakeClock` (`swapClock`; such tests must not run in parallel).
- PDU test vectors include real captured PDUs plus hand-packed GSM7/alphanumeric
  vectors (`pdu_extra_test.go`); when fixing parser bugs, add the offending PDU
  as a regression vector and a `FuzzParsePDU` seed.
- Pipeline tests assert the no-loss invariants (nothing deleted on any failure,
  DRY_RUN inert, corrupted transcripts inert) — extend them rather than delete.

### Live loopback suite

`live_test.go` (build tag `live`) sends real SMS to the SIM's **own number**
via `AT+CMGS` (`SimpleAT.CommandWithPrompt`) and verifies the full path:
network round trip → CMGL framing → PDU decode → multipart assembly → real
Telegram Bot API (wrapped in a recording decorator — bots cannot read back
their own messages, so API acceptance + the recorded payload is the check) →
per-message SIM deletion.

```bash
# Stop the service first — the serial port is exclusive.
source tokens.sh   # or: export TELEGRAM_BOT_TOKEN=...
LIVE_SERIAL_PORT=/dev/ttyUSB0 \
LIVE_SELF_NUMBER=+<your SIM's own number> \
LIVE_TELEGRAM_CHAT_ID=<dedicated test chat id> \
go test -tags live -run TestLive -v -timeout 20m .
```

Rules:

- Skips automatically when the `LIVE_*` env is not set; **never** wire it into
  CI — every run sends real, billed SMS and delivery latency makes it flaky
  by nature. It replaces most of the manual pre-release smoke test.
- Safety: scenarios correlate by a per-run `LIVE-<nonce>` body marker and only
  deliver/delete nonce messages; foreign SMS on the SIM are left untouched.
  Cleanup removes leftover nonce slots even on failure. Still prefer a
  dedicated test SIM: a real 2FA SMS arriving mid-test stays safe, but the
  service is down for the duration.
- The SUBMIT encoder lives in the untagged `livesend_test.go` so encoder ↔
  production-decoder round trips run in ordinary CI; keep it that way.
- Not covered (manual only): alphanumeric senders, alert/negative paths
  (antenna off, SIM out), CFUN reset, the >4096-char chunking scenario
  (~30 real SMS per run).

## Dependencies

- `github.com/go-telegram/bot` — Bot API. `bot.New` is called with
  `bot.WithSkipGetMe()`. Error mapping lives in `telegram.go:classifySendError`
  (uses `bot.ErrorBadRequest`/`ErrorForbidden`/`ErrorUnauthorized`/
  `ErrorNotFound`/`ErrorConflict` sentinels, `*bot.TooManyRequestsError` with
  `RetryAfter`, `*bot.MigrateError`).
- `github.com/tarm/serial` (archived 2018) — serial port; `ReadTimeout` maps to
  termios VTIME (500ms configured in `runModemLoop`). A migration to a
  maintained library (e.g. `go.bug.st/serial`) is possible behind the
  `io.ReadWriter` seam but requires real-hardware validation — never combine it
  with protocol changes.

## Release process

CI at the **repo root**, shared by all Go projects in the repository. Project
detection and matrix building live in the composite action
`.github/actions/go-projects-matrix` (a Go project = top-level folder with
`go.mod` + `main.go`; release targets also need `.version`):

- `.github/workflows/go-test.yml` — universal test gate: on pushes to **any**
  branch, PRs and manual dispatch it builds a matrix of affected projects and
  runs `go vet`, `go test`, `go test -race` and shellcheck (all tracked
  `*.sh` in the project) per project. When the change range is unknown (new
  branch) or `.github/` itself changed, all Go projects are tested.
- `go-binaries.yml` / `docker-images.yml` — release workflows; they trigger on
  push to `main` **only when `sms-to-telegram/.version` changed** in that
  commit. They run tests, cross-build linux amd64/arm64 (`CGO_ENABLED=0`,
  `-trimpath -ldflags="-s -w"`), generate `.sha256` files, and upload assets
  `sms-to-telegram[-<ver>]-linux-<arch>[.sha256]` to the GitHub release
  **tagged `sms-to-telegram`** (one rolling release per tool, assets
  overwritten). The docker workflow pushes `ghcr.io/kogeler/tooling/sms-to-telegram`.

To release: bump `.version` and update `CHANGELOG.md` (canonical changelog;
`docs/README.md` keeps only a short user-facing summary) in the same commit as
the changes.

Deployed hosts update via `install.sh --update --name <name>` (downloads the
release binary, verifies its sha256 when published, keeps a `.old` backup and
rolls back if the restarted service does not stay active). The unit template
applies heavy systemd sandboxing — if the binary gains new runtime needs
(files, sockets, devices), `docs/sms-to-telegram.service` must be updated in step.

## Security notes

- `tokens.sh` is a local, gitignored helper holding the live bot token for manual
  testing. The operator keeps it in the working directory deliberately — this is
  fine and not an issue; just never commit it or print its contents into logs
  (`.dockerignore` keeps it out of image build contexts).
- On deployed hosts, secrets live in `/opt/<name>/env` (root:root 0600)
  referenced by the unit via `EnvironmentFile=`; the unit itself is secret-free.
  Installs made by pre-1.2.0 installers still carry inline `Environment=`
  secrets in the unit until a full re-install.
- Forwarded SMS regularly contain 2FA codes — message content is sensitive and
  must only be logged at DEBUG level (invariant 9).
