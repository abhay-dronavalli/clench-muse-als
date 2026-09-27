"""The agent's keyboard stand-in (hard rule 3: the app runs with no headband). Global keys, because
the focused window is whatever app the person is using, never the board:

  F8 tap          CLENCH                       F9    DOUBLE_BLINK
  F8 hold         LONG_CLENCH after long_clench_ms (help)
  F7              calibrate the eyes           F10   pause / resume clicks
  Ctrl+F8         switch the input target (desktop <-> Clench board)

The gestures go to the Core on /ws/desktop exactly as the headband's would; the Core routes them.
The keys are swallowed so the focused app never sees them. `StandIn` is the pure tap / hold logic;
`KeyboardHook` is the Windows low-level hook that feeds it.
"""

from __future__ import annotations

import ctypes
import queue
import threading
from ctypes import wintypes
from dataclasses import dataclass
from typing import Any, Callable

VK = {0x76: "F7", 0x77: "F8", 0x78: "F9", 0x79: "F10"}
VK_CONTROL = 0x11


@dataclass(frozen=True)
class Key:
    name: str  # "F7" .. "F10"
    down: bool
    ctrl: bool
    t: float  # Unix seconds


class StandIn:
    """F8 / F9 to gestures. Call `key` for every key event and `tick` every frame (a hold becomes a
    LONG_CLENCH while the key is still down, as the headband sends it)."""

    def __init__(self, send: Callable[[dict[str, Any]], None], long_ms: int = 2500) -> None:
        self.send = send
        self.long_ms = long_ms
        self._down_at: float | None = None
        self._long_sent = False

    def key(self, k: Key) -> None:
        if k.name == "F8" and not k.ctrl:
            if k.down:
                if self._down_at is None:  # ignore auto-repeat
                    self._down_at, self._long_sent = k.t, False
            elif self._down_at is not None:
                if not self._long_sent:
                    self.send({"type": "CLENCH", "t": self._down_at, "strength": 1.0})
                self._down_at = None
        elif k.name == "F9" and k.down:
            self.send({"type": "DOUBLE_BLINK", "t": k.t})

    def tick(self, now: float) -> None:
        if self._down_at is not None and not self._long_sent and now - self._down_at >= self.long_ms / 1000:
            self._long_sent = True
            self.send({"type": "LONG_CLENCH", "t": now, "duration": round(now - self._down_at, 2)})


WH_KEYBOARD_LL, WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP = 13, 0x100, 0x101, 0x104, 0x105
LLKHF_INJECTED = 0x10


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)


class KeyboardHook(threading.Thread):
    """Puts our keys on `events` (a Key each) and swallows them. Its own thread: a low-level hook runs
    on the thread that installed it, which must pump messages, and must answer fast."""

    def __init__(self, events: "queue.Queue[Key]", clock: Callable[[], float]) -> None:
        super().__init__(name="keyboard-hook", daemon=True)
        self.events = events
        self.clock = clock
        self.error: str | None = None

    def run(self) -> None:
        import time

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
        user32.CallNextHookEx.restype = ctypes.c_ssize_t
        user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
        user32.SetWindowsHookExW.restype = wintypes.HHOOK

        def proc(code: int, wparam: int, lparam: int) -> int:
            if code == 0:
                info = ctypes.cast(lparam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                name = VK.get(info.vkCode)
                if name and not info.flags & LLKHF_INJECTED:
                    ctrl = bool(user32.GetAsyncKeyState(VK_CONTROL) & 0x8000)
                    down = wparam in (WM_KEYDOWN, WM_SYSKEYDOWN)
                    self.events.put(Key(name, down, ctrl, time.time()))
                    return 1  # swallowed
            return user32.CallNextHookEx(None, code, wparam, lparam)

        self._proc = HOOKPROC(proc)  # keep a reference for the life of the hook
        hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._proc, None, 0)
        if not hook:
            self.error = f"keyboard hook failed (error {ctypes.get_last_error()})"
            return
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
