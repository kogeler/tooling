#!/usr/bin/env bash

# Copyright © 2025 kogeler
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

DEFAULT_BAUD="115200"
DEFAULT_NAME="sms-to-telegram"
BIN_BASE_URL="https://github.com/kogeler/tooling/releases/download/sms-to-telegram"
SERVICE_URL="https://raw.githubusercontent.com/kogeler/tooling/refs/heads/main/sms-to-telegram/docs/sms-to-telegram.service"
HEALTH_WAIT_SECONDS=5

usage() {
  cat <<'EOF'
Usage:
  install.sh [--name NAME] --serial /dev/ttyUSB0 --token BOT_TOKEN --chats id1,id2 [--baud 115200] [--version X.Y.Z]
  install.sh --update [--name NAME] [--version X.Y.Z]

Install (default):
  --serial  Serial port path for the modem (e.g., /dev/ttyUSB0)
  --token   Telegram bot token
  --chats   Comma-separated list of chat IDs

Update-only:
  --update  Update binary and restart service (no config changes)

Optional:
  --name    Installation name (used for /opt/<name> and service name; default: sms-to-telegram)
  --baud    Serial port baud rate (default: 115200)
  --version Binary version (e.g., 1.0.2). Default: latest build without version suffix.

Secrets are written to /opt/<name>/env (root:root 0600) and referenced from the
systemd unit via EnvironmentFile=; the unit itself contains no secrets.
EOF
  exit 1
}

# Escape a value for use in the replacement side of a sed s|...|...| expression:
# backslash, ampersand, and the actual delimiter '|' must be escaped.
escape_sed() {
  printf '%s' "$1" | sed -e 's/[\\&|]/\\&/g'
}

fail() {
  echo "Error: $*" >&2
  exit 1
}

if [[ $# -eq 0 ]]; then
  usage
fi

NAME="$DEFAULT_NAME"
SERIAL_PORT=""
TELEGRAM_TOKEN=""
CHAT_IDS=""
BAUD_RATE="$DEFAULT_BAUD"
VERSION=""
UPDATE_ONLY=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --update)
      UPDATE_ONLY=1
      shift
      ;;
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
    --version)
      VERSION="${2:-}"
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

# --- Input validation -------------------------------------------------------

# Name becomes /opt/<name> and the systemd unit name: require it to start with
# an alphanumeric and contain no dots, so path traversal ("..") and hidden or
# option-like names are impossible.
if [[ ! "$NAME" =~ ^[A-Za-z0-9][A-Za-z0-9_-]*$ ]]; then
  fail "installation name must start with a letter/digit and contain only letters, digits, '_' or '-'"
fi

if [[ "$UPDATE_ONLY" -eq 0 ]]; then
  if [[ -z "$SERIAL_PORT" || -z "$TELEGRAM_TOKEN" || -z "$CHAT_IDS" ]]; then
    echo "Missing required arguments."
    usage
  fi

  if [[ ! "$SERIAL_PORT" =~ ^/[A-Za-z0-9/_.:-]+$ || "$SERIAL_PORT" == *..* ]]; then
    fail "serial port must be an absolute device path (e.g., /dev/ttyUSB0)"
  fi

  if [[ ! "$TELEGRAM_TOKEN" =~ ^[0-9]+:[A-Za-z0-9_-]+$ ]]; then
    fail "token does not look like a Telegram bot token (expected <digits>:<base64url>)"
  fi

  if [[ ! "$CHAT_IDS" =~ ^-?[0-9]+(,-?[0-9]+)*$ ]]; then
    fail "chat IDs must be a comma-separated list of non-zero integers (e.g., -100123,456)"
  fi
  IFS=',' read -r -a _chat_arr <<< "$CHAT_IDS"
  for _chat in "${_chat_arr[@]}"; do
    if [[ "$_chat" == "0" || "$_chat" == "-0" ]]; then
      fail "chat ID 0 is not a valid Telegram chat"
    fi
  done

  if [[ ! "$BAUD_RATE" =~ ^[1-9][0-9]*$ ]]; then
    fail "baud rate must be a positive integer"
  fi
fi

if [[ -n "$VERSION" && ! "$VERSION" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*$ ]]; then
  fail "version must start with a letter/digit and contain only letters, digits, '.', '_' or '-'"
fi

if [[ "$EUID" -ne 0 ]]; then
  fail "please run as root (use sudo)"
fi

command -v curl >/dev/null 2>&1 || fail "curl not found. Please install curl."
command -v systemctl >/dev/null 2>&1 || fail "systemctl not found. This script requires systemd."
command -v sha256sum >/dev/null 2>&1 || fail "sha256sum not found. Please install coreutils."

ARCH="$(uname -m)"
case "$ARCH" in
  x86_64|amd64)
    BIN_ARCH="amd64"
    ;;
  aarch64|arm64)
    BIN_ARCH="arm64"
    ;;
  *)
    fail "unsupported architecture: $ARCH"
    ;;
esac

INSTALL_DIR="/opt/${NAME}"
BIN_PATH="${INSTALL_DIR}/sms-to-telegram"
ENV_FILE="${INSTALL_DIR}/env"
SERVICE_NAME="${NAME}"
SERVICE_FILE="${SERVICE_NAME}.service"
SERVICE_PATH="/etc/systemd/system/${SERVICE_FILE}"
BIN_FILE="sms-to-telegram-linux-${BIN_ARCH}"
if [[ -n "$VERSION" ]]; then
  BIN_FILE="sms-to-telegram-${VERSION}-linux-${BIN_ARCH}"
fi
BIN_URL="${BIN_BASE_URL}/${BIN_FILE}"

TMP_BIN="$(mktemp)"
TMP_SUM="$(mktemp)"
TMP_SERVICE=""
TMP_ENV=""
cleanup() {
  rm -f "$TMP_BIN" "$TMP_SUM"
  [[ -n "$TMP_SERVICE" ]] && rm -f "$TMP_SERVICE"
  [[ -n "$TMP_ENV" ]] && rm -f "$TMP_ENV"
}
trap cleanup EXIT

# --- Download and verify binary ---------------------------------------------

download_and_verify_binary() {
  echo "Downloading binary for ${BIN_ARCH}..."
  curl -fsSL "$BIN_URL" -o "$TMP_BIN"

  # Checksums are published by CI next to each asset. Verify when available;
  # releases built before checksum publication only get a loud warning.
  if curl -fsSL "${BIN_URL}.sha256" -o "$TMP_SUM" 2>/dev/null; then
    expected="$(awk '{print $1}' "$TMP_SUM")"
    actual="$(sha256sum "$TMP_BIN" | awk '{print $1}')"
    if [[ -z "$expected" || "$expected" != "$actual" ]]; then
      fail "checksum mismatch for ${BIN_FILE}: expected ${expected:-<empty>}, got ${actual}"
    fi
    echo "Checksum verified (${actual})."
  else
    echo "WARNING: no checksum file published for ${BIN_FILE}; skipping verification." >&2
  fi
}

# Wait briefly, then verify the unit is active; used for post-restart health check.
service_healthy() {
  sleep "$HEALTH_WAIT_SECONDS"
  systemctl is-active --quiet "$SERVICE_NAME"
}

# --- Update-only path --------------------------------------------------------

if [[ "$UPDATE_ONLY" -eq 1 ]]; then
  [[ -d "$INSTALL_DIR" ]] || fail "install directory ${INSTALL_DIR} not found. Run full install first."
  [[ -f "$SERVICE_PATH" ]] || fail "service unit ${SERVICE_PATH} not found. Run full install first."

  download_and_verify_binary

  BACKUP_BIN=""
  if [[ -f "$BIN_PATH" ]]; then
    BACKUP_BIN="${BIN_PATH}.old"
    cp -p "$BIN_PATH" "$BACKUP_BIN"
  fi

  install -m 0755 -o root -g root "$TMP_BIN" "$BIN_PATH"

  echo "Restarting service ${SERVICE_NAME}..."
  systemctl restart "$SERVICE_NAME"

  if service_healthy; then
    echo "Update complete. Service status:"
    systemctl --no-pager --full status "$SERVICE_NAME"
    exit 0
  fi

  echo "Service failed to stay active after update." >&2
  if [[ -n "$BACKUP_BIN" && -f "$BACKUP_BIN" ]]; then
    echo "Rolling back to previous binary..." >&2
    install -m 0755 -o root -g root "$BACKUP_BIN" "$BIN_PATH"
    systemctl restart "$SERVICE_NAME" || true
    if service_healthy; then
      echo "Rollback succeeded; service is running the previous binary." >&2
    else
      echo "Rollback restart also failed; inspect 'journalctl -u ${SERVICE_NAME}'." >&2
    fi
  fi
  exit 1
fi

# --- Full install path -------------------------------------------------------

echo "Creating install directory at ${INSTALL_DIR}..."
mkdir -p "$INSTALL_DIR"

download_and_verify_binary
install -m 0755 -o root -g root "$TMP_BIN" "$BIN_PATH"

if ! id -u sms-forwarder >/dev/null 2>&1; then
  echo "Creating service user sms-forwarder..."
  useradd -r -s /usr/sbin/nologin -G dialout sms-forwarder
fi

echo "Writing environment file ${ENV_FILE} (0600)..."
TMP_ENV="$(mktemp "${INSTALL_DIR}/.env.XXXXXX")"
chmod 0600 "$TMP_ENV"
chown root:root "$TMP_ENV"
cat > "$TMP_ENV" <<EOF
TELEGRAM_BOT_TOKEN=${TELEGRAM_TOKEN}
TELEGRAM_CHAT_IDS=${CHAT_IDS}
SERIAL_PORT=${SERIAL_PORT}
BAUD_RATE=${BAUD_RATE}
LOG_LEVEL=INFO
EOF
mv -f "$TMP_ENV" "$ENV_FILE"
TMP_ENV=""

echo "Downloading systemd unit template..."
TMP_SERVICE="$(mktemp)"
curl -fsSL "$SERVICE_URL" -o "$TMP_SERVICE"

ESC_ENV_FILE="$(escape_sed "$ENV_FILE")"
ESC_BIN_PATH="$(escape_sed "$BIN_PATH")"
ESC_NAME="$(escape_sed "$NAME")"

echo "Applying configuration to systemd unit (no secrets in the unit)..."
sed -i \
  -e "s|^Description=.*|Description=SMS to Telegram Forwarder (${ESC_NAME})|" \
  -e "s|^EnvironmentFile=.*|EnvironmentFile=${ESC_ENV_FILE}|" \
  -e "s|^ExecStart=.*|ExecStart=${ESC_BIN_PATH}|" \
  "$TMP_SERVICE"

if grep -qE '^Environment=.*TELEGRAM_BOT_TOKEN' "$TMP_SERVICE"; then
  fail "unit template unexpectedly contains inline secrets; refusing to install"
fi

echo "Installing systemd unit to ${SERVICE_PATH}..."
install -m 0644 -o root -g root "$TMP_SERVICE" "$SERVICE_PATH"

echo "Reloading systemd and enabling service ${SERVICE_NAME}..."
systemctl daemon-reload
systemctl enable "$SERVICE_NAME"
systemctl restart "$SERVICE_NAME"

if service_healthy; then
  echo "Installation complete. Service status:"
else
  echo "WARNING: service is not active after install; inspect 'journalctl -u ${SERVICE_NAME}'." >&2
fi
systemctl --no-pager --full status "$SERVICE_NAME" || true
