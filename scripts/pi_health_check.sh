#!/usr/bin/env bash
set -euo pipefail

echo "== AI Security Camera =="
systemctl --no-pager --full status vision-appliance | sed -n '1,18p' || true

echo
echo "== API status =="
curl -fsS http://127.0.0.1:8080/status || true

echo
echo
echo "== Temperature =="
if command -v vcgencmd >/dev/null 2>&1; then
  vcgencmd measure_temp || true
  vcgencmd get_throttled || true
else
  cat /sys/class/thermal/thermal_zone0/temp 2>/dev/null || true
fi

echo
echo "== Storage =="
df -h / /var/lib/vision-appliance 2>/dev/null || df -h /

echo
echo "== Evidence =="
find /var/lib/vision-appliance/clips /var/lib/vision-appliance/frames \
  -maxdepth 1 -type f 2>/dev/null | wc -l | awk '{print $1 " files"}'

