#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/opt/vision-appliance"
DATA_DIR="/var/lib/vision-appliance"
SERVICE_FILE="/etc/systemd/system/vision-appliance.service"
ENV_FILE="/etc/vision-appliance.env"
APP_USER="${SUDO_USER:-pi5}"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run with sudo: sudo scripts/install_pi.sh"
  exit 1
fi

apt-get update
apt-get install -y python3-venv python3-pip python3-opencv v4l-utils rsync

mkdir -p "${APP_DIR}" "${DATA_DIR}"
rsync -a --delete \
  --exclude ".git" \
  --exclude ".venv" \
  --exclude "data" \
  ./ "${APP_DIR}/"

python3 -m venv --system-site-packages "${APP_DIR}/.venv"
"${APP_DIR}/.venv/bin/pip" install --upgrade pip
"${APP_DIR}/.venv/bin/pip" install --upgrade setuptools wheel
"${APP_DIR}/.venv/bin/pip" install -r "${APP_DIR}/requirements-pi.txt"
"${APP_DIR}/.venv/bin/pip" install "${APP_DIR}" --no-build-isolation

sed "s/^User=.*/User=${APP_USER}/" \
  "${APP_DIR}/deploy/systemd/vision-appliance.service" > "${SERVICE_FILE}"
if [[ ! -f "${ENV_FILE}" ]]; then
  cp "${APP_DIR}/deploy/systemd/vision-appliance.env" "${ENV_FILE}"
else
  while IFS= read -r line; do
    [[ "${line}" =~ ^[A-Z0-9_]+= ]] || continue
    key="${line%%=*}"
    if ! grep -q "^${key}=" "${ENV_FILE}"; then
      echo "${line}" >> "${ENV_FILE}"
    fi
  done < "${APP_DIR}/deploy/systemd/vision-appliance.env"
fi

chown -R "${APP_USER}:${APP_USER}" "${APP_DIR}" "${DATA_DIR}"
usermod -aG video "${APP_USER}"
systemctl daemon-reload
systemctl enable vision-appliance
systemctl restart vision-appliance

echo "AI Security Camera is running as ${APP_USER} at http://$(hostname -I | awk '{print $1}'):8080"
