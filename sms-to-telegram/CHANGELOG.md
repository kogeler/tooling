# Changelog

## 1.2.0

### AT session reliability

- Persistent line framer with a partial-line accumulator: a response split
  across serial read timeouts is reassembled instead of being committed as two
  bogus lines (previously a split PDU could be forwarded as garbage and the
  real SMS deleted unread).
- Poisoned-session model: after a command deadline or transport failure the
  response stream is no longer trusted — the session is closed and reopened
  instead of letting a late reply satisfy the wrong command. Reopens are
  quiet; a "Modem Not Responding" alert fires only after 3 consecutive failed
  sessions.
- Unsolicited result codes (`+CMTI`, `+CMT`/`+CDS` with payload line, `RING`,
  boot banners, …) are recognized and filtered out of command responses.
- `+CME`/`+CMS ERROR` are treated as terminal result lines: the command fails
  but the session stays synchronized.
- Mandatory, verified session initialization: `ATE0`, PDU mode (`AT+CMGF=0`
  queried back), SIM storage (`AT+CPMS` with capacity parsing) and
  `AT+CNMI=2,0,0,0,0` (fallback `0,0,0,0,0`) must all succeed before polling
  starts; failure raises a typed "Modem Initialization Failed" alert.
- Support for prompt-style commands (`AT+CMGS` dialog: prompt, payload,
  Ctrl+Z) — used by the live test suite.

### SMS listing and deletion safety

- Strict `AT+CMGL` transcript validation: every header/PDU pair is checked
  (pure hex, byte count consistent with the header length field, numeric
  status). A corrupted transcript aborts the whole cycle with no sends and no
  deletions.
- Per-message deletion: each message owns exactly its SIM slot indices and is
  deleted immediately after it reached every configured chat. A later failure
  can no longer cause earlier messages to be re-sent, and one stuck message no
  longer blocks the rest.
- Storage status is honored: stored outgoing messages (sent-box, stat 2/3) are
  never touched; recognized status reports are deleted without forwarding;
  strictly framed but undecodable PDUs are forwarded as marked raw hex and
  only then deleted.
- An unacknowledged `AT+CMGD` (transport error) stops all further deletes and
  reopens the session.
- Dedicated bounded timeout (20s) for the potentially large `AT+CMGL` listing.

### Telegram delivery

- Error classification: transient errors (network, 5xx) get up to 3 quick
  retries and then defer to the next poll (the SIM is the durable queue);
  429 honors `retry_after` with a per-chat cooldown while polling continues;
  destination/token errors (401/403/404) pause delivery and alert without
  deleting anything.
- Long messages are split into chunks below Telegram's 4096-character limit,
  each carrying the full metadata header and a chunk marker.
- Content rejected by Telegram (400) is retried once as plain text; if still
  rejected, the SMS stays on the SIM, a one-time alert with its slot number is
  sent, the message is not re-sent until restart, and later messages continue.
- Startup no longer requires Telegram availability (`GetMe` is skipped); the
  service starts degraded and delivers once the network is up.

### PDU parser

- Alphanumeric sender IDs (banks, services) are decoded as GSM 7-bit text
  instead of BCD digit garbage; BCD extension digits (`* # a b c`) supported.
- DCS is parsed by coding group (TS 23.038): message-waiting groups map to the
  right alphabet, compressed/reserved schemes yield a typed
  unsupported-encoding result instead of silently wrong text.
- Strict UDL/UDH bounds for all alphabets (odd UCS2 payloads, UDL beyond data,
  UDH larger than UDL are rejected as malformed); national language shift
  tables are surfaced as unsupported instead of decoding with the wrong table.
- Timestamps (SCTS) are validated (BCD digits, calendar ranges, timezone
  range); invalid timestamps become zero time and never feed stale cleanup.
- Typed parse outcomes distinguish DELIVER / status report / stored SUBMIT /
  malformed / unsupported, so the pipeline applies policy per kind.

### Multipart

- Grouping key now includes reference width (8/16-bit), total part count and
  alphabet in addition to sender+reference — parts of different messages can
  no longer be spliced together after 8-bit reference wraparound.
- Byte-identical duplicate part deliveries keep all SIM slots and free them on
  assembly; conflicting duplicates mark the group as conflicted (never
  assembled, never silently deleted; stale cleanup resolves it).
- Malformed concatenation headers (zero totals, part out of range, wrong IE
  size, conflicting IEs) are rejected as malformed instead of being treated as
  ordinary single messages.
- A pending multipart declaring more parts than the SIM can hold is logged as
  impossible to complete.

### Diagnostics and alerting

- Exact `+CPIN` parsing: `NOT READY` is no longer misclassified as `READY`
  (substring bug); PIN2/PUK2/`BUSY` states handled.
- A mandatory-init command failing with a modem ERROR while the SIM is absent
  is reported as "SIM Not Detected" (verified by a SIM probe), not "Modem
  Initialization Failed" — a pulled SIM produces exactly one alert and one
  recovery instead of two alternating alert types; init failures now also
  trigger the `AT+CFUN` reset path so a hot-inserted SIM is re-read
  (verified live: modem-without-SIM boot, hot insert, hot pull, re-insert).
- Signal (CSQ=99) and network registration share the `NETWORK_REG_GRACE`
  window — no more spurious "No Signal" alert/recovery pair on every start.
- Transport failures during diagnostics are reported as session errors, not
  fake SIM failures, so no misleading alerts or pointless `AT+CFUN` resets.
- `AT+CSQ`/`AT+CREG?` are required evidence: diagnostics no longer pass when
  radio state cannot be read.
- Notifier keeps per-chat delivered state: a chat that missed an alert is
  retried without spamming the others; recovery goes only to chats that saw
  the error.
- "No signal" and "not registered" alerts form one deduplication group —
  flapping weak coverage no longer produces an alert on every flip between
  the two states.
- Telegram destination failures (kicked bot, deleted chat) alert once per
  chat via a stateless path and send a one-time notice when the chat works
  again; they no longer latch into the modem-recovery state, which used to
  produce a false "Recovered" plus a repeated alert on every session restart.
- Last-resort reset escalation: three consecutive failures of the same
  condition without a healthy session force an `AT+CFUN` reset even for
  error types that normally never reset.
- Documentation now recommends a stable `/dev/serial/by-id/...` path for
  `SERIAL_PORT` (`ttyUSBn` names change on USB re-enumeration).
- All dynamic values in notifications are HTML-escaped — raw modem output can
  no longer make Telegram reject the alert itself.
- SIM storage monitoring: usage is checked at session start and on every
  health tick; ≥80% raises a "SIM Storage Low" alert (cleared below 70%).
- Shutdown is cancellation-aware end to end; no spurious "Recovered"
  notification while stopping.

### Security and operations

- Secrets moved out of the world-readable systemd unit into a root-only 0600
  environment file referenced via `EnvironmentFile=`; the installer writes it
  atomically.
- Installer hardening: strict validation of name/serial/token/chat-ID/baud
  inputs (no path traversal via `--name`), correct sed escaping, sha256
  verification of downloaded binaries (checksums now published by CI) and
  automatic rollback to the previous binary if the restarted service fails.
- Sensitive data (SMS bodies, raw PDUs, full ICCID) only appears in logs at
  DEBUG; INFO/WARN carry lengths and non-reversible fingerprints.
- `.dockerignore` keeps local-only files and caches out of image build
  contexts; documented Docker invocation fixed (`--group-add` for the serial
  device group).
- New universal CI test workflow: affected Go projects are detected by a
  shared composite action (`.github/actions/go-projects-matrix`) and each runs
  `go vet`, `go test`, `go test -race` and shellcheck on pushes to any branch,
  pull requests and manual dispatch (previously only release builds were
  tested). The release workflow reuses the same detection action.
- Module path corrected to `github.com/kogeler/tooling/sms-to-telegram`.

### Configuration

- `DRY_RUN` accepts `true`/`yes`/`1` case-insensitively.
- Chat ID `0` and non-positive `BAUD_RATE` are rejected; duplicate chat IDs
  are deduplicated preserving order.

### Testing

- Test seams (Telegram sender, AT commander, clock) with full fakes: scripted
  byte-level serial port, command-level AT fake, deterministic clock.
- Pipeline tests enforce the no-loss invariants: nothing is deleted on any
  delivery failure, DRY_RUN never sends or deletes, corrupted transcripts
  never trigger actions.
- PDU coverage extended with hand-packed and captured vectors: alphanumeric
  senders, GSM7 with UDH and fill bits, escape sequences (`€ [ ] { } ~ \ |`)
  through the full parser, `@` including trailing position, boundary lengths
  (160/153+UDH/70 UCS2), empty user data, RTL and CJK scripts, malformed UDH
  variants, invalid timestamps; plus a `ParsePDU` fuzz target with seeds.
- Live loopback suite (`go test -tags live`, env-gated, never in CI): sends
  real SMS to the SIM's own number via `AT+CMGS` and verifies the full path —
  network round trip, decoding, multipart assembly, real Telegram delivery,
  SIM cleanup. Scenarios: GSM7, UCS2 with Cyrillic + emoji, 3-part
  concatenated message, GSM7 national characters; foreign SMS on the SIM are
  never touched. A flight-mode scenario (AT+CFUN=4, no SMS cost) drives a real
  radio outage and verifies the radio diagnosis and CFUN recovery cycle.

## 1.1.0

- DRY_RUN no longer requires Telegram env vars; added `TELEGRAM_SEND_TIMEOUT`, `NETWORK_REG_GRACE`, `MULTIPART_MAX_AGE`
- Telegram sending now uses per-call timeouts and tries all chats before failing
- Multipart handling only deletes complete messages; optional cleanup of stale parts
- Diagnostics alert on no signal/network not registered with grace period
- AT command reader handles partial lines more reliably
