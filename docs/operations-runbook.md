# Operations Runbook

## Start

On Windows PowerShell, start the laptop camera stream if using the laptop webcam:

```powershell
cd "C:\Users\Neel\Documents\Security Camera"
.\.venv\Scripts\python.exe scripts\laptop_camera_streamer.py --camera-index 0 --port 8090 --fps 24 --width 960 --height 540
```

On the Pi:

```bash
sudo systemctl start vision-appliance
curl http://127.0.0.1:8080/status
```

Dashboard:

```text
http://10.0.0.199:8080
```

## Stop Safely

On the Pi:

```bash
sudo systemctl stop vision-appliance
sudo shutdown now
```

On Windows PowerShell, press `Ctrl+C` in the camera-streamer window.

## Health Check

On the Pi:

```bash
cd ~/vision-appliance
bash scripts/pi_health_check.sh
```

Important signals:

- `running: true` means the camera pipeline is active.
- `detector: onnx` means YOLO is enabled.
- `throttle.mode: warm` or `hot` means the service is automatically reducing FPS and YOLO frequency.
- `throttle.mode: critical` means the dashboard stays live, but detection is paused until the Pi cools.

## Evidence Clips

Event clips are saved under `/var/lib/vision-appliance/clips` by default. Each clip includes 4 seconds before the event trigger and 8 seconds after it.

The service keeps the newest 5 events, reports, and clips by default. Older items are pruned automatically through `VISION_HISTORY_LIMIT=5`.

Check clips:

```bash
ls -lh /var/lib/vision-appliance/clips
```

Tune the window:

```bash
sudo sed -i 's|^VISION_CLIP_SECONDS_BEFORE=.*|VISION_CLIP_SECONDS_BEFORE=4|' /etc/vision-appliance.env
sudo sed -i 's|^VISION_CLIP_SECONDS_AFTER=.*|VISION_CLIP_SECONDS_AFTER=8|' /etc/vision-appliance.env
sudo sed -i 's|^VISION_HISTORY_LIMIT=.*|VISION_HISTORY_LIMIT=5|' /etc/vision-appliance.env
sudo systemctl restart vision-appliance
```

If a clip opens to "No video with supported format and MIME type found," redeploy so the installer adds `ffmpeg`. New clips are encoded as H.264 MP4 files for browser playback.

## Thermal Safety

The service automatically enters degraded modes as the Pi warms up:

- Warm: lower FPS and slower object detection.
- Hot: heavier throttling.
- Critical: low-FPS stream with motion/object detection paused.

If temperature stays at or above 85 C:

```bash
sudo systemctl stop vision-appliance
vcgencmd measure_temp
vcgencmd get_throttled
```

If it does not cool below 70 C within a few minutes:

```bash
sudo shutdown now
```

Safer demo settings:

```bash
sudo sed -i 's|^VISION_FPS=.*|VISION_FPS=12|' /etc/vision-appliance.env
sudo sed -i 's|^VISION_DETECTION_INTERVAL=.*|VISION_DETECTION_INTERVAL=12|' /etc/vision-appliance.env
sudo sed -i 's|^VISION_ONNX_INPUT_SIZE=.*|VISION_ONNX_INPUT_SIZE=320|' /etc/vision-appliance.env
sudo systemctl restart vision-appliance
```

## Alerting

Alerts are disabled by default. Enable them only when the Pi has network access to your SMTP provider or SMS provider.

Email alerts:

```bash
sudo sed -i 's|^VISION_ALERTS_ENABLED=.*|VISION_ALERTS_ENABLED=true|' /etc/vision-appliance.env
sudo sed -i 's|^VISION_ALERT_EMAIL_ENABLED=.*|VISION_ALERT_EMAIL_ENABLED=true|' /etc/vision-appliance.env
sudo sed -i 's|^VISION_ALERT_EMAIL_SMTP_HOST=.*|VISION_ALERT_EMAIL_SMTP_HOST=smtp.gmail.com|' /etc/vision-appliance.env
sudo sed -i 's|^VISION_ALERT_EMAIL_SMTP_PORT=.*|VISION_ALERT_EMAIL_SMTP_PORT=587|' /etc/vision-appliance.env
sudo sed -i 's|^VISION_ALERT_EMAIL_SMTP_USER=.*|VISION_ALERT_EMAIL_SMTP_USER=your_email@gmail.com|' /etc/vision-appliance.env
sudo sed -i 's|^VISION_ALERT_EMAIL_SMTP_PASSWORD=.*|VISION_ALERT_EMAIL_SMTP_PASSWORD=your_app_password|' /etc/vision-appliance.env
sudo sed -i 's|^VISION_ALERT_EMAIL_FROM=.*|VISION_ALERT_EMAIL_FROM=your_email@gmail.com|' /etc/vision-appliance.env
sudo sed -i 's|^VISION_ALERT_EMAIL_TO=.*|VISION_ALERT_EMAIL_TO=your_email@gmail.com|' /etc/vision-appliance.env
sudo sed -i 's|^VISION_ALERT_MIN_SEVERITY=.*|VISION_ALERT_MIN_SEVERITY=warning|' /etc/vision-appliance.env
sudo sed -i 's|^VISION_ALERT_COOLDOWN_SECONDS=.*|VISION_ALERT_COOLDOWN_SECONDS=30|' /etc/vision-appliance.env
sudo sed -i 's|^VISION_PUBLIC_BASE_URL=.*|VISION_PUBLIC_BASE_URL=http://10.0.0.199:8080|' /etc/vision-appliance.env
sudo systemctl restart vision-appliance
```

SMS alerts through a carrier email-to-SMS gateway. Use this path when you have an email account and a cell number, but no Twilio sender number:

```bash
sudo sed -i 's|^VISION_ALERT_SMS_ENABLED=.*|VISION_ALERT_SMS_ENABLED=true|' /etc/vision-appliance.env
sudo sed -i 's|^VISION_ALERT_SMS_PROVIDER=.*|VISION_ALERT_SMS_PROVIDER=email_gateway|' /etc/vision-appliance.env
sudo sed -i 's|^VISION_ALERT_SMS_EMAIL_GATEWAY_TO=.*|VISION_ALERT_SMS_EMAIL_GATEWAY_TO=your_number@your-carrier-gateway.example|' /etc/vision-appliance.env
sudo systemctl restart vision-appliance
```

SMS alerts through Twilio. Use this path only after you have a Twilio sender number:

```bash
sudo sed -i 's|^VISION_ALERT_SMS_ENABLED=.*|VISION_ALERT_SMS_ENABLED=true|' /etc/vision-appliance.env
sudo sed -i 's|^VISION_ALERT_SMS_PROVIDER=.*|VISION_ALERT_SMS_PROVIDER=twilio|' /etc/vision-appliance.env
sudo sed -i 's|^VISION_ALERT_SMS_TWILIO_ACCOUNT_SID=.*|VISION_ALERT_SMS_TWILIO_ACCOUNT_SID=ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx|' /etc/vision-appliance.env
sudo sed -i 's|^VISION_ALERT_SMS_TWILIO_AUTH_TOKEN=.*|VISION_ALERT_SMS_TWILIO_AUTH_TOKEN=your_twilio_auth_token|' /etc/vision-appliance.env
sudo sed -i 's|^VISION_ALERT_SMS_TWILIO_FROM=.*|VISION_ALERT_SMS_TWILIO_FROM=+15551234567|' /etc/vision-appliance.env
sudo sed -i 's|^VISION_ALERT_SMS_TO=.*|VISION_ALERT_SMS_TO=+15557654321|' /etc/vision-appliance.env
sudo systemctl restart vision-appliance
```

Verify:

```bash
curl http://127.0.0.1:8080/config
curl http://127.0.0.1:8080/status
```

The API reports whether email/SMS are configured, but it does not return email addresses, phone numbers, SMTP passwords, or Twilio tokens.

## Label Objects

From the dashboard, use the label form under an active object track. The label is saved in SQLite and applied to matching future detections with the same detector class and similar normalized position/size.

API equivalent:

```bash
curl -X POST http://127.0.0.1:8080/objects/3/label \
  -H 'Content-Type: application/json' \
  -d '{"label":"work backpack"}'
```

## Switch From Laptop Webcam To USB Webcam

When a USB webcam is attached to the Pi:

```bash
sudo sed -i 's|^VISION_CAMERA_SOURCE=.*|VISION_CAMERA_SOURCE=|' /etc/vision-appliance.env
sudo sed -i 's|^VISION_CAMERA_INDEX=.*|VISION_CAMERA_INDEX=0|' /etc/vision-appliance.env
sudo systemctl restart vision-appliance
```

If index `0` does not work:

```bash
v4l2-ctl --list-devices
ls -l /dev/video*
```

Then try `VISION_CAMERA_INDEX=1`, `2`, etc.

## Tune Sensitivity

Quieter:

```bash
sudo sed -i 's|^VISION_MIN_MOTION_AREA=.*|VISION_MIN_MOTION_AREA=12000|' /etc/vision-appliance.env
sudo sed -i 's|^VISION_MAX_MOTION_DETECTIONS=.*|VISION_MAX_MOTION_DETECTIONS=2|' /etc/vision-appliance.env
sudo systemctl restart vision-appliance
```

More sensitive:

```bash
sudo sed -i 's|^VISION_MIN_MOTION_AREA=.*|VISION_MIN_MOTION_AREA=5000|' /etc/vision-appliance.env
sudo systemctl restart vision-appliance
```
