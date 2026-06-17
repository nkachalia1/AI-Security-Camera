# Interview Guide

## 30-Second Pitch

AI Security Camera is a Raspberry Pi 5 edge vision appliance. It watches a room, detects motion and objects, tracks movement across zones, saves evidence clips, and generates human-readable incident reports through a local web dashboard.

The important part is not just object detection. The project shows a complete embedded system: camera ingestion, local services, storage, event processing, deployment, thermal awareness, and an operator UI.

## Demo Script

1. Open the dashboard at `http://10.0.0.199:8080`.
2. Show the live feed, FPS, detector mode, object tracks, zones, and temperature.
3. Walk into the camera view and approach the configured workbench zone.
4. Label a visible object from the dashboard and show that the active track changes to the friendly name.
5. Show the event timeline and saved evidence clips.
6. Click **Generate report** and explain how structured events become natural-language summaries.
7. Show `systemctl status vision-appliance` to prove it is deployed as a real service.

## Architecture

```text
Camera source
  -> OpenCV capture
  -> Background subtraction motion detector
  -> YOLO ONNX object detector
  -> Centroid tracker
  -> Event generator
  -> SQLite event store
  -> Clip and frame recorder
  -> FastAPI dashboard and API
```

## Engineering Decisions

- The Pi runs the service locally through systemd, so it boots into an appliance-like mode.
- YOLO is exported to ONNX on the laptop, then OpenCV DNN runs inference on the Pi. The Pi does not need PyTorch.
- Motion runs every frame, while YOLO can run every Nth frame. This keeps tracking smooth while controlling CPU and heat.
- Event generation is structured before summarization. The LLM/report layer receives facts instead of raw video.
- Clips are event-triggered with a 4-second pre-event buffer and 8-second post-event recording window, which avoids continuous disk writes and keeps storage use predictable.
- `/status` includes temperature and throttle state because sustained vision workloads can overheat a Pi 5.
- The thermal guard lowers FPS, increases the effective YOLO interval, and pauses detection at critical temperature.
- Operator labels preserve the detector's original class while showing friendly names such as `work backpack` in the UI and incident timeline.
- Optional email/SMS alerting sends warning/critical events to the operator without blocking the camera pipeline.

## Strong Talking Points

- Embedded deployment: Raspberry Pi 5, systemd, local storage, camera pipeline.
- Backend engineering: FastAPI endpoints, SQLite persistence, media serving.
- Computer vision: motion segmentation, object detection, tracking, zones, event rules.
- AI workflow: detector facts become incident reports through promptable summarization.
- Product thinking: dashboard, evidence retention, thermal safety, alerting, service recovery, human-in-the-loop labels.

## Honest Limitations

- A laptop webcam stream is useful for prototyping, but a USB camera makes the device self-contained.
- YOLOv8n is lightweight but still heats the Pi under sustained load. Active cooling is recommended.
- Centroid tracking is simple and explainable, but not as robust as Deep SORT or ByteTrack in crowded scenes.

## Next Upgrades

- Add a small fan/heatsink case and thermal alerting.
- Add authenticated dashboard access.
- Add a better tracker such as ByteTrack.
- Add zone editing from the dashboard.
- Add daily incident report export.
