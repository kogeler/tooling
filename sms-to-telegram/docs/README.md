# SMS to Telegram Forwarder

Forwards SMS messages from a USB GSM modem to Telegram chats.

## Features

- Reads SMS via USB GSM modem (SIM800C and compatible)
- Supports multipart (concatenated) SMS
- Supports Cyrillic and other non-ASCII characters (UCS2/UTF-16)
- Guaranteed delivery: SMS deleted only after successful Telegram send
- Automatic retry with exponential backoff for Telegram API
- Per-call Telegram API timeouts
- Periodic modem health checks
- Network/signal alerts with configurable grace period
- DRY_RUN mode for testing
- Optional cleanup of stale multipart parts

## Project Structure

```
sms-to-telegram/
├── main.go        # Application entry point, config, main loop
├── at.go          # AT command wrapper for modem communication
├── pdu.go         # PDU parser (GSM 7-bit, UCS2, multipart)
├── errors.go      # Diagnostic errors + Telegram notifier
├── main_test.go   # Formatting/utility tests
├── at_test.go     # Tests for AT wrapper
├── pdu_test.go    # Tests for PDU parser
├── errors_test.go # Tests for diagnostics/notifier
├── go.mod         # Go module definition
├── go.sum         # Go dependency checksums
├── Dockerfile     # Container build
├── .version       # Current release version
└── docs/
    ├── README.md              # Project documentation
    ├── install.sh             # Remote install script
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
| `DRY_RUN` | No | `false` | If `true` or `1`, don't send to Telegram and don't delete SMS |
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

# Install systemd service
sudo cp docs/sms-to-telegram.service /etc/systemd/system/
# Edit the service file to set your: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_IDS, SERIAL_PORT and binary path
sudo systemctl daemon-reload
sudo systemctl enable --now sms-to-telegram
```

### Docker

```bash
# You can build image (from repo root) or use existing images
docker build -t sms-to-telegram ./sms-to-telegram

# Run with modem device passed through
docker run --rm --device /dev/ttyUSB0 \
  -e TELEGRAM_BOT_TOKEN="your-bot-token" \
  -e TELEGRAM_CHAT_IDS="-100123456789,9876543" \
  -e SERIAL_PORT=/dev/ttyUSB0 \
  ghcr.io/kogeler/tooling/sms-to-telegram:latest
```

## Error Handling

Diagnostic error types (from code):

- `ErrTypeSerialPort`: cannot open serial port; alert sent; retry after 30s; modem reset: no
- `ErrTypeModemNotResponding`: modem timeouts/health check failed; alert sent; retry after 30s; modem reset: no
- `ErrTypeSimNotDetected`: SIM not detected or not ready; alert sent; modem reset: yes
- `ErrTypeSimPinRequired`: SIM requires PIN; alert sent; modem reset: yes
- `ErrTypeSimPukLocked`: SIM is PUK locked; alert sent; modem reset: yes
- `ErrTypeNetworkDenied`: operator denied registration; alert sent; modem reset: yes
- `ErrTypeNetworkNotRegistered`: no registration after `NETWORK_REG_GRACE`; alert sent; modem reset: no
- `ErrTypeNoSignal`: no signal detected (CSQ=99); alert sent; modem reset: no

Other error handling:

- Telegram API errors: per-chat retries with exponential backoff (up to 10 attempts) and per-call timeout
- SMS deletion: only after successful delivery to all chats and complete multipart; stale multipart parts deleted only if `MULTIPART_MAX_AGE` is set

## Changelog

### 1.1.0

- DRY_RUN no longer requires Telegram env vars; added `TELEGRAM_SEND_TIMEOUT`, `NETWORK_REG_GRACE`, `MULTIPART_MAX_AGE`
- Telegram sending now uses per-call timeouts and tries all chats before failing
- Multipart handling only deletes complete messages; optional cleanup of stale parts
- Diagnostics alert on no signal/network not registered with grace period
- AT command reader handles partial lines more reliably
