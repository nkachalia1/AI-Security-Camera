# AI Security Camera

A Raspberry Pi 5 edge vision appliance that watches a room, detects motion and objects, tracks movement through zones, saves evidence clips, and generates incident reports from camera activity.

![AI Security Camera dashboard demo](docs/assets/ai-security-camera-demo.gif)

## Why This Project Is Strong

This is built like a small commercial edge device, not a tutorial script.

- Embedded deployment on Raspberry Pi 5 with systemd.
- Camera ingestion from USB cameras or an MJPEG network stream.
- OpenCV motion detection and centroid tracking.
- YOLOv8n ONNX object detection through OpenCV DNN.
- Event-triggered screenshots and video clips.
- SQLite event storage.
- FastAPI backend and local dashboard.
- Natural-language incident reports.
- Runtime health status including FPS, detector mode, temperature, and throttling signals.

Operational identifiers such as `vision-appliance.service`, `/opt/vision-appliance`, and the `vision-appliance` command are kept stable so existing Pi installs continue to work.

## Architecture

```text
Camera source
  -> OpenCV capture
  -> Motion detection
  -> YOLO ONNX object detection
  -> Centroid tracker
  -> Event generator
  -> SQLite event store
  -> Evidence recorder
  -> FastAPI dashboard and API
```

Useful docs:

- [Interview guide](docs/interview-guide.md)
- [Operations runbook](docs/operations-runbook.md)

## Dashboard

The local dashboard shows:

- Annotated live camera feed
- FPS, detector mode, and Pi temperature
- Active object tracks
- Configured zones
- Event timeline
- Saved clips and screenshots
- Generated reports

Open:

```text
http://<pi-ip>:8080
```

## API

- `GET /status`
- `GET /health`
- `GET /events`
- `GET /latest-frame`
- `GET /stream`
- `GET /clips`
- `GET /frames`
- `POST /reports/generate`

## Local Laptop Demo

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
pip install --upgrade setuptools wheel
pip install -e . --no-build-isolation
copy .env.example .env
vision-appliance
```

Open:

```text
http://127.0.0.1:8080
```

## Laptop Webcam As Network Camera

A laptop's built-in webcam is not a USB device the Pi can open directly. For demos, run an MJPEG streamer on Windows and point the Pi at that stream.

Windows PowerShell:

```powershell
cd "C:\Users\Neel\Documents\Security Camera"
.\.venv\Scripts\python.exe scripts\laptop_camera_streamer.py --camera-index 0 --port 8090 --fps 24 --width 960 --height 540
```

Pi env:

```bash
sudo sed -i 's|^VISION_CAMERA_SOURCE=.*|VISION_CAMERA_SOURCE=http://10.0.0.198:8090/stream.mjpg|' /etc/vision-appliance.env
sudo systemctl restart vision-appliance
curl http://127.0.0.1:8080/status
```

## Raspberry Pi Deployment

From WSL on Windows:

```bash
cd "/mnt/c/Users/Neel/Documents/Security Camera"

rsync -az --delete \
  -e "ssh -i ~/.ssh/pi5_edge" \
  --exclude ".git" \
  --exclude ".venv" \
  --exclude ".ultralytics" \
  --exclude "data" \
  ./ pi5@10.0.0.199:~/vision-appliance/

ssh -i ~/.ssh/pi5_edge -t pi5@10.0.0.199 \
  'cd ~/vision-appliance && sudo scripts/install_pi.sh'
```

Service commands on the Pi:

```bash
sudo systemctl status vision-appliance --no-pager -l
sudo journalctl -u vision-appliance -f
sudo systemctl restart vision-appliance
```

## YOLO ONNX Object Detection

Export on the laptop:

```powershell
cd "C:\Users\Neel\Documents\Security Camera"
.\.venv\Scripts\python.exe -m pip install -r requirements-yolo-export.txt
.\.venv\Scripts\python.exe scripts\export_yolo_onnx.py --model yolov8n.pt --output models\yolov8n.onnx --imgsz 640
```

Enable on the Pi:

```bash
sudo tee -a /etc/vision-appliance.env >/dev/null <<'EOF'
VISION_ONNX_MODEL=/opt/vision-appliance/models/yolov8n.onnx
VISION_LABELS_FILE=/opt/vision-appliance/models/coco.names
VISION_ONNX_INPUT_SIZE=640
VISION_DETECT_LABELS=person,backpack,handbag,suitcase,laptop,cell phone,bottle,cup,book,keyboard,mouse,remote,chair,tv
VISION_DETECTION_INTERVAL=8
VISION_CONFIDENCE_THRESHOLD=0.35
EOF

sudo systemctl restart vision-appliance
curl http://127.0.0.1:8080/status
```

`detector: onnx` means YOLO is active. Raise `VISION_DETECTION_INTERVAL` to reduce heat.

## Thermal Safety

The Pi 5 needs active cooling for sustained YOLO/OpenCV workloads.

If temperature reaches 85 C:

```bash
sudo systemctl stop vision-appliance
vcgencmd measure_temp
vcgencmd get_throttled
```

Safer demo settings:

```bash
sudo sed -i 's|^VISION_FPS=.*|VISION_FPS=12|' /etc/vision-appliance.env
sudo sed -i 's|^VISION_DETECTION_INTERVAL=.*|VISION_DETECTION_INTERVAL=12|' /etc/vision-appliance.env
sudo sed -i 's|^VISION_ONNX_INPUT_SIZE=.*|VISION_ONNX_INPUT_SIZE=320|' /etc/vision-appliance.env
sudo systemctl restart vision-appliance
```

## Project Layout

- `src/vision_appliance/camera.py` - camera capture and overlays
- `src/vision_appliance/motion_detector.py` - foreground motion regions
- `src/vision_appliance/object_detector.py` - YOLO ONNX plus fallback detector
- `src/vision_appliance/object_tracker.py` - centroid tracking
- `src/vision_appliance/event_generator.py` - incident rules
- `src/vision_appliance/video_recorder.py` - pre/post event clips and screenshots
- `src/vision_appliance/api.py` - FastAPI service
- `src/vision_appliance/static/` - dashboard
- `deploy/systemd/` - Pi service files
- `docs/` - interview and operations notes

