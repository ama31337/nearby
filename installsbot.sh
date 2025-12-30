#!/bin/bash
set -euo pipefail

SERVICE_NAME="nearby"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BOT_DIR="${SCRIPT_DIR}"
BOT_PATH="${BOT_DIR}/bot.py"
VENV_DIR="${BOT_DIR}/venv"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
ENV_FILE="/etc/default/${SERVICE_NAME}"
REQUIREMENTS_FILE="${BOT_DIR}/requirements.txt"

LOGIN_USER="${SUDO_USER:-$(id -un)}"
LOGIN_HOME="$(getent passwd "${LOGIN_USER}" | cut -d: -f6)"

if [[ -z "${LOGIN_HOME}" ]]; then
  echo "ERROR, cannot determine home dir for user, ${LOGIN_USER}"
  exit 1
fi

if [[ "${EUID}" -ne 0 ]]; then
  echo "ERROR, run with sudo, example, sudo bash install.sh"
  exit 1
fi

echo "Service name, ${SERVICE_NAME}"
echo "Bot dir, ${BOT_DIR}"
echo "Bot path, ${BOT_PATH}"
echo "Login user, ${LOGIN_USER}"
echo "Login home, ${LOGIN_HOME}"

if [[ ! -f "${BOT_PATH}" ]]; then
  echo "ERROR, bot.py not found at ${BOT_PATH}"
  exit 1
fi

echo "Installing OS packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y python3 python3-venv python3-pip ca-certificates

PYTHON_BIN="$(command -v python3)"

echo "Creating venv at ${VENV_DIR}"
if [[ ! -d "${VENV_DIR}" ]]; then
  sudo -u "${LOGIN_USER}" "${PYTHON_BIN}" -m venv "${VENV_DIR}"
fi

VENV_PY="${VENV_DIR}/bin/python"
VENV_PIP="${VENV_DIR}/bin/pip"

echo "Upgrading pip tooling inside venv"
sudo -u "${LOGIN_USER}" "${VENV_PY}" -m pip install --upgrade pip setuptools wheel

echo "Installing requirements if present"
if [[ -f "${REQUIREMENTS_FILE}" ]]; then
  sudo -u "${LOGIN_USER}" "${VENV_PY}" -m pip install -r "${REQUIREMENTS_FILE}"
else
  echo "requirements.txt not found, will only install base packages"
fi

echo "Ensuring critical packages are installed"
sudo -u "${LOGIN_USER}" "${VENV_PY}" -m pip install --upgrade \
  httpx \
  python-dotenv \
  psutil \
  loguru \
  pyTelegramBotAPI \
  py_near

echo "Sanity check, show python and imports"
sudo -u "${LOGIN_USER}" "${VENV_PY}" - <<'PY'
import sys
import psutil
print("python_executable", sys.executable)
print("psutil_version", psutil.__version__)
PY

echo "Creating env file at ${ENV_FILE}"
if [[ ! -f "${ENV_FILE}" ]]; then
  cat > "${ENV_FILE}" <<'EOF'
PYTHONUNBUFFERED=1
PYTHONDONTWRITEBYTECODE=1
EOF
  chmod 0644 "${ENV_FILE}"
fi

echo "Creating systemd unit at ${SERVICE_FILE}"
cat > "${SERVICE_FILE}" <<EOF
[Unit]
Description=Nearby Bot Service
After=network.target

[Service]
Type=simple
User=${LOGIN_USER}
WorkingDirectory=${BOT_DIR}
EnvironmentFile=-${ENV_FILE}
ExecStart=${VENV_PY} ${BOT_PATH}
Restart=on-failure
RestartSec=5

StandardOutput=journal
StandardError=journal

NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=strict
ProtectHome=no
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectControlGroups=yes
LockPersonality=yes
RestrictRealtime=yes
RestrictSUIDSGID=yes
MemoryDenyWriteExecute=yes

UMask=027

ReadWritePaths=${BOT_DIR}

[Install]
WantedBy=multi-user.target
EOF

echo "Reloading systemd and enabling service"
systemctl daemon-reexec
systemctl daemon-reload
systemctl enable --now "${SERVICE_NAME}"

echo "Waiting briefly and checking service status"
sleep 1
systemctl is-active --quiet "${SERVICE_NAME}" && echo "Service is active" || true
systemctl --no-pager --full status "${SERVICE_NAME}" || true

SHELL_RC="${LOGIN_HOME}/.bashrc"
if [[ -n "${SHELL:-}" && "${SHELL}" == */zsh ]]; then
  SHELL_RC="${LOGIN_HOME}/.zshrc"
fi

echo "Adding aliases to ${SHELL_RC} if not present"
if [[ -f "${SHELL_RC}" ]]; then
  if ! grep -q "Aliases for ${SERVICE_NAME} systemd service" "${SHELL_RC}"; then
    {
      echo ""
      echo "# Aliases for ${SERVICE_NAME} systemd service"
      echo "alias ${SERVICE_NAME}start='sudo systemctl start ${SERVICE_NAME}'"
      echo "alias ${SERVICE_NAME}stop='sudo systemctl stop ${SERVICE_NAME}'"
      echo "alias ${SERVICE_NAME}restart='sudo systemctl restart ${SERVICE_NAME}'"
      echo "alias ${SERVICE_NAME}status='sudo systemctl status ${SERVICE_NAME}'"
      echo "alias ${SERVICE_NAME}logs='sudo journalctl -u ${SERVICE_NAME} -f'"
    } >> "${SHELL_RC}"
  else
    echo "Aliases already present, skipping"
  fi
else
  echo "Shell rc file not found at ${SHELL_RC}, skipping aliases"
fi

echo "Setup complete, reload shell rc, source ${SHELL_RC}"
echo "Logs, sudo journalctl -u ${SERVICE_NAME} -f"
