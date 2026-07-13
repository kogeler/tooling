# SMS to Telegram Forwarder

Forwards SMS messages from a USB GSM modem to Telegram chats.

## Features

- Reads SMS via USB GSM modem (SIM800C and compatible) in PDU mode
- Supports multipart (concatenated) SMS, alphanumeric sender IDs, GSM 7-bit
  and UCS2 (Cyrillic and other non-ASCII) encodings
- Guaranteed delivery: an SMS is deleted from the SIM only after every part of
  it reached every configured chat (at-least-once; duplicates possible, loss not)
- Long messages are split into multiple Telegram messages below the 4096-char limit
- Telegram errors are classified: transient errors retry briefly and defer to
  the next poll, 429 honors retry_after per chat, permanently rejected content
  is kept on the SIM and alerted once
- Robust AT session handling: split lines are reassembled, unsolicited modem
  notifications are filtered, and a desynchronized session is reopened instead
  of trusted
- Strict CMGL transcript validation: a corrupted listing never triggers
  forwarding or deletion
- Periodic modem health checks and SIM storage monitoring with alerts
- Network/signal alerts with a shared configurable grace period
- DRY_RUN mode for testing
- Optional cleanup of stale multipart parts

## Project Structure

```
sms-to-telegram/
├── main.go        # Entry point, config, session init, diagnostics, poll loop,
│                  # strict CMGL parsing and per-message deletion
├── at.go          # AT session: line framing, URC filtering, poisoned-session model
├── pdu.go         # PDU parser (GSM 7-bit, UCS2, alphanumeric senders, multipart)
├── telegram.go    # Delivery: chunking, error classification, per-chat cooldowns
├── errors.go      # Typed errors + per-chat Telegram notifier + storage alerts
├── seams.go       # Narrow interfaces (Telegram, AT, clock) for testing
├── *_test.go      # Unit tests incl. transcript fixtures and a PDU fuzz target
├── go.mod         # Go module definition
├── go.sum         # Go dependency checksums
├── Dockerfile     # Container build
├── .dockerignore  # Keeps local-only files out of the build context
├── .version       # Current release version
└── docs/
    ├── README.md              # Project documentation
    ├── install.sh             # Remote install/update script (checksums, rollback)
    └── sms-to-telegram.service  # Systemd unit with security hardening
```

## Configuration

All configuration via environment variables:

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `TELEGRAM_BOT_TOKEN` | Yes | - | Telegram Bot API token |
| `TELEGRAM_CHAT_IDS` | Yes | - | Comma-separated list of chat IDs |
| `SERIAL_PORT` | No | `/dev/ttyUSB0` | Serial port device |
| `BAUD_RATE` | No | `115200` | Serial port baud rate |
| `LOG_LEVEL` | No | `INFO` | Log level: DEBUG, INFO, WARN, ERROR |
| `DRY_RUN` | No | `false` | If `true`, `yes` or `1` (case-insensitive), don't send to Telegram and don't delete SMS |
| `TELEGRAM_SEND_TIMEOUT` | No | `20s` | Timeout for a single Telegram API call (e.g. `10s`, `1m`) |
| `NETWORK_REG_GRACE` | No | `90s` | Grace period to wait for network registration before alerting; `0` disables grace |
| `MULTIPART_MAX_AGE` | No | `0` | Max age for stale multipart parts before deletion (e.g. `72h`); `0` disables cleanup |

## Usage

```bash
# Build
go build -o sms-to-telegram .

# Run
export TELEGRAM_BOT_TOKEN="your-bot-token"
export TELEGRAM_CHAT_IDS="-100123456789,987654321"
./sms-to-telegram

# Test mode (no Telegram, no SMS deletion)
DRY_RUN=true LOG_LEVEL=DEBUG ./sms-to-telegram
```

When `DRY_RUN` is enabled, `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_IDS` are optional.

## Testing

Unit tests need no hardware and run in CI:

```bash
go vet ./...
go test ./...
go test -race ./...
# optional deeper PDU fuzzing (seed corpus already runs with plain go test):
go test -run=XXX -fuzz=FuzzParsePDU -fuzztime=30s .
```

### Live loopback tests (real modem, opt-in)

The `live` build tag enables an end-to-end suite that sends real SMS to the
SIM's **own number** via the modem and verifies reception, decoding, multipart
assembly, Telegram delivery and SIM cleanup. It is **not** run in CI: every run
sends real (billed) SMS and depends on network delivery latency.

Requirements: exclusive access to the modem (stop the service first, the serial
port is exclusive), a SIM that can receive self-addressed SMS with PIN disabled,
and a dedicated test chat. Configure everything via environment variables — no
secrets or numbers are hard-coded:

```bash
# Stop the running service first so the port is free.
export TELEGRAM_BOT_TOKEN="your-bot-token"
LIVE_SERIAL_PORT=/dev/ttyUSB0 \
LIVE_SELF_NUMBER=+<your SIM's own number> \
LIVE_TELEGRAM_CHAT_ID=<dedicated test chat id> \
go test -tags live -run TestLive -v -timeout 20m .
```

The suite skips automatically when the `LIVE_*` variables are unset. Only
messages carrying a per-run marker are delivered or deleted, so other SMS on
the SIM are left untouched — but a dedicated test SIM is still recommended.

## Cross-compilation

```bash
# For ARM (Raspberry Pi)
GOOS=linux GOARCH=arm GOARM=7 go build -o sms-to-telegram-arm .

# For ARM64
GOOS=linux GOARCH=arm64 go build -o sms-to-telegram-arm64 .
```

## Installation

### Remote install (script)

For install and update, you can optionally pass `--version X.Y.Z`; if omitted, the latest build is used.

```bash
curl -fsSL https://raw.githubusercontent.com/kogeler/tooling/refs/heads/main/sms-to-telegram/docs/install.sh | \
  sudo bash -s -- \
    --name sms-to-telegram \
    --serial /dev/ttyUSB0 \
    --token "your-bot-token" \
    --chats "-100123456789,987654321" \
    --baud 115200
```

Update binary only:

```bash
curl -fsSL https://raw.githubusercontent.com/kogeler/tooling/refs/heads/main/sms-to-telegram/docs/install.sh | \
  sudo bash -s -- \
    --update \
    --name sms-to-telegram
```

### Manual install (build + systemd)

```bash
# Build and install binary
go build -o sms-to-telegram .
sudo cp sms-to-telegram /usr/local/bin/

# Create service user
sudo useradd -r -s /usr/sbin/nologin -G dialout sms-forwarder

# Create the secrets file (root-only readable; the unit references it via
# EnvironmentFile= so no secrets live in the world-readable unit file)
sudo mkdir -p /opt/sms-to-telegram
sudo sh -c 'umask 077 && cat > /opt/sms-to-telegram/env <<EOF
TELEGRAM_BOT_TOKEN=your-bot-token
TELEGRAM_CHAT_IDS=-100123456789,987654321
SERIAL_PORT=/dev/ttyUSB0
BAUD_RATE=115200
LOG_LEVEL=INFO
EOF'

# Install systemd service (edit ExecStart/EnvironmentFile paths if you changed them)
sudo cp docs/sms-to-telegram.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now sms-to-telegram
```

### Docker

```bash
# You can build image (from repo root) or use existing images
docker build -t sms-to-telegram ./sms-to-telegram

# Run with modem device passed through.
# The image runs as a non-root user, so the container user must be added to the
# device's owning group; --device alone does not grant file permission.
docker run --rm --device /dev/ttyUSB0 \
  --group-add "$(stat -c '%g' /dev/ttyUSB0)" \
  -e TELEGRAM_BOT_TOKEN="your-bot-token" \
  -e TELEGRAM_CHAT_IDS="-100123456789,9876543" \
  -e SERIAL_PORT=/dev/ttyUSB0 \
  ghcr.io/kogeler/tooling/sms-to-telegram:latest
```

## Error Handling

Modem-side:

- Transport/session failures (timeouts, split responses, desync) close and
  reopen the serial session quietly; an alert (`Modem Not Responding`) is sent
  only after 3 consecutive failed sessions.
- Session initialization (`ATE0`, PDU mode, SIM storage, `AT+CNMI`) is
  mandatory and verified; failure raises `Modem Initialization Failed`.
- Diagnostic alerts (deduplicated per chat, with recovery notifications):
  serial port, modem not responding, SIM not detected / PIN required / PUK
  locked (these trigger an `AT+CFUN` modem reset on the next attempt),
  registration denied (reset too), not registered or no signal after
  `NETWORK_REG_GRACE` (signal and registration share the grace window).
- SIM storage: usage is checked at session start and on every health tick;
  crossing 80% raises a `SIM Storage Low` alert (cleared below 70%).

Telegram-side:

- Long texts are split into chunks below the 4096-character limit, each with
  the full metadata header.
- Transient errors (network, 5xx): up to 3 quick attempts, then the message
  stays on the SIM and the next poll (10s) retries — the SIM is the queue.
- 429: the chat cools down for `retry_after`; polling continues meanwhile.
- 400 on content: retried once as plain text; if still rejected, the SMS is
  kept on the SIM, an alert with its slot number is sent once, and later
  messages continue to flow. Remove the slot manually (`AT+CMGD=<index>`).
- 401/403/404 (token/chat problems): delivery pauses (nothing is deleted) and
  an alert is sent; fix the configuration and everything resumes.

Message hygiene:

- SMS are deleted per message, immediately after that message reached all
  chats — a later failure never causes earlier messages to be re-sent.
- Status reports are deleted without forwarding; stored outgoing messages
  (sent-box) are never touched; undecodable but correctly framed PDUs are
  forwarded as marked raw hex and then deleted.
- A corrupted `AT+CMGL` transcript aborts the whole cycle with no sends and
  no deletions, and the session is reopened.
- Stale multipart parts are deleted only if `MULTIPART_MAX_AGE` is set;
  conflicting duplicate parts are never assembled and are left to stale cleanup.


