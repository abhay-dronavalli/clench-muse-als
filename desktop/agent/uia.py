"""Clickable elements near the gaze, from Windows UI Automation (comtypes), on a worker thread.

Walking a whole window's tree is too slow for browsers (seconds on a big page), so the finder asks
"what is under this point?" (ElementFromPoint) at a small grid of points around the gaze and walks
up from each hit to the nearest clickable ancestor. Latest request wins; results come back through
`on_result(kind, candidates)` on the worker thread (the agent hands them to the Qt thread).

Elements of the agent's own process are skipped (its overlay is click-through, but just in case).
"""

from __future__ import annotations

import logging
import math
import os
import threading
import time
from typing import Callable

from desktop.agent.snap import Candidate, Rect

log = logging.getLogger("clench.desktop.uia")

# UIA control type ids that a click makes sense on.
CLICKABLE = {
    50000: "Button", 50002: "CheckBox", 50003: "ComboBox", 50004: "Edit", 50005: "Hyperlink",
    50007: "ListItem", 50011: "MenuItem", 50013: "RadioButton", 50019: "TabItem", 50024: "TreeItem",
    50029: "DataItem", 50031: "SplitButton",
}
MAX_UP = 6  # ancestors to walk from a hit before giving up

OnResult = Callable[[str, list[Candidate]], None]


def ring_points(x: float, y: float, radius: float) -> list[tuple[float, float]]:
    """The center, 6 points at half the radius and 10 at the radius."""
    pts = [(x, y)]
    for r, n in ((radius / 2, 6), (radius, 10)):
        pts += [(x + r * math.cos(2 * math.pi * i / n), y + r * math.sin(2 * math.pi * i / n)) for i in range(n)]
    return pts


def grid_points(r: Rect, cols: int = 7, rows: int = 5) -> list[tuple[float, float]]:
    return [(r.left + r.width * (c + 0.5) / cols, r.top + r.height * (w + 0.5) / rows)
            for w in range(rows) for c in range(cols)]


class UiaFinder(threading.Thread):
    def __init__(self, on_result: OnResult) -> None:
        super().__init__(name="uia", daemon=True)
        self.on_result = on_result
        self._lock = threading.Condition()
        self._request: tuple[str, list[tuple[float, float]]] | None = None
        self.last_ms = 0.0
        self.error: str | None = None

    def near(self, x: float, y: float, radius: float) -> None:
        self._ask("point", ring_points(x, y, radius))

    def inside(self, r: Rect) -> None:
        self._ask("zoom", grid_points(r))

    def _ask(self, kind: str, points: list[tuple[float, float]]) -> None:
        with self._lock:
            self._request = (kind, points)
            self._lock.notify()

    def run(self) -> None:
        try:
            import sys

            # comtypes initializes COM on the thread that first imports it; ask for the free-threaded
            # apartment (this thread pumps no messages).
            sys.coinit_flags = 0  # type: ignore[attr-defined]
            import comtypes.client

            comtypes.client.GetModule("UIAutomationCore.dll")
            from comtypes.gen.UIAutomationClient import CUIAutomation, IUIAutomation  # type: ignore
            from ctypes import wintypes

            uia = comtypes.client.CreateObject(CUIAutomation, interface=IUIAutomation)
            walker = uia.ControlViewWalker
        except Exception as e:  # no UIA: the agent still points and zooms, it just never snaps
            self.error = f"UI Automation unavailable: {e}"
            log.warning(self.error)
            return
        me = os.getpid()
        while True:
            with self._lock:
                while self._request is None:
                    self._lock.wait()
                kind, points = self._request
                self._request = None
            started = time.perf_counter()
            found: dict[tuple[int, ...], Candidate] = {}
            for x, y in points:
                try:
                    el = uia.ElementFromPoint(wintypes.POINT(round(x), round(y)))
                    for _ in range(MAX_UP):
                        if el is None:
                            break
                        ctype = el.CurrentControlType
                        if ctype in CLICKABLE:
                            if el.CurrentProcessId != me and el.CurrentIsEnabled and not el.CurrentIsOffscreen:
                                key = tuple(el.GetRuntimeId())
                                if key not in found:
                                    b = el.CurrentBoundingRectangle
                                    found[key] = Candidate(Rect(b.left, b.top, b.right, b.bottom),
                                                           el.CurrentName or "", CLICKABLE[ctype])
                            break
                        if ctype in (50032, 50033):  # Window, Pane: stop climbing
                            break
                        el = walker.GetParentElement(el)
                except Exception:
                    continue  # an app answering badly (or going away) must not stop the others
            self.last_ms = (time.perf_counter() - started) * 1000
            try:
                self.on_result(kind, list(found.values()))
            except Exception:
                log.exception("UIA result handler failed")
