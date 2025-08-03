#!/bin/bash

# Configuration
SERVICE_NAME=nearby
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BOT_PATH="${SCRIPT_DIR}/bot.py"
BOT_DIR="${SCRIPT_DIR}"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
REQUIREMENTS_FILE="${BOT_DIR}/requirements.txt"
SHELL_RC="$HOME/.bashrc"
[[ "$SHELL" == */zsh ]] && SHELL_RC="$HOME/.zshrc"

# Ensure ~/.local/bin is in PATH
export PATH="$HOME/.local/bin:$PATH"

# Detect real user (avoid 'root' if run with sudo)
LOGIN_USER=$(logname)

# --- Ensure Python 3 is installed ---
if ! command -v python3 >/dev/null 2>&1; then
  echo "⚠️ Python 3 not found. Installing..."
  sudo apt update && sudo apt install -y python3
fi

# --- Ensure pip3 is installed ---
if ! command -v pip3 >/dev/null 2>&1; then
  echo "⚠️ pip3 not found. Installing..."
  sudo apt install -y python3-pip
fi

PYTHON_BIN=$(which python3)
PIP_BIN=$(which pip3)

# --- Ensure pipreqs is installed globally ---
if ! command -v pipreqs >/dev/null 2>&1; then
  echo "📦 Installing pipreqs globally..."
  sudo $PIP_BIN install pipreqs
fi

# --- Generate requirements.txt ---
echo "🧾 Generating requirements.txt..."
pipreqs "$BOT_DIR" --force

# --- Install detected dependencies globally ---
if [ -f "$REQUIREMENTS_FILE" ]; then
  echo "📦 Installing dependencies from requirements.txt..."
  sudo $PIP_BIN install -r "$REQUIREMENTS_FILE"
else
  echo "⚠️ requirements.txt not found, skipping automatic dependency installation"
fi

# --- Force install known required packages ---
echo "📦 Ensuring critical packages are installed globally..."
sudo $PIP_BIN install \
  httpx \
  python-dotenv \
  psutil \
  loguru \

# --- Create systemd unit ---
echo "🔧 Creating systemd unit file..."
sudo bash -c "cat > $SERVICE_FILE" <<EOF
[Unit]
Description=Nearby Bot Service
After=network.target

[Service]
Type=simple
User=${LOGIN_USER}
WorkingDirectory=${BOT_DIR}
ExecStart=${PYTHON_BIN} ${BOT_PATH}
Restart=on-failure
RestartSec=5
Environment="PYTHONUNBUFFERED=1"

[Install]
WantedBy=multi-user.target
EOF

# --- Reload and enable service ---
echo "🔄 Reloading systemd and enabling service..."
sudo systemctl daemon-reexec
sudo systemctl daemon-reload
sudo systemctl enable --now ${SERVICE_NAME}

# --- Add aliases ---
echo "🔗 Adding control aliases to $SHELL_RC"
{
  echo ""
  echo "# Aliases for ${SERVICE_NAME} systemd service"
  echo "alias ${SERVICE_NAME}start='sudo systemctl start ${SERVICE_NAME}'"
  echo "alias ${SERVICE_NAME}stop='sudo systemctl stop ${SERVICE_NAME}'"
  echo "alias ${SERVICE_NAME}restart='sudo systemctl restart ${SERVICE_NAME}'"
  echo "alias ${SERVICE_NAME}status='sudo systemctl status ${SERVICE_NAME}'"
  echo "alias ${SERVICE_NAME}logs='sudo journalctl -u ${SERVICE_NAME} -f'"
} >> "$SHELL_RC"

echo "✅ Setup complete. Run 'source $SHELL_RC' or restart your terminal."
echo "You can now use: ${SERVICE_NAME}start / stop / restart / logs"
