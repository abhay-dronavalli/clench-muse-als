"""Start and stop the Muse Sensor Service from the Caregiver Console.

The Sensor Service is its own process (`python -m sensor.main`), because BrainFlow's
Bluetooth transport is blocking and must not share an event loop with the Core. That
left starting it a terminal-only job, so the console had an "Enable Muse clenches"
switch that did nothing until somebody ran a command by hand. This module gives the
console a supervisor: one subprocess at a time, its output kept for the UI, and a
guaranteed kill on Core shutdown.

Only one process may own the headband. BrainFlow reports a second BLE client as
BOARD_NOT_READY_ERROR, and the Core's /ws/sensor route already refuses a second
connection, so `start` always stops the process it is replacing first.
"""

from __future__ import annotations

import logging
import re
import subprocess
import sys
import threading
from collections import deque
from pathlib import Path

log = logging.getLogger("clench.sensor_service")

# The same shape sensor/profiles.py accepts, checked here too: this name reaches a
# command line, so it must never be able to carry a path or an extra argument.
PROFILE_NAME = re.compile(r"[A-Za-z0-9_-]{1,32}")
PROFILE_DIR = Path(__file__).resolve().parent.parent / "test"
SOURCES = ("muse", "demo")
BLINK_MODES = ("auto", "on", "off")
LOG_LINES = 120


def available_profiles(directory: Path = PROFILE_DIR) -> list[str]:
    """Calibration profiles the console can offer, newest first."""
    found = [
        (path.stat().st_mtime, path.name[len("calibration.") : -len(".json")])
        for path in directory.glob("calibration.*.json")
    ]
    return [name for _, name in sorted(found, reverse=True) if PROFILE_NAME.fullmatch(name)]


class SensorService:
    """Supervises at most one `sensor.main` subprocess."""

    def __init__(self, python: str | None = None, cwd: Path | None = None) -> None:
        self.python = python or sys.executable
        self.cwd = cwd or Path(__file__).resolve().parent.parent
        self.proc: subprocess.Popen[str] | None = None
        self.profile: str | None = None
        self.source: str | None = None
        self.blink: str = "auto"
        self.lines: deque[str] = deque(maxlen=LOG_LINES)
        self.lock = threading.Lock()

    # --- state ---------------------------------------------------------------

    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def status(self) -> dict[str, object]:
        with self.lock:
            proc = self.proc
            exit_code = proc.poll() if proc is not None else None
            return {
                "running": proc is not None and exit_code is None,
                "pid": proc.pid if proc is not None and exit_code is None else None,
                "profile": self.profile,
                "source": self.source,
                "blink": self.blink,
                "exit_code": exit_code,
                "profiles": available_profiles(),
                "log": list(self.lines),
            }

    # --- control -------------------------------------------------------------

    def start(self, url: str, profile: str, source: str = "muse",
              blink: str = "auto") -> dict[str, object]:
        """Launch the Sensor Service. Replaces a running one so the headband has one owner."""
        if source not in SOURCES:
            raise ValueError(f"source must be one of {', '.join(SOURCES)}")
        if blink not in BLINK_MODES:
            raise ValueError(f"blink must be one of {', '.join(BLINK_MODES)}")
        # A demo run needs no calibration file; a real headband must not guess a threshold.
        if source == "muse":
            if not PROFILE_NAME.fullmatch(profile):
                raise ValueError("profile must contain only letters, digits, hyphens or underscores")
            if not (PROFILE_DIR / f"calibration.{profile}.json").is_file():
                raise ValueError(f"no calibration profile named {profile!r}")
        self.stop()
        args = [self.python, "-m", "sensor.main", "--source", source, "--url", url,
                "--blink", blink]
        if source == "muse":
            args += ["--profile", profile]
        with self.lock:
            self.lines.clear()
            self.lines.append(f"$ {' '.join(args[1:])}")
            try:
                # Line-buffered text so the console sees connection errors as they happen.
                self.proc = subprocess.Popen(
                    args,
                    cwd=self.cwd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            except OSError as exc:
                self.lines.append(f"could not start the Sensor Service: {exc}")
                raise ValueError(f"could not start the Sensor Service: {exc}") from exc
            self.profile, self.source, self.blink = profile, source, blink
            threading.Thread(target=self._drain, args=(self.proc,), daemon=True).start()
        log.info("sensor service started: pid %d, profile %s, source %s", self.proc.pid, profile, source)
        return self.status()

    def stop(self) -> dict[str, object]:
        """Stop the Sensor Service and wait for the headband to be released."""
        with self.lock:
            proc, self.proc = self.proc, None
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                # BrainFlow can sit in a blocking BLE call; the session is released by the kill.
                log.warning("sensor service pid %d did not exit, killing it", proc.pid)
                proc.kill()
                proc.wait(timeout=5)
            log.info("sensor service pid %d stopped", proc.pid)
            with self.lock:
                self.lines.append("Sensor Service stopped.")
        return self.status()

    def _drain(self, proc: subprocess.Popen[str]) -> None:
        """Keep the subprocess's last lines so the console can show why it failed."""
        assert proc.stdout is not None
        for line in proc.stdout:
            with self.lock:
                self.lines.append(line.rstrip())
        code = proc.wait()
        with self.lock:
            if proc is self.proc:
                self.lines.append(f"Sensor Service exited with code {code}.")
