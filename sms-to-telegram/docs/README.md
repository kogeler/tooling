# SMS to Telegram Forwarder

Forwards SMS messages from a USB GSM modem to Telegram chats.

## Features

- Reads SMS via USB GSM modem (SIM800C and compatible)
- Supports multipart (concatenated) SMS
- Supports Cyrillic and other non-ASCII characters (UCS2/UTF-16)
- Guaranteed delivery: SMS deleted only after successful Telegram send
- Automatic retry with exponential backoff for Telegram API
- Periodic modem health checks
- DRY_RUN mode for testing

## Project Structure

```
sms-to-telegram/
├── main.go      # Application entry point, config, main loop
├── at.go        # AT command wrapper for modem communication
├── pdu.go       # PDU parser (GSM 7-bit, UCS2, multipart)
├── at_test.go   # Tests for AT wrapper
├── pdu_test.go  # Tests for PDU parser
└── docs/
    ├── README.md
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

## Cross-compilation

```bash
# For ARM (Raspberry Pi)
GOOS=linux GOARCH=arm GOARM=7 go build -o sms-to-telegram-arm .

# For ARM64
GOOS=linux GOARCH=arm64 go build -o sms-to-telegram-arm64 .
```

## Docker

```bash
# Build image (from repo root)
docker build -t sms-to-telegram ./sms-to-telegram

# Run with modem device passed through
docker run --rm --device /dev/ttyUSB0 \
  -e TELEGRAM_BOT_TOKEN="your-bot-token" \
  -e TELEGRAM_CHAT_IDS="-100123456789,9876543" \
  -e SERIAL_PORT=/dev/ttyUSB0 \
  sms-to-telegram
```

## Installation

```bash
# Build and install binary
go build -o sms-to-telegram .
sudo cp sms-to-telegram /usr/local/bin/

# Create service user
sudo useradd -r -s /usr/sbin/nologin -G dialout sms-forwarder

# Install systemd service
sudo cp docs/sms-to-telegram.service /etc/systemd/system/
# Edit the service file to set your TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_IDS
sudo systemctl daemon-reload
sudo systemctl enable --now sms-to-telegram
```

## Error Handling

- Modem timeout/disconnect: immediate exit for orchestrator restart
- Telegram API errors: retry with exponential backoff (up to 10 attempts)
- 5 consecutive errors: exit for restart
- SMS deletion: only after successful Telegram delivery
