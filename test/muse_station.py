"""Muse Station: one window that holds the headband, so nothing else has to.

Why this exists
---------------
Every other script here opens its own Bluetooth session, uses it, and hands it
back. That is fine for one run and miserable in practice: you reconnect before
every single thing you do, BLE needs about five seconds between a release and
the next connect, and the headband powers itself off when nothing is holding it.
Calibrating, then listening, then calibrating again meant three connections and
three chances for the link to sulk.

So: leave this window open. It connects once, keeps the connection alive, and
runs the activities *inside itself* using the board it already holds. Nothing
disconnects until you press Disconnect. If the link drops on its own -- you
walked out of range, the band slipped -- it reconnects by itself, because a drop
is not you asking to stop.

Only one program may hold a Muse over BLE at a time. That is a property of the
headband, not of this code, so while the station is connected the standalone
scripts cannot run. Press Disconnect first, or just do the thing here.

    python muse_station.py                  # real Muse 2
    python muse_station.py --synthetic      # fake data, no hardware

Threading, because a GUI makes it matter
----------------------------------------
Three threads, with one rule: only the Tk thread touches a widget.

  main thread   Tk. Draws everything. Drains a queue every 50 ms.
  link thread   Owns connect / reconnect / release, and samples electrode fit.
  work thread   Whatever activity is running (calibrate, listen). At most one.

The link and work threads never call into Tk. They post messages onto
`self.queue` and the main thread renders them. Everything they both touch on the
board goes through LockedBoard, because BrainFlow's BoardShim is a thin wrapper
over native code and makes no thread-safety promises.
"""

import queue
import re
import sys
import threading
import time
import tkinter as tk
from tkinter import simpledialog, ttk
from types import SimpleNamespace

import numpy as np
from brainflow.board_shim import BoardShim, BrainFlowPresets

import clench_detect as cd
import station_activities as sa
from config import (CONNECT_HELP, board_label, build_parser,
                    eeg_channels_and_names, enable_ppg, get_board,
                    warn_about_platform)

# How long the link thread waits before deciding the stream has stalled. The
# Muse sends 256 Hz continuously, so silence this long is a real drop, not jitter.
STALL_SECONDS = 4.0
RECONNECT_WAIT = 5.0        # BLE needs a few seconds to re-advertise after a release
CONNECT_ATTEMPTS = 3
FIT_HZ = 2.0                # how often the electrode-fit bars refresh
UI_TICK_MS = 50             # how often the main thread drains the queue

# Matches check_connection.py's verdicts so the two tools agree about the same band.
FLAT_UV = 1.0
NOISY_UV = 200.0
FIT_BAR_FULL_UV = 150.0     # a full bar means "plenty of signal", not "good"

# Profile names become filenames (calibration.<name>.json), so they are kept to
# characters that cannot turn into a path or a second extension.
PROFILE_NAME = re.compile(r"^[A-Za-z0-9_-]{1,32}$")

OFF, CONNECTING, CONNECTED, LOST = "off", "connecting", "connected", "lost"
DOT = {OFF: "#6b7280", CONNECTING: "#f59e0b", CONNECTED: "#22c55e", LOST: "#ef4444"}
STATE_TEXT = {
    OFF: "disconnected",
    CONNECTING: "connecting...",
    CONNECTED: "CONNECTED",
    LOST: "link lost, retrying",
}


class LockedBoard:
    """A BoardShim that serialises every call behind one lock.

    The link thread reads the electrode fit while a work thread reads detection
    windows, both off the same native object. Neither drains the other's data --
    get_current_board_data only copies -- but two threads inside the same native
    call is asking for trouble, so they queue up here instead.
    """

    def __init__(self, board):
        self._board = board
        self._lock = threading.RLock()

    def __getattr__(self, name):
        attribute = getattr(self._board, name)
        if not callable(attribute):
            return attribute

        def locked(*args, **kwargs):
            with self._lock:
                return attribute(*args, **kwargs)

        return locked

    @property
    def board_id(self):
        # Read as a plain attribute all over clench_detect, so it must not become
        # a callable wrapper on the way through __getattr__.
        return self._board.board_id


# ===================================================================== the link

class Link:
    """Owns the board: connects, keeps it alive, releases it. Manual on and off.

    The only thing that sets `wanted` to False is the user pressing Disconnect
    (or closing the window). Everything else -- a stalled stream, a failed
    connect, a dropped band -- leaves `wanted` True, so the loop keeps trying.
    That asymmetry is the whole point of the station.
    """

    def __init__(self, args, post):
        self.args = args
        self.post = post                    # callable: puts a message on the UI queue
        self.wanted = False
        self.shutdown = False
        self.state = OFF
        self.board = None
        self.connected_since = None
        self.fs = 0
        self.channels, self.names, self.rows = [], [], {}
        self.timestamp_row = None
        self.battery_row = None
        self._last_sample_at = 0.0
        self._on_link_lost = None           # set by the Station: stop any activity
        self._thread = threading.Thread(target=self._run, name="link", daemon=True)

    def start(self):
        self._thread.start()

    def connect(self):
        self.wanted = True

    def disconnect(self):
        self.wanted = False

    def close(self):
        self.shutdown = True
        self.wanted = False
        self._thread.join(timeout=15.0)

    # ------------------------------------------------------------------ helpers

    def _set_state(self, state, detail=""):
        self.state = state
        self.post(("state", state, detail))

    def _log(self, message):
        self.post(("log", message))

    # --------------------------------------------------------------- the thread

    def _run(self):
        while not self.shutdown:
            try:
                if self.wanted and self.board is None:
                    self._open()
                elif self.wanted and self.board is not None:
                    self._watch()
                elif not self.wanted and self.board is not None:
                    self._close_board("disconnected by you")
                else:
                    time.sleep(0.1)
            except Exception as exc:        # a crash here must not kill the window
                self._log(f"!! link thread: {type(exc).__name__}: {exc}")
                self._close_board("link error")
                time.sleep(RECONNECT_WAIT)
        self._close_board(None)

    def _open(self):
        self._set_state(CONNECTING)
        board = get_board(self.args)
        label = board_label(board)
        self._log(f"connecting to {label} ...")

        for attempt in range(1, CONNECT_ATTEMPTS + 1):
            if not self.wanted or self.shutdown:
                self._set_state(OFF)
                return
            try:
                board.prepare_session()
                break
            except Exception as exc:
                self._log(f"  attempt {attempt}/{CONNECT_ATTEMPTS} failed: "
                          f"{type(exc).__name__}: {exc}")
                if attempt == CONNECT_ATTEMPTS:
                    self._log("")
                    for line in CONNECT_HELP:
                        self._log(line)
                    self._log("")
                    self._log(f"retrying in {RECONNECT_WAIT:.0f}s -- press Disconnect to stop.")
                    self._set_state(LOST, "cannot connect")
                    self._sleep_while_wanted(RECONNECT_WAIT)
                    return
                self._sleep_while_wanted(RECONNECT_WAIT)

        # Prepared. Set up the channel layout before streaming so the UI can
        # label its meters, then start the stream.
        try:
            enable_ppg(board, verbose=False)
            board.start_stream()
        except Exception as exc:
            self._log(f"!! prepared but could not stream: {type(exc).__name__}: {exc}")
            self._release(board)
            self._set_state(LOST, "no stream")
            self._sleep_while_wanted(RECONNECT_WAIT)
            return

        self.board = LockedBoard(board)
        self.fs = BoardShim.get_sampling_rate(board.board_id,
                                             BrainFlowPresets.DEFAULT_PRESET)
        self.channels, self.names = eeg_channels_and_names(board.board_id)
        # TP9, AF7, AF8, TP10 on a Muse 2: ears first and last (jaw muscle),
        # forehead in the middle (eyes). Same split clench_detect.main() makes.
        self.rows = {"emg": [self.channels[0], self.channels[3]],
                     "blink": [self.channels[1], self.channels[2]]}
        self.timestamp_row = self._optional_row(BoardShim.get_timestamp_channel,
                                                board.board_id)
        self.battery_row = self._optional_row(BoardShim.get_battery_channel,
                                              board.board_id)
        self.connected_since = time.monotonic()
        self._last_sample_at = time.monotonic()
        self._log(f"connected to {label} @ {self.fs} Hz  "
                  f"({', '.join(self.names)})")
        self.post(("board", label, self.fs, list(self.names)))
        self._set_state(CONNECTED)

    @staticmethod
    def _optional_row(getter, board_id):
        """Row index from one of BoardShim's get_*_channel calls, or None.

        The synthetic board has no battery row and raises rather than returning
        nothing, so every one of these has to be asked for defensively.
        """
        try:
            return getter(board_id, BrainFlowPresets.DEFAULT_PRESET)
        except Exception:
            return None

    def _watch(self):
        """Connected: check the stream is still flowing and sample electrode fit."""
        window = int(self.fs * cd.WINDOW_SECONDS)
        try:
            data = self.board.get_current_board_data(
                window, preset=BrainFlowPresets.DEFAULT_PRESET)
        except Exception as exc:
            self._log(f"!! read failed: {type(exc).__name__}: {exc}")
            self._drop("read failed")
            return

        now = time.monotonic()
        if data.size and data.shape[1] > 0:
            if self._is_fresh(data):
                self._last_sample_at = now
            self.post(("fit", self._fit(data)))
            battery = self._battery(data)
            if battery is not None:
                self.post(("battery", battery))

        if now - self._last_sample_at > STALL_SECONDS:
            self._log(f"!! no data for {STALL_SECONDS:.0f}s -- the link dropped.")
            self._drop("stalled")
            return

        self.post(("uptime", now - self.connected_since))
        time.sleep(1.0 / FIT_HZ)

    def _is_fresh(self, data):
        """Is the newest sample recent? Falls back to 'data exists' with no clock.

        BrainFlow's timestamp row is unix epoch seconds. Comparing it to the wall
        clock is the only honest staleness test: the ring buffer keeps returning
        the same old samples after a drop, so "I got data" proves nothing.
        """
        if self.timestamp_row is None:
            return True
        newest = float(data[self.timestamp_row][-1])
        return (time.time() - newest) < STALL_SECONDS

    def _fit(self, data):
        """Per-electrode (name, microvolt spread, verdict) for the fit bars."""
        readings = []
        for row, name in zip(self.channels, self.names):
            spread = float(np.std(data[row])) if data.shape[1] else 0.0
            if spread < FLAT_UV:
                verdict = "FLAT?"
            elif spread > NOISY_UV:
                verdict = "NOISY?"
            else:
                verdict = "ok"
            readings.append((name, spread, verdict))
        return readings

    def _battery(self, data):
        if self.battery_row is None or not data.shape[1]:
            return None
        value = float(data[self.battery_row][-1])
        return value if 0.0 <= value <= 100.0 else None

    def _drop(self, why):
        """The link failed on its own: tear down but keep wanting to be connected."""
        if self._on_link_lost:
            self._on_link_lost()
        self._close_board(why, next_state=LOST)
        if self.wanted and not self.shutdown:
            self._log(f"reconnecting in {RECONNECT_WAIT:.0f}s ...")
            self._sleep_while_wanted(RECONNECT_WAIT)

    def _close_board(self, why, next_state=OFF):
        if self.board is None:
            if why:
                self._set_state(next_state, why)
            return
        if self._on_link_lost:
            self._on_link_lost()
        self._release(self.board)
        self.board = None
        self.connected_since = None
        if why:
            self._log(f"session released ({why}).")
            self._set_state(next_state, why)

    @staticmethod
    def _release(board):
        for cleanup in (board.stop_stream, board.release_session):
            try:
                cleanup()
            except Exception:
                pass

    def _sleep_while_wanted(self, seconds):
        """Sleep, but wake early if the user presses Disconnect."""
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if not self.wanted or self.shutdown:
                return
            time.sleep(0.1)


# ================================================================ activity glue

class StationUI:
    """clench_detect's ConsoleUI, rewired to a Tk queue instead of stdout.

    Every method here is called on the work thread, so not one of them touches a
    widget: they post messages and return. `wait` is the interesting one -- the
    console version blocks on input(), and this one blocks on a button.
    """

    def __init__(self, post, stop_event, ready_event):
        self.post = post
        self.stop_event = stop_event
        self.ready_event = ready_event

    def log(self, message=""):
        self.post(("log", message))

    def instruct(self, headline, detail=""):
        self.post(("instruct", headline, detail))

    def wait(self, prompt):
        """Block the activity until the user clicks Ready (or presses Stop)."""
        self.ready_event.clear()
        self.post(("prompt", prompt.strip()))
        while not self.ready_event.wait(timeout=0.1):
            if self.stop_event.is_set():
                self.post(("prompt", None))
                raise cd.Cancelled()
        self.post(("prompt", None))

    def progress(self, label, remaining, levels):
        self.post(("progress", label, remaining, levels))

    def progress_done(self):
        self.post(("progress", None, 0.0, None))

    def event(self, name, detail, elapsed):
        self.post(("event", name, detail, elapsed))

    def tick(self, levels, recognizer, recent):
        hold = recognizer.long_blink.threshold if recognizer.long_blink else None
        self.post(("tick", levels, recognizer.clench.threshold,
                   recognizer.blink.threshold, hold))

    def should_stop(self):
        return self.stop_event.is_set()


# ================================================================== the station

class Station(tk.Tk):
    def __init__(self, args):
        super().__init__()
        self.title("Muse Station")
        self.minsize(560, 620)
        self.args = args
        self.queue = queue.Queue()
        self.link = Link(args, self.queue.put)
        self.link._on_link_lost = self._stop_activity

        self.work_thread = None
        self.stop_event = threading.Event()
        self.ready_event = threading.Event()
        self.activity_name = None
        self.activity_window = None

        self._build()
        self.bind("<Return>", lambda _event: self._on_ready())
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.link.start()
        self.after(UI_TICK_MS, self._drain)
        self._refresh_buttons()

    # ---------------------------------------------------------------- the layout

    def _build(self):
        pad = {"padx": 10, "pady": 4}
        root = ttk.Frame(self, padding=10)
        root.pack(fill="both", expand=True)

        # --- header: the one thing you look at from across the room ---------
        header = ttk.Frame(root)
        header.pack(fill="x")
        self.dot = tk.Canvas(header, width=18, height=18, highlightthickness=0)
        self.dot_id = self.dot.create_oval(2, 2, 16, 16, fill=DOT[OFF], outline="")
        self.dot.pack(side="left", padx=(0, 8))
        self.state_label = ttk.Label(header, text=STATE_TEXT[OFF],
                                     font=("Segoe UI", 14, "bold"))
        self.state_label.pack(side="left")
        self.detail_label = ttk.Label(header, text="", foreground="#6b7280")
        self.detail_label.pack(side="left", padx=8)

        self.meta_label = ttk.Label(root, text="no board", foreground="#6b7280")
        self.meta_label.pack(fill="x", pady=(2, 8))

        # --- connection controls -------------------------------------------
        controls = ttk.LabelFrame(root, text="Connection", padding=8)
        controls.pack(fill="x")
        self.connect_button = ttk.Button(controls, text="Connect",
                                         command=self._on_connect)
        self.connect_button.grid(row=0, column=0, sticky="w", **pad)
        self.disconnect_button = ttk.Button(controls, text="Disconnect",
                                            command=self._on_disconnect)
        self.disconnect_button.grid(row=0, column=1, sticky="w", **pad)

        self.synthetic_var = tk.BooleanVar(value=bool(self.args.synthetic))
        self.synthetic_check = ttk.Checkbutton(
            controls, text="synthetic (no hardware)", variable=self.synthetic_var)
        self.synthetic_check.grid(row=0, column=2, sticky="w", **pad)

        ttk.Label(controls, text="device").grid(row=1, column=0, sticky="e", **pad)
        self.name_var = tk.StringVar(value=self.args.name or "")
        self.name_entry = ttk.Entry(controls, textvariable=self.name_var, width=18)
        self.name_entry.grid(row=1, column=1, sticky="w", **pad)
        ttk.Label(controls, text="e.g. Muse-1234, blank = first one seen",
                  foreground="#6b7280").grid(row=1, column=2, sticky="w", **pad)

        # --- electrode fit --------------------------------------------------
        fit = ttk.LabelFrame(root, text="Electrode fit", padding=8)
        fit.pack(fill="x", pady=(8, 0))
        self.fit_rows = {}
        for index in range(4):
            name = ttk.Label(fit, text="--", width=6, font=("Consolas", 10))
            name.grid(row=index, column=0, sticky="w", padx=(0, 6), pady=2)
            bar = Bar(fit, width=200)
            bar.grid(row=index, column=1, sticky="w", pady=2)
            value = ttk.Label(fit, text="", width=10, font=("Consolas", 9),
                              foreground="#6b7280")
            value.grid(row=index, column=2, sticky="w", padx=6)
            verdict = ttk.Label(fit, text="", width=7, font=("Consolas", 9))
            verdict.grid(row=index, column=3, sticky="w")
            self.fit_rows[index] = (name, bar, value, verdict)
        ttk.Label(fit, text="FLAT? = no contact (wet the skin, move hair).   "
                            "NOISY? = loose electrode or mains hum.",
                  foreground="#6b7280").grid(row=4, column=0, columnspan=4,
                                             sticky="w", pady=(6, 0))

        # --- live signal ----------------------------------------------------
        signal = ttk.LabelFrame(root, text="Live signal", padding=8)
        signal.pack(fill="x", pady=(8, 0))
        ttk.Label(signal, text="clench", width=6,
                  font=("Consolas", 10)).grid(row=0, column=0, sticky="w")
        self.emg_bar = Bar(signal, width=200, marked=True)
        self.emg_bar.grid(row=0, column=1, sticky="w", pady=2)
        self.emg_value = ttk.Label(signal, text="--", width=22,
                                   font=("Consolas", 9), foreground="#6b7280")
        self.emg_value.grid(row=0, column=2, sticky="w", padx=6)

        ttk.Label(signal, text="blink", width=6,
                  font=("Consolas", 10)).grid(row=1, column=0, sticky="w")
        self.blink_bar = Bar(signal, width=200, marked=True)
        self.blink_bar.grid(row=1, column=1, sticky="w", pady=2)
        self.blink_value = ttk.Label(signal, text="--", width=22,
                                     font=("Consolas", 9), foreground="#6b7280")
        self.blink_value.grid(row=1, column=2, sticky="w", padx=6)

        self.last_event = ttk.Label(signal, text="", font=("Segoe UI", 13, "bold"),
                                    foreground="#2563eb")
        self.last_event.grid(row=2, column=0, columnspan=3, sticky="w", pady=(6, 0))

        # --- activities -----------------------------------------------------
        activity = ttk.LabelFrame(root, text="Run", padding=8)
        activity.pack(fill="x", pady=(8, 0))
        ttk.Label(activity, text="profile").grid(row=0, column=0, sticky="e",
                                                 padx=(0, 6))
        self.profile_var = tk.StringVar(value=self.args.profile)
        self.profile_box = ttk.Combobox(activity, textvariable=self.profile_var,
                                        width=14, values=cd.list_profiles())
        self.profile_box.grid(row=0, column=1, sticky="w")
        self.new_profile_button = ttk.Button(activity, text="New...", width=7,
                                            command=self._on_new_profile)
        self.new_profile_button.grid(row=0, column=2, padx=(4, 10))

        self.calibrate_button = ttk.Button(activity, text="Calibrate",
                                           command=self._on_calibrate)
        self.calibrate_button.grid(row=0, column=3, padx=4)
        self.listen_button = ttk.Button(activity, text="Listen",
                                        command=self._on_listen)
        self.listen_button.grid(row=0, column=4, padx=4)
        self.flappy_button = ttk.Button(activity, text="Flappy",
                                       command=self._on_flappy)
        self.flappy_button.grid(row=0, column=5, padx=4)
        self.drill_button = ttk.Button(activity, text="Drill",
                                       command=self._on_drill)
        self.drill_button.grid(row=0, column=6, padx=4)
        self.stop_button = ttk.Button(activity, text="Stop", command=self._on_stop)
        self.stop_button.grid(row=0, column=7, padx=4)

        self.quick_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(activity, text="quick (rest only, no clench/blink reps)",
                        variable=self.quick_var).grid(row=1, column=0, columnspan=8,
                                                      sticky="w", pady=(6, 0))
        self.blink_only_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(activity, text="blink only (keep saved clench numbers)",
                        variable=self.blink_only_var).grid(row=2, column=0,
                                                           columnspan=8, sticky="w")

        # --- the prompt, when an activity is waiting on you -----------------
        self.prompt_frame = ttk.Frame(root, padding=(0, 8))
        self.prompt_label = ttk.Label(self.prompt_frame, text="",
                                      font=("Segoe UI", 12, "bold"),
                                      foreground="#b45309", wraplength=420)
        self.prompt_label.pack(side="left")
        self.ready_button = ttk.Button(self.prompt_frame, text="Ready",
                                       command=self._on_ready)
        self.ready_button.pack(side="left", padx=10)

        # --- log ------------------------------------------------------------
        log_frame = ttk.LabelFrame(root, text="Log", padding=4)
        log_frame.pack(fill="both", expand=True, pady=(8, 0))
        self.log_text = tk.Text(log_frame, height=10, wrap="none",
                                font=("Consolas", 9), state="disabled")
        scroll = ttk.Scrollbar(log_frame, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scroll.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

    # ------------------------------------------------------------- UI callbacks

    def _on_connect(self):
        # Board choice can only change while disconnected, so read it here.
        self.args.synthetic = self.synthetic_var.get()
        self.args.name = self.name_var.get().strip() or None
        self.link.connect()
        self._refresh_buttons()

    def _on_disconnect(self):
        self._stop_activity()
        self.link.disconnect()
        self._refresh_buttons()

    def _on_ready(self):
        self.ready_event.set()

    def _on_stop(self):
        self._stop_activity()

    def _on_calibrate(self):
        # Calibration gets its own full-screen window: you take your glasses off
        # to put the band on, which is exactly when the small text stops working.
        self._start_activity("calibrate", self._calibrate,
                             window=lambda: sa.CalibrationWindow(
                                 self, self._on_window_closed, self._on_ready))

    def _on_listen(self):
        self._start_activity("listen", self._listen)

    def _on_flappy(self):
        self._start_activity("flappy", self._interactive,
                             window=lambda: sa.FlappyWindow(self,
                                                            self._on_window_closed))

    def _on_drill(self):
        # Which inputs the drill can ask for depends on the profile, and that is a
        # cheap file read, so it happens here rather than in the worker: the window
        # has to know what it is drilling before it opens.
        inputs = [sa.CLENCH]
        calibration = self._peek_calibration()
        if calibration and calibration.get("hold_threshold"):
            inputs.append(sa.LONG_BLINK)
        else:
            self._log("!! this profile has no long-blink calibration, so the drill "
                      "can only test the clench.")
            self._log("   Recalibrate to enable the second input.")
        self._start_activity("drill", self._interactive,
                             window=lambda: sa.DrillWindow(self,
                                                           self._on_window_closed,
                                                           inputs=inputs))

    def _peek_calibration(self):
        """Read the selected profile's calibration on the Tk thread, quietly."""
        board = self.link.board
        if board is None:
            return None
        try:
            return cd.load_calibration(board, self._profile_name(),
                                       StationUI(self.queue.put, self.stop_event,
                                                 self.ready_event))
        except Exception as exc:
            self._log(f"!! could not read the profile: {exc}")
            return None

    def _profile_name(self):
        return self.profile_var.get().strip() or "default"

    def _on_new_profile(self):
        """Claim a new profile name. It becomes a file when calibration saves it."""
        name = simpledialog.askstring(
            "New profile",
            "Name for the new profile\n(letters, digits, - and _):",
            parent=self)
        if name is None:
            return
        name = name.strip()
        if not PROFILE_NAME.match(name):
            self._log(f"!! '{name}' is not a usable profile name. Use letters, "
                      "digits, - or _, up to 32 characters.")
            return
        if name in cd.list_profiles():
            self._log(f"profile '{name}' already exists -- selected it.")
        else:
            self._log(f"profile '{name}' selected. It is empty until you press "
                      "Calibrate.")
        values = sorted(set(cd.list_profiles()) | {name})
        self.profile_box.configure(values=values)
        self.profile_var.set(name)

    def _on_window_closed(self):
        """The game window was closed by the user: that ends the activity."""
        self.activity_window = None
        self._stop_activity()

    def _close_activity_window(self):
        window, self.activity_window = self.activity_window, None
        if window is not None and window.alive:
            window.close()

    def _on_close(self):
        self._log("closing: stopping work and releasing the headband ...")
        self._close_activity_window()
        self._stop_activity()
        self.update_idletasks()
        self.link.close()
        self.destroy()

    # ---------------------------------------------------------------- activities

    def _args_for_run(self):
        """A clench_detect-shaped args object built from the checkboxes.

        Tk widget state may only be read on the Tk thread, so this is called when
        the button is pressed and the result is handed to the work thread. Reading
        a BooleanVar from the worker raises "main thread is not in main loop".
        """
        return SimpleNamespace(
            profile=self._profile_name(),
            baseline_seconds=10.0,
            k=6.0,
            no_clench_cal=self.quick_var.get(),
            blink_only=self.blink_only_var.get(),
            long_ms=1500,
            double_ms=700,
            long_blink_ms=cd.LONG_BLINK_MS,
        )

    def _start_activity(self, name, target, window=None):
        if self.link.state != CONNECTED:
            self._log("!! connect first.")
            return
        if self.work_thread and self.work_thread.is_alive():
            self._log("!! something is already running -- press Stop.")
            return
        if window is not None:
            # Created here because this runs on the Tk thread; a worker must never
            # build a widget.
            self.activity_window = window()
        self.stop_event.clear()
        self.ready_event.clear()
        self.activity_name = name
        run_args = self._args_for_run()        # read the widgets here, not in the worker
        ui = StationUI(self.queue.put, self.stop_event, self.ready_event)
        self.work_thread = threading.Thread(target=self._guarded,
                                            args=(target, ui, run_args),
                                            name=f"work-{name}", daemon=True)
        self.work_thread.start()
        self._refresh_buttons()

    def _guarded(self, target, ui, run_args):
        """Run one activity on the work thread, surviving anything it throws."""
        try:
            target(ui, run_args)
        except cd.Cancelled:
            self.queue.put(("log", "stopped."))
        except Exception as exc:
            self.queue.put(("log", f"!! {type(exc).__name__}: {exc}"))
        finally:
            self.queue.put(("done",))

    def _calibrate(self, ui, args):
        board = self.link.board
        if board is None:
            raise cd.Cancelled()
        window = int(self.link.fs * cd.WINDOW_SECONDS)
        calibration = cd.calibrate(board, self.link.rows, self.link.fs,
                                   window, args, ui)
        if calibration:
            cd.describe(calibration, ui)
            self.queue.put(("profiles", cd.list_profiles()))

    def _listen(self, ui, args):
        self._detect(ui, args, emit_start=False)

    def _interactive(self, ui, args):
        """Flappy and the drill: same loop, but clenches report on the rising edge."""
        self._detect(ui, args, emit_start=True)

    def _detect(self, ui, args, emit_start):
        board = self.link.board
        if board is None:
            raise cd.Cancelled()
        calibration = cd.load_calibration(board, args.profile, ui)
        if not calibration:
            ui.log("!! no usable calibration for this profile. Press Calibrate first.")
            return
        ui.log(f"loaded calibration saved {calibration['saved_at']}")
        ui.log("  (recalibrate if the band has moved since then)")
        cd.describe(calibration, ui)
        window = int(self.link.fs * cd.WINDOW_SECONDS)
        cd.detect_loop(board, self.link.rows, self.link.fs, window,
                       calibration, args, ui, emit_start=emit_start)

    def _stop_activity(self):
        """Ask the activity to stop. Safe from any thread, and safe if idle."""
        self.stop_event.set()
        self.ready_event.set()          # unblock a wait() that is sitting on Ready

    # --------------------------------------------------------- queue -> widgets

    def _drain(self):
        """The only place widgets get written. Runs on the Tk thread every 50 ms."""
        try:
            while True:
                message = self.queue.get_nowait()
                self._render(message)
        except queue.Empty:
            pass
        self.after(UI_TICK_MS, self._drain)

    def _render(self, message):
        kind = message[0]

        if kind == "log":
            self._log(message[1])

        elif kind == "state":
            _, state, detail = message
            self.dot.itemconfigure(self.dot_id, fill=DOT[state])
            self.state_label.configure(text=STATE_TEXT[state])
            self.detail_label.configure(text=detail)
            if state != CONNECTED:
                self._clear_live()
            self._refresh_buttons()

        elif kind == "board":
            _, label, fs, names = message
            self.meta_label.configure(
                text=f"{label} @ {fs} Hz   channels {', '.join(names)}")
            for index, name in enumerate(names[:4]):
                self.fit_rows[index][0].configure(text=name)

        elif kind == "uptime":
            self._set_meta_suffix(f"up {self._duration(message[1])}")

        elif kind == "battery":
            self._set_meta_suffix(f"battery {message[1]:.0f}%", slot="battery")

        elif kind == "fit":
            for index, (name, spread, verdict) in enumerate(message[1][:4]):
                label, bar, value, verdict_label = self.fit_rows[index]
                label.configure(text=name)
                bar.set(min(1.0, spread / FIT_BAR_FULL_UV),
                        color=("#22c55e" if verdict == "ok" else "#f59e0b"))
                value.configure(text=f"{spread:6.1f} uV")
                verdict_label.configure(
                    text=verdict,
                    foreground="#16a34a" if verdict == "ok" else "#b45309")

        elif kind == "instruct":
            _, headline, detail = message
            if self.activity_window is not None:
                self.activity_window.instruct(headline, detail)

        elif kind == "progress":
            _, label, remaining, levels = message
            if label is None:
                self.last_event.configure(text="")
            else:
                self.last_event.configure(text=f"{label}   {remaining:4.1f}s left")
                self._show_levels(levels, None, None)
            if self.activity_window is not None:
                self.activity_window.set_countdown(None if label is None
                                                   else remaining)

        elif kind == "tick":
            _, levels, emg_threshold, blink_threshold, hold_threshold = message
            self._show_levels(levels, emg_threshold, blink_threshold)
            if self.activity_window is not None:
                self.activity_window.on_levels(levels, emg_threshold, hold_threshold)

        elif kind == "event":
            _, name, detail, elapsed = message
            self.last_event.configure(text=f"{name}    {detail}")
            self._log(f"  [{elapsed:6.1f}s]  {name:<13} {detail}")
            if self.activity_window is not None:
                self.activity_window.on_gesture(name, detail)

        elif kind == "prompt":
            prompt = message[1]
            if prompt:
                self.prompt_label.configure(text=prompt)
                self.prompt_frame.pack(fill="x", after=self.meta_label)
            else:
                self.prompt_frame.pack_forget()
            if self.activity_window is not None:
                self.activity_window.set_prompt(prompt)

        elif kind == "profiles":
            self.profile_box.configure(values=message[1])

        elif kind == "done":
            self.activity_name = None
            # Gestures have stopped arriving, so a game window would be dead.
            self._close_activity_window()
            self.prompt_frame.pack_forget()
            self.last_event.configure(text="")
            self._refresh_buttons()

    # ------------------------------------------------------------ small helpers

    def _show_levels(self, levels, emg_threshold, blink_threshold):
        if levels is None:
            return
        self.emg_bar.set(*_bar_for(levels.emg, emg_threshold))
        self.blink_bar.set(*_bar_for(levels.blink, blink_threshold))
        fires = f"  fires at {emg_threshold:5.1f}" if emg_threshold else ""
        self.emg_value.configure(text=f"{levels.emg:6.1f} uV{fires}")
        fires = f"  fires at {blink_threshold:5.1f}" if blink_threshold else ""
        self.blink_value.configure(
            text=f"L{levels.blink_left:5.0f} R{levels.blink_right:5.0f}{fires}")

    def _clear_live(self):
        for _, bar, value, verdict in self.fit_rows.values():
            bar.set(0.0)
            value.configure(text="")
            verdict.configure(text="")
        self.emg_bar.set(0.0)
        self.blink_bar.set(0.0)
        self.emg_value.configure(text="--")
        self.blink_value.configure(text="--")

    def _set_meta_suffix(self, text, slot="uptime"):
        """Keep uptime and battery in the header without either clobbering the other."""
        if not hasattr(self, "_meta_slots"):
            self._meta_slots = {}
        self._meta_slots[slot] = text
        base = self.meta_label.cget("text").split("   |   ")[0]
        extras = "   ".join(self._meta_slots[k] for k in sorted(self._meta_slots))
        self.meta_label.configure(text=f"{base}   |   {extras}" if extras else base)

    @staticmethod
    def _duration(seconds):
        minutes, secs = divmod(int(seconds), 60)
        hours, minutes = divmod(minutes, 60)
        return f"{hours}h{minutes:02d}m" if hours else f"{minutes}m{secs:02d}s"

    def _refresh_buttons(self):
        connected = self.link.state == CONNECTED
        busy = bool(self.work_thread and self.work_thread.is_alive())
        wanted = self.link.wanted

        self.connect_button.configure(state="disabled" if wanted else "normal")
        self.disconnect_button.configure(state="normal" if wanted else "disabled")
        # Board choice is baked in at connect time, so freeze it while we want one.
        frozen = "disabled" if wanted else "normal"
        self.synthetic_check.configure(state=frozen)
        self.name_entry.configure(state=frozen)

        runnable = "normal" if (connected and not busy) else "disabled"
        self.calibrate_button.configure(state=runnable)
        self.listen_button.configure(state=runnable)
        self.flappy_button.configure(state=runnable)
        self.drill_button.configure(state=runnable)
        self.new_profile_button.configure(state="disabled" if busy else "normal")
        self.stop_button.configure(state="normal" if busy else "disabled")
        self.profile_box.configure(state="readonly" if not busy else "disabled")

    def _log(self, message):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", f"{time.strftime('%H:%M:%S')}  {message}\n")
        # Keep the log bounded: this window stays open for hours.
        if int(self.log_text.index("end-1c").split(".")[0]) > 500:
            self.log_text.delete("1.0", "200.0")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")


def _bar_for(level, threshold):
    """Bar fraction and colour for a level against its threshold.

    The threshold sits at 2/3 of the bar, matching the ASCII meter in
    clench_detect so the window and the terminal read the same way.
    """
    if not threshold:
        return min(1.0, level / FIT_BAR_FULL_UV), "#9ca3af"
    fraction = min(1.0, (level / (threshold * 1.5)) if threshold else 0.0)
    return fraction, "#22c55e" if level > threshold else "#3b82f6"


class Bar(tk.Canvas):
    """A horizontal level bar with an optional threshold tick at 2/3."""

    HEIGHT = 14

    def __init__(self, parent, width=200, marked=False):
        super().__init__(parent, width=width, height=self.HEIGHT,
                         highlightthickness=0, bg="#e5e7eb")
        self._width = width
        self._fill = self.create_rectangle(0, 0, 0, self.HEIGHT,
                                           fill="#9ca3af", outline="")
        if marked:
            mark = int(width * 2 / 3)
            self.create_line(mark, 0, mark, self.HEIGHT, fill="#374151")

    def set(self, fraction, color=None):
        self.coords(self._fill, 0, 0, max(0, min(self._width,
                                                 int(self._width * fraction))),
                    self.HEIGHT)
        if color:
            self.itemconfigure(self._fill, fill=color)


def main():
    parser = build_parser("A window that holds the Muse connection open.")
    parser.add_argument("--connect", action="store_true",
                        help="start connecting immediately instead of waiting "
                             "for the Connect button")
    args = parser.parse_args()

    warn_about_platform()
    if not args.debug:
        BoardShim.disable_board_logger()

    station = Station(args)
    if args.connect:
        station._on_connect()
    station.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
