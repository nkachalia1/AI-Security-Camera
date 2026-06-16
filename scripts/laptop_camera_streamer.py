from __future__ import annotations

import argparse
import logging
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import cv2

LOGGER = logging.getLogger("laptop-camera-streamer")


class CameraState:
    def __init__(self, camera_index: int, width: int, height: int, fps: int, jpeg_quality: int):
        self.camera_index = camera_index
        self.width = width
        self.height = height
        self.fps = fps
        self.jpeg_quality = jpeg_quality
        self.latest_jpeg: bytes | None = None
        self.latest_error: str | None = None
        self.lock = threading.Lock()
        self.stop = threading.Event()

    def run(self) -> None:
        capture = cv2.VideoCapture(self.camera_index, cv2.CAP_DSHOW)
        if not capture.isOpened():
            capture = cv2.VideoCapture(self.camera_index)
        if not capture.isOpened():
            self.latest_error = f"Could not open laptop camera index {self.camera_index}"
            LOGGER.error(self.latest_error)
            return

        capture.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        capture.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        capture.set(cv2.CAP_PROP_FPS, self.fps)
        LOGGER.info("Laptop camera opened on index %s", self.camera_index)

        delay = 1 / max(self.fps, 1)
        while not self.stop.is_set():
            ok, frame = capture.read()
            if not ok or frame is None:
                self.latest_error = "Camera read failed"
                time.sleep(0.25)
                continue
            ok, buffer = cv2.imencode(
                ".jpg",
                frame,
                [int(cv2.IMWRITE_JPEG_QUALITY), self.jpeg_quality],
            )
            if ok:
                with self.lock:
                    self.latest_jpeg = buffer.tobytes()
                    self.latest_error = None
            time.sleep(delay)
        capture.release()


def make_handler(state: CameraState):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path in {"/", "/health"}:
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.end_headers()
                body = "ok\n/stream.mjpg\n/latest.jpg\n"
                if state.latest_error:
                    body += f"warning: {state.latest_error}\n"
                self.wfile.write(body.encode("utf-8"))
                return

            if self.path == "/latest.jpg":
                jpeg = self._latest()
                if jpeg is None:
                    self.send_error(503, state.latest_error or "No frame yet")
                    return
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Content-Length", str(len(jpeg)))
                self.end_headers()
                self.wfile.write(jpeg)
                return

            if self.path == "/stream.mjpg":
                self.send_response(200)
                self.send_header("Age", "0")
                self.send_header("Cache-Control", "no-cache, private")
                self.send_header("Pragma", "no-cache")
                self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
                self.end_headers()
                while True:
                    jpeg = self._latest()
                    if jpeg is not None:
                        try:
                            self.wfile.write(b"--frame\r\n")
                            self.wfile.write(b"Content-Type: image/jpeg\r\n")
                            self.wfile.write(f"Content-Length: {len(jpeg)}\r\n\r\n".encode("ascii"))
                            self.wfile.write(jpeg)
                            self.wfile.write(b"\r\n")
                        except (BrokenPipeError, ConnectionResetError):
                            return
                    time.sleep(1 / max(state.fps, 1))

            self.send_error(404)

        def log_message(self, format: str, *args) -> None:
            LOGGER.info("%s - %s", self.address_string(), format % args)

        @staticmethod
        def _latest() -> bytes | None:
            with state.lock:
                return state.latest_jpeg

    return Handler


def local_ip() -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]


def main() -> None:
    parser = argparse.ArgumentParser(description="Expose a laptop webcam as an MJPEG IP camera.")
    parser.add_argument("--camera-index", type=int, default=0)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8090)
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=720)
    parser.add_argument("--fps", type=int, default=15)
    parser.add_argument("--jpeg-quality", type=int, default=82)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    state = CameraState(args.camera_index, args.width, args.height, args.fps, args.jpeg_quality)
    thread = threading.Thread(target=state.run, daemon=True)
    thread.start()

    server = ThreadingHTTPServer((args.host, args.port), make_handler(state))
    LOGGER.info("Laptop webcam stream: http://%s:%s/stream.mjpg", local_ip(), args.port)
    LOGGER.info("Preview frame: http://%s:%s/latest.jpg", local_ip(), args.port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        LOGGER.info("Stopping")
    finally:
        state.stop.set()
        server.server_close()


if __name__ == "__main__":
    main()
