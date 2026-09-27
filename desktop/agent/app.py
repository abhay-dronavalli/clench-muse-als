"""The desktop agent: eyes point, the jaw clicks, anywhere in Windows (docs/desktop-control.md).

    uv run --extra desktop python -m desktop.agent                       # Eyedid gaze, Core on 8000
    uv run --extra desktop python -m desktop.agent --gaze mouse          # the mouse stands in for the eyes
    uv run --extra desktop python -m desktop.agent --url ws://127.0.0.1:8001/ws/desktop

Everything runs on the Qt thread at 60 frames a second (`Agent.step`): the gaze samples, the Core's
messages, UI Automation results and the stand-in's keys arrive on queues from their own threads.
On start the agent asks the Core for the desktop as the input target, and asks again after every
reconnect until the person switches to the board (the palette's "Clench board" or Ctrl+F8).
"""

from __future__ import annotations

import argparse
import logging
import queue
import random
import sys
import time
from typing import Any

from desktop.agent import calibration, winput
from desktop.agent.bridge import GazeBridge, gaze_message
from desktop.agent.gaze import Gaze, SavitzkyGolay
from desktop.agent.hotkeys import Key, KeyboardHook, StandIn
from desktop.agent.interaction import (Calibrate, CancelCalibration, Click, Compose, Controller, Effect, FocusBoard,
                                       Keys, MoveTo, Press, Release, Scroll, SetTarget, ZoomShot)
from desktop.agent.links import CoreLink, EyedidSource, MouseSource
from desktop.agent.snap import Rect
from desktop.agent.uia import UiaFinder

log = logging.getLogger("clench.desktop")

ACTION_TEXT = {"place_call": "call", "send_message": "message", "speak": "say", "room_control": "room",
               "help_alert": "help"}


class Agent:
    def __init__(self, args: argparse.Namespace) -> None:
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QApplication

        from desktop.agent.overlay import Overlay

        self.args = args
        self.qscreen = QApplication.primaryScreen()
        self.display = winput.primary_display()
        d = self.display
        self.screen = Rect(0, 0, d.width_px, d.height_px)
        self.ctl = Controller(self.screen, d.px_per_mm, lookback_s=args.lookback_ms / 1000)
        sg = None if args.sg_window_ms <= 0 else SavitzkyGolay(
            args.sg_window_ms / 1000, args.sg_order, args.sg_lag_ms / 1000)
        self.gaze = Gaze(d.width_px, d.height_px, sg=sg, one_euro=not args.no_one_euro)
        self.gaze_point: tuple[float, float] | None = None
        self.inbox: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.keys: queue.Queue[Key] = queue.Queue()
        self.core = CoreLink(args.url, self.inbox)
        self.desired = "board" if args.no_take else "desktop"
        self.settings: dict[str, Any] | None = None
        self.settings_fresh = False  # the first SETTINGS after a (re)connect
        self.help_countdown: int | None = None
        self.type_into: int | None = None  # the window that had focus when the person asked to type
        self.stand_in = StandIn(self._send_gesture)
        self.source_kind = args.gaze
        if args.gaze == "eyedid":
            from core.config import load_env

            key = load_env().get("EYEDID_DESKTOP_KEY", "")
            screen_arg = f"{d.width_px}x{d.height_px}@{d.width_mm:g}x{d.height_mm:g}"
            self.source: EyedidSource | MouseSource = EyedidSource(
                self.inbox, screen_arg, {"EYEDID_DESKTOP_KEY": key}, args.camera, args.face_cm)
        else:
            self.source = MouseSource(self.inbox)
        self.eyedid_ready = False
        self.calib = calibration.Flow(self.source.command, self._later, self._calibration_ended)
        self.calib_point_at = 0.0
        self.check: calibration.Check | None = None  # the one-dot check of a loaded calibration
        self.uia = UiaFinder(lambda kind, c: self.inbox.put(("uia", (kind, c))))
        self.bridge = GazeBridge((d.width_px, d.height_px), args.bridge_port, args.bridge_origin)
        self._bridged_at = float("-inf")  # the last gaze sample sent to the board
        self._uia_at = (float("-inf"), (0.0, 0.0))
        self._top_at = 0.0
        self.overlay = Overlay(self)
        self.hook = KeyboardHook(self.keys, time.time)
        self.timer = QTimer()
        self.timer.timeout.connect(self.step)

    def start(self) -> None:
        self.overlay.show_overlay()
        for thread in (self.core, self.source, self.uia, self.hook, self.bridge):
            thread.start()
        self.timer.start(16)
        log.info("desktop agent: screen %dx%d px, %.0fx%.0f mm, gaze from %s, Core %s", self.display.width_px,
                 self.display.height_px, self.display.width_mm, self.display.height_mm, self.source_kind, self.args.url)
        a = self.args
        log.info("smoothing: %s%s", f"Savitzky-Golay {a.sg_window_ms} ms order {a.sg_order} lag {a.sg_lag_ms} ms"
                 if a.sg_window_ms > 0 else "no Savitzky-Golay", "" if a.no_one_euro else ", then One Euro")

    def stop(self) -> None:
        self._run(self.ctl.set_input_target("board"))  # never quit with the button held
        if isinstance(self.source, EyedidSource):
            self.source.stop()

    # --- the frame ------------------------------------------------------------------------------------

    def step(self) -> None:
        now = time.time()
        while True:
            try:
                kind, payload = self.inbox.get_nowait()
            except queue.Empty:
                break
            try:
                self._handle(kind, payload, now)
            except Exception:
                log.exception("handling %s failed", kind)
        while True:
            try:
                self._key(self.keys.get_nowait(), now)
            except queue.Empty:
                break
        self.stand_in.tick(now)
        fresh = self.gaze.fresh(now)
        self.gaze_point = self.gaze.point if fresh else None
        if self.check is not None and self.check.running:
            passed = self.check.feed(now, self.gaze_point)
            if passed is not None:
                self.ctl.calibration_ended()
                self.ctl.toast = (("Eye calibration checked: good" if passed
                                   else "Eye calibration looks off here: press F7 to recalibrate"), now + (4 if passed else 8))
                log.info("calibration check %s", "passed" if passed else "failed")
        self._run(self.ctl.tick(now, self.gaze.point, fresh))
        self._ask_uia(now)
        if self.gaze.last_sample_at > self._bridged_at:
            self._bridged_at = self.gaze.last_sample_at
            state = "CALIBRATING" if self.dots is not None else self.gaze.state
            self.bridge.publish(gaze_message(self.gaze.last_sample_at, self.gaze_point,
                                             fresh and self.dots is None, state))
        if now - self._top_at > 2.0:
            self._top_at = now
            self.overlay.keep_on_top()  # another topmost window may have come up over us
        self.overlay.update()

    def _handle(self, kind: str, payload: Any, now: float) -> None:
        if kind == "gaze":
            self.gaze.feed(payload)
        elif kind == "core":
            self._core(payload, now)
        elif kind == "core_status":
            self.settings_fresh = bool(payload)
            if not payload:
                self.help_countdown = None
                self.ctl.set_help(False)
                self._run(self.ctl.set_input_target("board"))  # nobody routes gestures to us now
        elif kind == "eyedid":
            self._eyedid(payload, now)
        elif kind == "uia":
            which, candidates = payload
            if which == "zoom":
                self.ctl.set_zoom_candidates(candidates)
            else:
                self.ctl.set_candidates(candidates)

    def _core(self, msg: dict[str, Any], now: float) -> None:
        kind = msg.get("type")
        if kind == "SETTINGS":
            self.settings = msg
            self._run(self.ctl.set_input_target(msg.get("input_target") or "board"))
            self.ctl.set_lang(msg.get("lang") or "en")
            if msg.get("long_clench_ms"):
                self.stand_in.long_ms = int(msg["long_clench_ms"])
            if self.settings_fresh:
                self.settings_fresh = False
                if msg.get("input_target") != self.desired:
                    self._send_target(self.desired)
            elif msg.get("input_target"):
                self.desired = msg["input_target"]  # switched from anywhere: keep it over reconnects
        elif kind == "SCREEN":
            on = msg.get("screen") == "help_countdown"
            self.help_countdown = int(msg.get("countdown") or 0) if on else None
            self._run(self.ctl.set_help(on))
        elif kind == "ACTION_RESULT":
            what = ACTION_TEXT.get(msg.get("action", ""), msg.get("action", ""))
            who = f" {msg['contact']}" if msg.get("contact") else ""
            ok = "" if msg.get("ok") else " failed"
            self.ctl.toast = (f"{what}{who}{ok} ({msg.get('detail')})", now + 4)
        elif kind == "DESKTOP_INPUT":
            self._run(self.ctl.on_gesture(msg["kind"], float(msg["t"]), now))
        elif kind == "TYPE_TEXT":
            self._type(str(msg.get("text", "")))

    def _eyedid(self, msg: dict[str, Any], now: float) -> None:
        if msg["type"] == "status":
            state = msg.get("state")
            if state == "running" and not self.eyedid_ready:
                self.eyedid_ready = True
                data = calibration.load(self.args.person, self.display)
                if data:
                    self.source.command({"cmd": "set_calibration", "data": data})
                    self.ctl.toast = (f"Eye calibration loaded ({self.args.person})", now + 4)
                    self._later(1.5, self._start_check)  # is it still right from this seat?
                else:
                    self.ctl.toast = ("Eyes not calibrated yet: press F7", now + 8)
            elif state == "error":
                self.eyedid_ready = False
                self.ctl.toast = (f"Eye tracker: {msg.get('detail')}", now + 8)
                log.error("eyedid: %s", msg.get("detail"))
            return
        if msg["type"] == "calib_point":
            self.calib_point_at = time.time()
        data = self.calib.on_worker(msg)
        if data:
            path = calibration.save(self.args.person, self.display, data)
            log.info("eye calibration saved to %s", path)

    # --- effects ----------------------------------------------------------------------------------------

    def _run(self, effects: list[Effect]) -> None:
        for e in effects:
            try:
                if isinstance(e, Click):
                    winput.click(e.x, e.y, e.button, e.double)
                    self._uia_at = (float("-inf"), (0.0, 0.0))  # the screen changed: look again
                elif isinstance(e, Keys):
                    winput.escape() if e.combo == "escape" else winput.alt_left()
                elif isinstance(e, Scroll):
                    winput.scroll(e.x, e.y, e.delta)
                elif isinstance(e, Press):
                    winput.press(e.x, e.y)
                elif isinstance(e, MoveTo):
                    winput.move_to(e.x, e.y)
                elif isinstance(e, Release):
                    winput.release(e.x, e.y)
                    self._uia_at = (float("-inf"), (0.0, 0.0))
                elif isinstance(e, ZoomShot):
                    self._zoom_shot(e)
                elif isinstance(e, SetTarget):
                    self._send_target(e.target)
                elif isinstance(e, FocusBoard):
                    hwnd = winput.board_window()
                    if hwnd is None:
                        self.ctl.toast = ("Open the board in the browser (localhost:5173)", time.time() + 5)
                    else:
                        winput.focus(hwnd)
                elif isinstance(e, Calibrate):
                    self._start_calibration()
                elif isinstance(e, CancelCalibration):
                    if self.check is not None and self.check.running:
                        self.check.cancel()
                        self.ctl.toast = ("Calibration check skipped", time.time() + 3)
                    else:
                        self.calib.cancel()
                elif isinstance(e, Compose):
                    self._compose()
            except OSError as err:
                self.ctl.toast = (f"Windows refused that: {err}", time.time() + 5)
                log.warning("%s failed: %s", type(e).__name__, err)

    def _zoom_shot(self, e: ZoomShot) -> None:
        """Hide the overlay for a moment, grab the area, then show it magnified."""
        from PySide6.QtCore import QTimer

        self.overlay.zoom_pixmap = None
        self.overlay.hide_all = True
        self.overlay.repaint()
        src = e.zoom.source
        dpr = self.overlay.dpr

        def grab() -> None:
            self.overlay.zoom_pixmap = self.qscreen.grabWindow(
                0, round(src.left / dpr), round(src.top / dpr), round(src.width / dpr), round(src.height / dpr))
            self.overlay.hide_all = False
            self.uia.inside(src)

        QTimer.singleShot(90, grab)  # let the compositor drop our drawing first

    def _compose(self) -> None:
        """Remember where the focus is, then write on the board (the Core switches the gestures)."""
        hwnd = winput.foreground()
        board = winput.board_window()
        if not hwnd or hwnd == board:
            self.ctl.toast = ("Click into a text box first, then Type", time.time() + 4)
            return
        if board is None:
            self.ctl.toast = ("Open the board in the browser (localhost:5173)", time.time() + 5)
            return
        self.type_into = hwnd
        if not self.core.send({"type": "COMPOSE"}):
            self.ctl.toast = ("The Core is not connected", time.time() + 3)
            return
        winput.focus(board)

    def _type(self, text: str) -> None:
        """TYPE_TEXT: the person confirmed it on the board. Back to their window, then type it."""
        from PySide6.QtCore import QTimer

        hwnd = self.type_into
        self.type_into = None
        if not text or hwnd is None or not winput.user32.IsWindow(hwnd):
            self.ctl.toast = ("The window to type into is gone", time.time() + 4)
            return
        winput.focus(hwnd)

        def type_now() -> None:
            try:
                winput.type_text(text)
                self.ctl.toast = ("Typed", time.time() + 2.5)
            except OSError as err:
                self.ctl.toast = (f"Windows refused the typing: {err}", time.time() + 5)

        QTimer.singleShot(200, type_now)  # let the window take the focus back first

    @property
    def dots(self) -> "calibration.Flow | calibration.Check | None":
        """The calibration or the check on screen, if any (the overlay draws either the same way)."""
        if self.calib.running:
            return self.calib
        if self.check is not None and self.check.running:
            return self.check
        return None

    @property
    def dot_since(self) -> float:
        """When the dot on screen appeared (its settle ring closes from then)."""
        return self.calib_point_at if self.calib.running else self.check.started if self.check else 0.0

    def _start_check(self) -> None:
        if self.dots is not None or self.ctl.mode == "calibrating" or not self.eyedid_ready:
            return
        point = random.choice(calibration.check_points(self.display))
        self.check = calibration.Check(point, self.display.px_per_mm, time.time())
        self.ctl.hold_still()

    def _start_calibration(self) -> None:
        if self.check is not None and self.check.running:
            self.check.cancel()  # F7 during the check: calibrate instead
        if self.source_kind != "eyedid" or not self.eyedid_ready:
            self.ctl.calibration_ended()
            self.ctl.toast = ("Calibration needs the eye tracker running", time.time() + 4)
            return
        self.calib.start(calibration.area(self.display))

    def _calibration_ended(self, how: str) -> None:
        self.ctl.calibration_ended()
        text = {"done": f"Eyes calibrated ({self.args.person})", "cancelled": "Calibration stopped",
                "failed": "Calibration could not start"}[how]
        self.ctl.toast = (text, time.time() + 4)

    def _later(self, seconds: float, fn: Any) -> None:
        from PySide6.QtCore import QTimer

        QTimer.singleShot(round(seconds * 1000), fn)

    # --- keys and the Core ----------------------------------------------------------------------------

    def _key(self, k: Key, now: float) -> None:
        if k.name == "F8" and k.ctrl:
            if k.down:
                self.desired = "board" if self.ctl.active else "desktop"
                self._send_target(self.desired)
                if self.desired == "board" and (hwnd := winput.board_window()):
                    winput.focus(hwnd)
            return
        if k.name == "F7" and k.down:
            self._run(self.ctl.start_calibration())
        elif k.name == "F10" and k.down:
            self.ctl.paused = not self.ctl.paused
            self.ctl.toast = (self.ctl.t("now_paused" if self.ctl.paused else "now_resumed"), now + 2.5)
        else:
            self.stand_in.key(k)

    def _send_gesture(self, msg: dict[str, Any]) -> None:
        if not self.core.send(msg):
            self.ctl.toast = ("The Core is not connected", time.time() + 3)

    def _send_target(self, target: str) -> None:
        self.desired = target
        if self.settings is None:
            return
        change = {k: v for k, v in self.settings.items() if v is not None}
        change["input_target"] = target
        self.core.send(change)

    def _ask_uia(self, now: float) -> None:
        """Ask for the elements near the gaze when it moved a third of the snap radius, or every 0.8 s."""
        if not self.ctl.active or self.ctl.mode not in ("pointing", "back") or self.gaze_point is None:
            return
        at, (x0, y0) = self._uia_at
        x, y = self.gaze_point
        moved = ((x - x0) ** 2 + (y - y0) ** 2) ** 0.5
        if moved > self.ctl.radius / 3 or now - at > 0.8:
            self._uia_at = (now, (x, y))
            self.uia.near(x, y, self.ctl.radius * 1.2)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Clench desktop agent: eyes point, the jaw clicks.")
    parser.add_argument("--url", default="ws://127.0.0.1:8000/ws/desktop", help="the Core's /ws/desktop")
    parser.add_argument("--gaze", choices=("eyedid", "mouse"), default="eyedid")
    parser.add_argument("--person", default="default", help="whose eye calibration to load and save")
    parser.add_argument("--camera", type=int, default=0)
    parser.add_argument("--face-cm", type=int, default=50, help="about how far the eyes are from the camera")
    parser.add_argument("--lookback-ms", type=int, default=250, help="a clench uses the highlight from this long before")
    parser.add_argument("--no-take", action="store_true", help="start with the board as the input target")
    parser.add_argument("--bridge-port", type=int, default=8766, help="the gaze bridge the board connects to")
    parser.add_argument("--bridge-origin", action="append", default=[],
                        help="another page origin allowed on the gaze bridge (localhost:5173-5175 always are)")
    smooth = parser.add_argument_group("smoothing (Savitzky-Golay, then One Euro)")
    smooth.add_argument("--sg-window-ms", type=int, default=500, help="Savitzky-Golay window; 0 turns it off")
    smooth.add_argument("--sg-order", type=int, default=2, choices=(1, 2, 3), help="polynomial order")
    smooth.add_argument("--sg-lag-ms", type=int, default=80,
                        help="read the fit this far behind the newest sample (more = smoother, slower)")
    smooth.add_argument("--no-one-euro", action="store_true", help="Savitzky-Golay only")
    args = parser.parse_args(argv)
    logging.basicConfig(format="%(asctime)s %(levelname)-7s %(name)s: %(message)s", level=logging.INFO)
    calibration.path_for(args.person)  # a bad name fails here, not after a calibration

    winput.set_dpi_aware()
    from PySide6.QtWidgets import QApplication

    app = QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(False)
    agent = Agent(args)
    agent.start()
    try:
        return app.exec()
    finally:
        agent.stop()
