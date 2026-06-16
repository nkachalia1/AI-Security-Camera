#!/usr/bin/env bash
set -euo pipefail

echo "Stopping AI Security Camera service..."
sudo systemctl stop vision-appliance || true

if command -v vcgencmd >/dev/null 2>&1; then
  vcgencmd measure_temp || true
  vcgencmd get_throttled || true
fi

echo "Service stopped. Run 'sudo shutdown now' before unplugging power."

