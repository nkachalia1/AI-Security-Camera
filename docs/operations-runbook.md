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
- `temperature_status: hot` means reduce FPS or stop the demo.
- `temperature_status: critical` means stop the service immediately.

## Thermal Safety

If temperature reaches 85 C:

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

