"""Eyedid gaze worker: webcam -> Eyedid -> screen pixels, as JSON lines (docs/desktop-control.md).

Run by the desktop agent as its own process, so a crash in the SDK never takes the agent down:

    python -m desktop.eyedid.worker --screen 1920x1080@344x193 [--camera 0] [--face-cm 50]

The key comes from EYEDID_DESKTOP_KEY in the environment. The worker owns the camera while it runs.
Video never leaves this process; only the lines below do.

stdout, one JSON object per line:
  {"type": "status", "state": "starting" | "running" | "error", "detail": str, "fps": float}
  {"type": "gaze", "t": epoch s, "x": px | null, "y": px | null, "fx": px | null, "fy": px | null,
   "state": "SUCCESS" | "FACE_MISSING" | "GAZE_NOT_FOUND", "move": "fixation" | "saccade" | "unknown"}
  {"type": "calib_point", "x": px, "y": px}      show a dot there; send "collect" once the eyes settle
  {"type": "calib_progress", "p": 0..1}
  {"type": "calib_done", "data": [float, ...]}    save it; the SDK already uses it
  {"type": "calib_cancel"}

stdin, one JSON object per line:
  {"cmd": "calibrate", "points": 5 | 1, "roi": [left, top, right, bottom]}   (pixels)
  {"cmd": "collect"}   {"cmd": "stop_calibration"}   {"cmd": "set_calibration", "data": [...]}
  {"cmd": "quit"}      (end of input also quits: the agent is gone)
"""

from __future__ import annotations

import argparse
import ctypes
import json
import math
import os
import sys
import threading
import time
from typing import Any, Callable, TextIO

from desktop.eyedid.convert import MISSING, Display

Emit = Callable[[dict[str, Any]], None]


def point(display: Display, x: float, y: float) -> tuple[float | None, float | None]:
    if x == MISSING or y == MISSING or not (math.isfinite(x) and math.isfinite(y)):
        return None, None  # the SDK sends -1001 or NaN when it has no point
    px, py = display.to_px(x, y)
    return round(px, 1), round(py, 1)


def gaze_message(display: Display, timestamp_ms: int, gaze: Any) -> dict[str, Any]:
    """One gaze sample from the SDK (millimetres from the camera) as a line for the agent (pixels)."""
    from desktop.eyedid.sdk import MOVEMENT_STATES, TRACKING_STATES

    x, y = point(display, gaze.x, gaze.y)
    fx, fy = point(display, gaze.fixation_x, gaze.fixation_y)
    return {
        "type": "gaze",
        "t": timestamp_ms / 1000,
        "x": x,
        "y": y,
        "fx": fx,
        "fy": fy,
        "state": TRACKING_STATES.get(gaze.tracking_state, "GAZE_NOT_FOUND"),
        "move": MOVEMENT_STATES.get(gaze.movement_state, "unknown"),
    }


def parse_screen(text: str) -> Display:
    """"1920x1080@344x193" (pixels @ millimetres) -> Display."""
    px, _, mm = text.partition("@")
    w, h = (int(v) for v in px.split("x"))
    wmm, hmm = (float(v) for v in mm.split("x"))
    return Display(w, h, wmm, hmm)


class Writer:
    """JSON lines to stdout from any thread (the SDK calls back on its own threads)."""

    def __init__(self, out: TextIO) -> None:
        self.out = out
        self.lock = threading.Lock()

    def __call__(self, msg: dict[str, Any]) -> None:
        line = json.dumps(msg, separators=(",", ":"))
        with self.lock:
            try:
                self.out.write(line + "\n")
                self.out.flush()
            except (OSError, ValueError):
                os._exit(0)  # the agent closed our pipe: nobody is listening


class Tracker:
    """The SDK tracker with its callbacks wired to `emit`."""

    def __init__(self, dll: ctypes.CDLL, key: str, display: Display, emit: Emit, face_cm: int) -> None:
        from desktop.eyedid import sdk

        self.dll = dll
        self.display = display
        self.emit = emit
        raw = key.encode()
        self.handle = dll.EyedidTrackerCreate(raw, len(raw))
        code = dll.EyedidTrackerGetAuthorizationResult(self.handle)
        if code != 0:
            raise RuntimeError(f"Eyedid refused the license key (authorization result {code}); check "
                               "EYEDID_DESKTOP_KEY is a Windows / C++ key and the laptop is online")
        options = sdk.TrackerOptions(0, 0, 1, 1, 3.14159265 / 4, 0)  # the SDK's defaults, gaze only
        dll.EyedidTrackerInit(self.handle, ctypes.byref(options))
        dll.EyedidTrackerSetFaceDistance(self.handle, face_cm * 10)
        dll.EyedidTrackerSetFPS(self.handle, 30)
        # Keep references: ctypes frees a callback whose Python object is collected.
        self._callbacks = (
            sdk.OnMetrics(self._on_metrics),
            sdk.OnDrop(lambda _u, _t: None),
            sdk.OnCalibrationNextPoint(self._on_next_point),
            sdk.OnCalibrationProgress(lambda _u, p: emit({"type": "calib_progress", "p": round(p, 3)})),
            sdk.OnCalibrationData(self._on_finish),
            sdk.OnCalibrationData(lambda _u, _d, _n: emit({"type": "calib_cancel"})),
        )
        m, d, nxt, prog, fin, cancel = self._callbacks
        dll.EyedidTrackerSetMetricsCallback(self.handle, m, d)
        dll.EyedidTrackerSetCalibrationCallback(self.handle, nxt, prog, fin, cancel)
        self.metrics = 0

    def _on_metrics(self, _user: int, timestamp: int, data: Any) -> None:
        self.metrics += 1
        self.emit(gaze_message(self.display, timestamp, data.contents.gaze))

    def _on_next_point(self, _user: int, x: float, y: float) -> None:
        px, py = self.display.to_px(x, y)
        self.emit({"type": "calib_point", "x": round(px, 1), "y": round(py, 1)})

    def _on_finish(self, _user: int, data: Any, size: int) -> None:
        self.emit({"type": "calib_done", "data": [data[i] for i in range(size)]})

    def add_frame(self, timestamp_ms: int, rgb: Any) -> None:
        h, w = rgb.shape[:2]
        self.dll.EyedidTrackerAddFrame(self.handle, timestamp_ms, rgb.ctypes.data, w, h)

    def command(self, msg: dict[str, Any]) -> None:
        cmd = msg.get("cmd")
        if cmd == "calibrate":
            left, top, right, bottom = msg.get("roi") or (
                self.display.left, self.display.top,
                self.display.left + self.display.width_px, self.display.top + self.display.height_px)
            (l_mm, t_mm), (r_mm, b_mm) = self.display.to_mm(left, top), self.display.to_mm(right, bottom)
            points = ctypes.c_int(5 if msg.get("points", 5) == 5 else 1)
            accuracy = ctypes.c_int(0)
            self.dll.EyedidTrackerStartCalibration(
                self.handle, ctypes.byref(points), ctypes.byref(accuracy), l_mm, t_mm, r_mm, b_mm, 0)
        elif cmd == "collect":
            self.dll.EyedidTrackerStartCollectSamples(self.handle)
        elif cmd == "stop_calibration":
            self.dll.EyedidTrackerStopCalibration(self.handle)
        elif cmd == "set_calibration":
            data = [float(v) for v in msg.get("data") or []]
            if data:
                self.dll.EyedidTrackerSetCalibrationData(self.handle, (ctypes.c_float * len(data))(*data), len(data))

    def close(self) -> None:
        self.dll.EyedidTrackerRemoveCallbackInterface(self.handle)
        self.dll.EyedidTrackerDeInit(self.handle)


def open_camera(index: int) -> Any:
    """DirectShow first (opens in about a second here; Media Foundation took 12 s), then MSMF."""
    os.environ.setdefault("OPENCV_VIDEOIO_MSMF_ENABLE_HW_TRANSFORMS", "0")
    import cv2

    for api in (cv2.CAP_DSHOW, cv2.CAP_MSMF):
        cap = cv2.VideoCapture(index, api)
        if cap.isOpened():
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
            cap.set(cv2.CAP_PROP_FPS, 30)
            ok, _ = cap.read()
            if ok:
                return cap
        cap.release()
    raise RuntimeError(f"camera {index} could not be opened (in use by another app, or blocked in "
                       "Windows Settings > Privacy & security > Camera)")


def read_commands(stream: TextIO, tracker: Tracker, stop: threading.Event) -> None:
    for line in stream:
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if msg.get("cmd") == "quit":
            break
        try:
            tracker.command(msg)
        except Exception as e:  # a bad command must not end tracking
            print(f"command {msg.get('cmd')} failed: {e}", file=sys.stderr)
    stop.set()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--screen", required=True, help="WxH@WMMxHMM, e.g. 1920x1080@344x193")
    parser.add_argument("--camera", type=int, default=0)
    parser.add_argument("--face-cm", type=int, default=50)
    args = parser.parse_args(argv)
    # The SDK prints its own log to stdout (fd 1). Keep a private copy of stdout for the protocol and
    # point fd 1 at stderr, so SDK chatter lands in the agent's log instead of between JSON lines.
    sys.stdout.flush()
    protocol = os.fdopen(os.dup(1), "w", encoding="utf-8", newline="\n")
    os.dup2(2, 1)
    emit = Writer(protocol)
    emit({"type": "status", "state": "starting", "detail": "loading Eyedid", "fps": 0.0})
    try:
        from desktop.eyedid import sdk

        key = os.environ.get("EYEDID_DESKTOP_KEY", "").strip()
        if not key:
            raise RuntimeError("EYEDID_DESKTOP_KEY is not set (.env)")
        dll = sdk.load()
        tracker = Tracker(dll, key, parse_screen(args.screen), emit, args.face_cm)
        emit({"type": "status", "state": "starting", "detail": "opening the camera", "fps": 0.0})
        cap = open_camera(args.camera)
    except Exception as e:
        emit({"type": "status", "state": "error", "detail": str(e), "fps": 0.0})
        return 1
    import cv2

    stop = threading.Event()
    threading.Thread(target=read_commands, args=(sys.stdin, tracker, stop), daemon=True).start()
    frames, since, failures = 0, time.monotonic(), 0
    emit({"type": "status", "state": "running", "detail": dll.EyedidVersionString().decode(), "fps": 0.0})
    try:
        while not stop.is_set():
            ok, frame = cap.read()
            if not ok:
                failures += 1
                if failures >= 30:
                    emit({"type": "status", "state": "error", "detail": "the camera stopped sending frames", "fps": 0.0})
                    return 1
                time.sleep(0.03)
                continue
            failures = 0
            tracker.add_frame(int(time.time() * 1000), cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            frames += 1
            now = time.monotonic()
            if now - since >= 2.0:
                emit({"type": "status", "state": "running", "detail": "", "fps": round(frames / (now - since), 1)})
                frames, since = 0, now
    finally:
        cap.release()
        tracker.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
