#!/usr/bin/env bash

# Copyright © 2025 kogeler
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

DEFAULT_BAUD="115200"
BIN_BASE_URL="https://github.com/kogeler/tooling/releases/download/sms-to-telegram"
SERVICE_URL="https://raw.githubusercontent.com/kogeler/tooling/refs/heads/main/sms-to-telegram/docs/sms-to-telegram.service"

usage() {
  cat <<'EOF'
Usage: install.sh --name NAME --serial /dev/ttyUSB0 --token BOT_TOKEN --chats id1,id2 [--baud 115200]

Required:
  --name    Installation name (used for /opt/<name> and service name)
  --serial  Serial port path for the modem (e.g., /dev/ttyUSB0)
  --token   Telegram bot token
  --chats   Comma-separated list of chat IDs

Optional:
  --baud    Serial port baud rate (default: 115200)
EOF
  exit 1
}

escape_sed() {
  # Escape sed replacement delimiters and ampersand to keep literal values
  printf '%s' "$1" | sed -e 's/[\\/&]/\\&/g'
}

if [[ $# -eq 0 ]]; then
  usage
fi

NAME=""
SERIAL_PORT=""
TELEGRAM_TOKEN=""
CHAT_IDS=""
BAUD_RATE="$DEFAULT_BAUD"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --name)
      NAME="${2:-}"
      shift 2
      ;;
    --serial)
      SERIAL_PORT="${2:-}"
      shift 2
      ;;
    --token)
      TELEGRAM_TOKEN="${2:-}"
      shift 2
      ;;
    --chats)
      CHAT_IDS="${2:-}"
      shift 2
      ;;
    --baud)
      BAUD_RATE="${2:-}"
      shift 2
      ;;
    -h|--help)
      usage
      ;;
    *)
      echo "Unknown argument: $1"
      usage
      ;;
  esac
done

if [[ -z "$NAME" || -z "$SERIAL_PORT" || -z "$TELEGRAM_TOKEN" || -z "$CHAT_IDS" ]]; then
  echo "Missing required arguments."
  usage
fi

if [[ "$NAME" =~ [^a-zA-Z0-9_.-] ]]; then
  echo "Installation name may only contain letters, numbers, '.', '_' or '-'."
  exit 1
fi

if [[ "$SERIAL_PORT" != /* ]]; then
  echo "Serial port path must be absolute (e.g., /dev/ttyUSB0)."
  exit 1
fi

if [[ ! "$BAUD_RATE" =~ ^[0-9]+$ ]]; then
  echo "Baud rate must be numeric."
  exit 1
fi

if [[ "$EUID" -ne 0 ]]; then
  echo "Please run as root (use sudo)."
  exit 1
fi

if ! command -v curl >/dev/null 2>&1; then
  echo "curl not found. Please install curl."
  exit 1
fi

if ! command -v systemctl >/dev/null 2>&1; then
  echo "systemctl not found. This script requires systemd."
  exit 1
fi

ARCH="$(uname -m)"
case "$ARCH" in
  x86_64|amd64)
    BIN_ARCH="amd64"
    ;;
  aarch64|arm64)
    BIN_ARCH="arm64"
    ;;
  *)
    echo "Unsupported architecture: $ARCH"
    exit 1
    ;;
esac

INSTALL_DIR="/opt/${NAME}"
BIN_PATH="${INSTALL_DIR}/sms-to-telegram"
SERVICE_NAME="${NAME}"
SERVICE_FILE="${SERVICE_NAME}.service"
SERVICE_PATH="/etc/systemd/system/${SERVICE_FILE}"
BIN_URL="${BIN_BASE_URL}/sms-to-telegram-linux-${BIN_ARCH}"

echo "Creating install directory at ${INSTALL_DIR}..."
mkdir -p "$INSTALL_DIR"

echo "Downloading binary for ${BIN_ARCH}..."
curl -fsSL "$BIN_URL" -o "$BIN_PATH"
chmod 0755 "$BIN_PATH"
chown root:root "$BIN_PATH"

if ! id -u sms-forwarder >/dev/null 2>&1; then
  echo "Creating service user sms-forwarder..."
  useradd -r -s /usr/sbin/nologin -G dialout sms-forwarder
fi

TMP_SERVICE="$(mktemp)"
cleanup() {
  rm -f "$TMP_SERVICE"
}
trap cleanup EXIT

echo "Downloading systemd unit template..."
curl -fsSL "$SERVICE_URL" -o "$TMP_SERVICE"

ESC_TOKEN="$(escape_sed "$TELEGRAM_TOKEN")"
ESC_CHATS="$(escape_sed "$CHAT_IDS")"
ESC_SERIAL="$(escape_sed "$SERIAL_PORT")"
ESC_BAUD="$(escape_sed "$BAUD_RATE")"
ESC_BIN_PATH="$(escape_sed "$BIN_PATH")"

echo "Applying configuration to systemd unit..."
sed -i \
  -e "s|^Description=.*|Description=SMS to Telegram Forwarder (${NAME})|" \
  -e "s|^Environment=TELEGRAM_BOT_TOKEN=.*|Environment=TELEGRAM_BOT_TOKEN=${ESC_TOKEN}|" \
  -e "s|^Environment=TELEGRAM_CHAT_IDS=.*|Environment=TELEGRAM_CHAT_IDS=${ESC_CHATS}|" \
  -e "s|^Environment=SERIAL_PORT=.*|Environment=SERIAL_PORT=${ESC_SERIAL}|" \
  -e "s|^Environment=BAUD_RATE=.*|Environment=BAUD_RATE=${ESC_BAUD}|" \
  -e "s|^ExecStart=.*|ExecStart=${ESC_BIN_PATH}|" \
  -e "s|^DeviceAllow=.*|DeviceAllow=${ESC_SERIAL} rw|" \
  "$TMP_SERVICE"

echo "Installing systemd unit to ${SERVICE_PATH}..."
mv "$TMP_SERVICE" "$SERVICE_PATH"
chmod 0644 "$SERVICE_PATH"

echo "Reloading systemd and enabling service ${SERVICE_NAME}..."
systemctl daemon-reload
systemctl enable "$SERVICE_NAME"
systemctl restart "$SERVICE_NAME"

echo "Installation complete. Service status:"
systemctl --no-pager --full status "$SERVICE_NAME"
