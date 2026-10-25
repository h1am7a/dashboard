#!/usr/bin/env bash
# ==============================================================================
# Priest Life Telegram Mini App — Automated One-Line Installer
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/h1am7a/dashboard/main/install.sh | sudo bash
# Or with pre-set variables:
#   BOT_TOKEN="xxx" ALLOWED_USER_ID="123" curl -fsSL https://raw.githubusercontent.com/h1am7a/dashboard/main/install.sh | sudo -E bash
# ==============================================================================
set -euo pipefail

# ANSI Colors
C_RESET='\033[0m'
C_CYAN='\033[36m'
C_GREEN='\033[32m'
C_YELLOW='\033[33m'
C_RED='\033[31m'
C_BOLD='\033[1m'

echo -e "${C_CYAN}${C_BOLD}"
echo "=========================================================="
echo "    Priest Life Mini App — Automated Installer"
echo "=========================================================="
echo -e "${C_RESET}"

# 1. Check Root
if [ "$(id -u)" -ne 0 ]; then
  echo -e "${C_RED}[!] Error: This installer must be run as root or with sudo.${C_RESET}"
  exit 1
fi

INSTALL_DIR="${INSTALL_DIR:-/opt/priest-panel}"
CUSTOM_DB_PATH="${DB_PATH:-}"
DB_PATH="${DB_PATH:-/root/priest/db/life.db}"
REPO_URL="https://github.com/h1am7a/dashboard.git"
SERVICE_NAME="priest-panel"

# Strictly guard against cloning into /root/priest
if [ "$INSTALL_DIR" = "/root/priest" ]; then
  echo -e "${C_RED}[!] Error: Target directory cannot be /root/priest.${C_RESET}"
  echo -e "    Panel must be installed into /opt/priest-panel."
  INSTALL_DIR="/opt/priest-panel"
fi

# 2. Check Existing Database
echo -e "${C_CYAN}[*] Database location: ${DB_PATH}${C_RESET}"
if [ -f "$DB_PATH" ]; then
  echo -e "    ${C_GREEN}[✓] Found existing database at ${DB_PATH}${C_RESET}"
else
  echo -e "    ${C_YELLOW}[i] Database not present at ${DB_PATH} yet. Existing DB will be opened once available.${C_RESET}"
fi

# 3. Install System Dependencies
echo -e "${C_CYAN}[*] Step 1/6: Installing system prerequisites...${C_RESET}"
if command -v apt-get >/dev/null 2>&1; then
  apt-get update -qq
  apt-get install -y -qq git python3 python3-pip python3-venv curl >/dev/null
elif command -v dnf >/dev/null 2>&1; then
  dnf install -y -q git python3 python3-pip curl
elif command -v yum >/dev/null 2>&1; then
  yum install -y -q git python3 python3-pip curl
fi

# 4. Clone or Update Repo
echo -e "${C_CYAN}[*] Step 2/6: Setting up project repository in ${INSTALL_DIR}...${C_RESET}"
mkdir -p "$(dirname "$INSTALL_DIR")"
if [ -d "$INSTALL_DIR/.git" ]; then
  echo "    Repository exists, updating to latest main..."
  git -C "$INSTALL_DIR" fetch origin main -q
  git -C "$INSTALL_DIR" reset --hard origin/main -q
else
  git clone -q "$REPO_URL" "$INSTALL_DIR"
fi

cd "$INSTALL_DIR"

# 5. Setup Python Virtual Environment
echo -e "${C_CYAN}[*] Step 3/6: Setting up Python virtual environment...${C_RESET}"
if [ ! -d "$INSTALL_DIR/.venv" ]; then
  python3 -m venv "$INSTALL_DIR/.venv"
fi
"$INSTALL_DIR/.venv/bin/pip" install --upgrade pip -q
"$INSTALL_DIR/.venv/bin/pip" install -r "$INSTALL_DIR/requirements.txt" -q

# 6. Configure Environment (.env)
echo -e "${C_CYAN}[*] Step 4/6: Configuring environment variables...${C_RESET}"
ENV_FILE="$INSTALL_DIR/.env"

TOKEN_VAL="${BOT_TOKEN:-}"
USER_ID_VAL="${ALLOWED_USER_ID:-}"

# Interactive prompt if running in a terminal and variables not set
if [ -f "$ENV_FILE" ] && grep -q "^BOT_TOKEN=.." "$ENV_FILE" && [ -z "$TOKEN_VAL" ]; then
  echo -e "    ${C_GREEN}[✓] Preserving existing Telegram credentials from ${ENV_FILE}${C_RESET}"
else
  if [ -z "$TOKEN_VAL" ] && [ -t 0 ]; then
    echo -e "${C_YELLOW}[?] Enter your Telegram Bot Token (from @BotFather) [or press Enter to configure later]:${C_RESET} "
    read -r TOKEN_VAL || true
  fi

  if [ -z "$USER_ID_VAL" ] && [ -t 0 ]; then
    echo -e "${C_YELLOW}[?] Enter your Telegram User ID (from @userinfobot) [or press Enter to configure later]:${C_RESET} "
    read -r USER_ID_VAL || true
  fi
fi

if [ ! -f "$ENV_FILE" ]; then
  cat <<EOF > "$ENV_FILE"
DB_PATH=${DB_PATH}
BOT_TOKEN=${TOKEN_VAL}
ALLOWED_USER_ID=${USER_ID_VAL}
DEV_MODE=false
HOST=127.0.0.1
PORT=8099
EOF
  chmod 600 "$ENV_FILE"
  echo "    Created ${ENV_FILE}"
else
  # Update existing .env if tokens were provided
  if [ -n "$TOKEN_VAL" ]; then
    sed -i "s|^BOT_TOKEN=.*|BOT_TOKEN=${TOKEN_VAL}|" "$ENV_FILE"
  fi
  if [ -n "$USER_ID_VAL" ]; then
    sed -i "s|^ALLOWED_USER_ID=.*|ALLOWED_USER_ID=${USER_ID_VAL}|" "$ENV_FILE"
  fi
  # Update DB_PATH if user explicitly provided CUSTOM_DB_PATH or missing in .env
  if [ -n "$CUSTOM_DB_PATH" ]; then
    if grep -q "^DB_PATH=" "$ENV_FILE"; then
      sed -i "s|^DB_PATH=.*|DB_PATH=${DB_PATH}|" "$ENV_FILE"
    else
      echo "DB_PATH=${DB_PATH}" >> "$ENV_FILE"
    fi
  elif ! grep -q "^DB_PATH=" "$ENV_FILE"; then
    echo "DB_PATH=${DB_PATH}" >> "$ENV_FILE"
  fi
  sed -i "/^PRIEST_DB=/d" "$ENV_FILE" 2>/dev/null || true
  echo "    Preserved existing ${ENV_FILE}"
fi

# 7. Setup Systemd Service
echo -e "${C_CYAN}[*] Step 5/6: Configuring systemd service (${SERVICE_NAME}.service)...${C_RESET}"
SYSTEMD_UNIT="/etc/systemd/system/${SERVICE_NAME}.service"

cat <<EOF > "$SYSTEMD_UNIT"
[Unit]
Description=Priest Life Telegram Mini App Panel
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=${INSTALL_DIR}
EnvironmentFile=-${INSTALL_DIR}/.env
ExecStart=${INSTALL_DIR}/.venv/bin/uvicorn app.api:app --host 127.0.0.1 --port 8099
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "${SERVICE_NAME}" -q
systemctl restart "${SERVICE_NAME}"

# 8. Verification & Health Check
echo -e "${C_CYAN}[*] Step 6/6: Verifying service health...${C_RESET}"
sleep 2

HEALTH_CHECK=""
if command -v curl >/dev/null 2>&1; then
  HEALTH_CHECK=$(curl -s http://127.0.0.1:8099/api/health || true)
fi

echo ""
if echo "$HEALTH_CHECK" | grep -q '"ok":true'; then
  echo -e "${C_GREEN}${C_BOLD}[✓] SUCCESS! Priest Life Panel is active and running on http://127.0.0.1:8099${C_RESET}"
else
  echo -e "${C_YELLOW}[!] Service started. Check status with: systemctl status ${SERVICE_NAME}${C_RESET}"
fi

echo ""
echo -e "${C_BOLD}Management Commands:${C_RESET}"
echo "  • Status:  sudo systemctl status ${SERVICE_NAME}"
echo "  • Restart: sudo systemctl restart ${SERVICE_NAME}"
echo "  • Logs:    sudo journalctl -u ${SERVICE_NAME} -f"
echo "  • Config:  nano ${INSTALL_DIR}/.env"
echo ""
echo -e "${C_BOLD}Next Steps for Telegram Mini App:${C_RESET}"
echo "  1. Expose port 8099 via HTTPS tunnel:"
echo "     • Tailscale Funnel (recommended):"
echo "       tailscale funnel --bg 8099"
echo "     • Or Cloudflare Quick Tunnel:"
echo "       cloudflared tunnel --url http://127.0.0.1:8099"
echo ""
echo "  2. In @BotFather:"
echo "     • /newapp (or Bot Settings -> Menu Button -> Configure Menu Button)"
echo "     • Paste your https:// tunnel URL"
echo "=========================================================="
