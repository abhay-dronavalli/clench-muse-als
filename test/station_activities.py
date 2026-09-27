"""The two windows the station can open on top of a live connection.

Both exist to answer questions a log cannot:

  FlappyWindow  does the input feel immediate? Flappy punishes latency visibly,
                so if this feels fair, clench-as-a-button works.
  DrillWindow   are the two inputs reliably told apart? Prompts one at random,
                times the response, and counts every way it can go wrong.

Both are Toplevels driven by `after()` on the Tk thread. They never read the
board: the station's work thread already does that and posts gestures onto the
queue, so by the time anything arrives here it is a plain event name on the right
thread. That is the only reason these can be this simple.

Physics for the game comes from clench_flappy.Flappy unchanged -- it is pure
state with no matplotlib in it, which is exactly why it was written that way.
"""

import random
import json
from pathlib import Path
import time
import tkinter as tk
from tkinter import ttk

from clench_flappy import (BIRD_R, BIRD_X, GAP_HEIGHT, GROUND_Y, PIPE_WIDTH,
                           WORLD_H, WORLD_W, Flappy)

FRAME_MS = 20               # 50 fps; the physics are dt-based so this is cosmetic
CLENCH = "CLENCH_START"     # the rising edge, not the release: see clench_flappy
LONG_BLINK = "LONG_BLINK"
BLINK = 'BLINK'

INK = "#0f172a"
PAPER = "#f8fafc"
BIRD = "#f59e0b"
PIPE = "#16a34a"
DEAD = "#dc2626"
HINT = "#64748b"
LIVE = "#2563eb"
READY = "#b45309"


class ActivityWindow(tk.Toplevel):
    """A Toplevel with a canvas and a frame clock. Subclasses fill in the rest."""

    def __init__(self, parent, title, width, height, on_close):
        super().__init__(parent)
        self.title(title)
        self.resizable(False, False)
        self._on_close = on_close
        self.alive = True
        self.width, self.height = width, height

        self.canvas = tk.Canvas(self, width=width, height=height, bg=PAPER,
                                highlightthickness=0)
        self.canvas.pack()
        self.status = ttk.Label(self, text="", foreground=HINT)
        self.status.pack(fill="x", padx=8, pady=4)

        self.protocol("WM_DELETE_WINDOW", self.close)
        self.bind("<KeyPress>", self.on_key)
        self.focus_set()

        self._last = time.monotonic()
        self.after(FRAME_MS, self._frame)

    # -------------------------------------------------------------- the clock

    def _frame(self):
        if not self.alive:
            return
        now = time.monotonic()
        # Clamped: a stalled frame must not teleport anything.
        dt = min(now - self._last, 0.1)
        self._last = now
        try:
            self.update_frame(dt)
        except tk.TclError:
            return          # window went away mid-frame
        self.after(FRAME_MS, self._frame)

    def close(self):
        if not self.alive:
            return
        self.alive = False
        self._on_close()
        self.destroy()

    # ------------------------------------------------- for subclasses to fill

    def update_frame(self, dt):
        raise NotImplementedError

    def on_gesture(self, name, detail):
        """A gesture fired. Called on the Tk thread."""

    def on_levels(self, levels, emg_threshold, hold_threshold):
        """One detector tick, for live meters."""

    def instruct(self, headline, detail=""):
        """The one thing the person should be doing right now."""

    def set_prompt(self, text):
        """Waiting on the person: text to show, or None once they have gone."""

    def set_countdown(self, remaining):
        """Seconds left in a timed phase, or None when nothing is being timed."""

    def on_key(self, event):
        if event.keysym in ("q", "Escape"):
            self.close()


# ================================================ calibration, big enough to read

class CalibrationWindow(ActivityWindow):
    """Calibration instructions at a size you can read without your glasses.

    You take your glasses off to put the headband on, which made the ordinary
    calibration log unreadable exactly when you needed it. So this shows one
    instruction and one number, both enormous, and nothing else. Enter is the only
    control -- hunting for a small Ready button has the same problem as reading
    small text.

    Everything it displays arrives as messages from the calibration running on the
    work thread; it holds no state of its own beyond what is currently on screen.
    """

    def __init__(self, parent, on_close, on_ready):
        screen_w = parent.winfo_screenwidth()
        screen_h = parent.winfo_screenheight()
        super().__init__(parent, "Calibration", screen_w, screen_h, on_close)
        self._on_ready = on_ready

        # Zoomed rather than true fullscreen: it fills the screen but keeps the
        # title bar, so there is always an obvious way out.
        try:
            self.state("zoomed")
        except tk.TclError:
            pass

        # Sized off the screen so it stays readable on any monitor.
        self.headline_font = ("Segoe UI", max(28, int(screen_h * 0.070)), "bold")
        self.detail_font = ("Segoe UI", max(14, int(screen_h * 0.026)))
        self.timer_font = ("Consolas", max(32, int(screen_h * 0.110)), "bold")
        self.wrap = int(screen_w * 0.88)

        self.headline = "GET READY"
        self.detail = ""
        self.countdown = None
        self.awaiting_enter = False

        for sequence in ("<Return>", "<KP_Enter>"):
            self.bind(sequence, self._on_enter)
        # Enter is the only control, so the focus has to be here and stay here.
        self.focus_force()
        self.status.configure(text="Press Enter when the screen says to.  "
                                  "Esc cancels.")

    # -------------------------------------------------------------- the input

    def _on_enter(self, _event=None):
        if self.awaiting_enter and self._on_ready:
            self._on_ready()

    def on_key(self, event):
        # Enter is handled by its own binding; only let Escape through, so a
        # stray keypress during calibration cannot close the window.
        if event.keysym == "Escape":
            self.close()

    # ------------------------------------------------------- what to display

    def instruct(self, headline, detail=""):
        self.headline = headline
        self.detail = detail

    def set_prompt(self, text):
        waiting = text is not None
        if waiting and not self.awaiting_enter:
            try:
                self.focus_force()
            except tk.TclError:
                pass        # withdrawn (the self-test does this) or already gone
        self.awaiting_enter = waiting

    def set_countdown(self, remaining):
        self.countdown = remaining

    # ------------------------------------------------------------- the drawing

    def update_frame(self, dt):
        self.draw()

    def draw(self):
        canvas = self.canvas
        canvas.delete("all")
        width = canvas.winfo_width() or self.width
        height = canvas.winfo_height() or self.height

        canvas.create_text(width / 2, height * 0.34, text=self.headline,
                           font=self.headline_font, fill=INK,
                           width=self.wrap, justify="center")
        if self.detail:
            canvas.create_text(width / 2, height * 0.50, text=self.detail,
                               font=self.detail_font, fill=HINT,
                               width=self.wrap, justify="center")

        # The timer and the Enter prompt occupy the same spot: exactly one of them
        # is true at a time, and two big things competing would defeat the point.
        if self.countdown is not None:
            canvas.create_text(width / 2, height * 0.72,
                               text=f"{max(0.0, self.countdown):.1f}",
                               font=self.timer_font, fill=LIVE)
        elif self.awaiting_enter:
            canvas.create_text(width / 2, height * 0.72, text="PRESS ENTER",
                               font=self.headline_font, fill=READY)


# ============================================================ flappy, for feel

class FlappyWindow(ActivityWindow):
    """Flappy Bird, flapped on the rising edge of a clench.

    The point is latency, not fun. Waiting for the clench to release would add the
    whole length of the clench to the lag, which in the scanning game read as
    being exactly one cell late every time -- so this flaps on CLENCH_START.
    """

    SCALE = 58

    def __init__(self, parent, on_close):
        width = int(WORLD_W * self.SCALE)
        height = int(WORLD_H * self.SCALE)
        super().__init__(parent, "Clench Flappy", width, height, on_close)
        self.game = Flappy()
        self.emg = 0.0
        self.emg_threshold = None
        self.status.configure(text="Clench to flap.  Space also flaps, R restarts, "
                                  "Q closes.")

    # -------------------------------------------------------------- the input

    def on_gesture(self, name, detail):
        if name == CLENCH:
            self.flap()

    def on_levels(self, levels, emg_threshold, hold_threshold):
        self.emg = levels.emg
        self.emg_threshold = emg_threshold

    def on_key(self, event):
        if event.keysym == "space":
            self.flap()
        elif event.keysym in ("r", "R"):
            self.game.reset()
        else:
            super().on_key(event)

    def flap(self):
        if self.game.alive:
            self.game.flap()
        elif self.game.elapsed > 0.4:
            # A flap on the game-over screen restarts, but not the same clench
            # that just killed you.
            self.game.reset()

    # ------------------------------------------------------------- the drawing

    def update_frame(self, dt):
        if self.game.alive:
            self.game.step(dt)
        self.draw()

    def _xy(self, x, y):
        """World coordinates to canvas pixels, with y flipped."""
        return x * self.SCALE, self.height - y * self.SCALE

    def draw(self):
        canvas = self.canvas
        canvas.delete("all")
        game = self.game

        # ground
        gx, gy = self._xy(0, GROUND_Y)
        canvas.create_rectangle(0, gy, self.width, self.height,
                                fill="#e2e8f0", outline="")

        for pipe in game.pipes:
            left, _ = self._xy(pipe["x"], 0)
            right, _ = self._xy(pipe["x"] + PIPE_WIDTH, 0)
            gap_top = pipe["gap_center"] + GAP_HEIGHT / 2
            gap_bottom = pipe["gap_center"] - GAP_HEIGHT / 2
            _, top_y = self._xy(0, gap_top)
            _, bottom_y = self._xy(0, gap_bottom)
            canvas.create_rectangle(left, 0, right, top_y, fill=PIPE, outline="")
            canvas.create_rectangle(left, bottom_y, right, gy, fill=PIPE, outline="")

        bx, by = self._xy(BIRD_X, game.y)
        radius = BIRD_R * self.SCALE
        canvas.create_oval(bx - radius, by - radius, bx + radius, by + radius,
                           fill=BIRD if game.alive else DEAD, outline="")

        canvas.create_text(12, 12, anchor="nw", text=str(game.score),
                           font=("Segoe UI", 22, "bold"), fill=INK)

        # A clench meter in the corner: when a flap does not happen, this says
        # whether the clench was too weak or the detector missed it.
        if self.emg_threshold:
            bar_w = 120
            fraction = min(1.0, self.emg / (self.emg_threshold * 1.5))
            canvas.create_rectangle(self.width - bar_w - 12, 14,
                                    self.width - 12, 26,
                                    fill="#e2e8f0", outline="")
            canvas.create_rectangle(self.width - bar_w - 12, 14,
                                    self.width - bar_w - 12 + bar_w * fraction, 26,
                                    fill=LIVE if self.emg < self.emg_threshold
                                    else "#22c55e", outline="")
            mark = self.width - bar_w - 12 + bar_w * (2 / 3)
            canvas.create_line(mark, 12, mark, 28, fill=INK)

        if not game.alive:
            canvas.create_text(self.width / 2, self.height / 2 - 20,
                               text="dead", font=("Segoe UI", 30, "bold"),
                               fill=DEAD)
            canvas.create_text(self.width / 2, self.height / 2 + 20,
                               text="clench (or Space) to try again",
                               font=("Segoe UI", 13), fill=HINT)


# ====================================================== the drill, for the truth

class Round:
    """One prompt and what came of it."""

    def __init__(self, want, now=None):
        self.want = want
        self.shown_at = time.monotonic() if now is None else now
        self.ended_at = None
        self.outcome = None            # "hit" | "wrong" | "miss"
        self.latency_ms = None
        self.got = None


class DrillState:
    """Prompts one of the two inputs at random and records what actually fired.

    Reliability and latency are different questions and this answers both, but
    only one of them cleanly. Reliability is exact: over N rounds, how often did
    the right input fire, how often did the other one fire instead, how often did
    nothing happen. Latency is not -- the number below is your reaction time plus
    the system's, and nothing here can separate those. What IS ours is the
    difference between the two inputs: a clench fires on its rising edge, while a
    long blink cannot fire until the hold completes, so expect a gap of roughly
    the hold length. If the clench column is much slower than your own reaction
    time, that is real lag and worth chasing.
    """

    ROUNDS = 20
    TIMEOUT_S = 4.0
    GAP_S = 1.2                 # quiet time between rounds, to catch stray fires

    PROMPT = {CLENCH: "CLENCH", LONG_BLINK: "EYES SHUT", BLINK: 'BLINK ONCE'}
    HOW = {CLENCH: "one short jaw clench",
           LONG_BLINK: "close your eyes and keep them shut", BLINK: 'one blink, then open your eyes'}

    def __init__(self, rounds=ROUNDS, inputs=(CLENCH, LONG_BLINK), seed=None,
                 clock=time.monotonic, require_neutral=False):
        self.clock = clock
        self.started_at = clock()
        self.require_neutral = require_neutral
        self.neutral_since = None
        self.last_state_at = None
        self.feedback = "Open eyes and relax jaw. Wait for the prompt."
        self.timeline = []
        self.total_rounds = rounds
        self.inputs = list(inputs)
        self.rng = random.Random(seed)
        self.rounds = []
        self.current = None
        self.waiting_until = clock() + 1.5   # a beat before the first prompt
        self.other_events = {}
        self.stray = 0
        self.flash_until = 0.0
        self.finished = False

    # -------------------------------------------------------------- the input

    def on_detector_state(self, ready, occurred_at=None):
        now = self.clock() if occurred_at is None else occurred_at
        self.last_state_at = now
        if not ready:
            self.neutral_since = None
        elif self.neutral_since is None:
            self.neutral_since = now

    def on_gesture(self, name, detail, occurred_at=None):
        now = self.clock() if occurred_at is None else occurred_at
        record = dict(t=now-self.started_at, event=name, detail=detail)
        self.timeline.append(record)
        if name not in self.inputs:
            # BLINK and DOUBLE_BLINK happen involuntarily; they are worth counting
            # but they are not failures of this drill.
            self.other_events[name] = self.other_events.get(name, 0) + 1
            record["disposition"] = "not a drilled input"
            return

        self.flash_until = self.clock() + 0.12
        # Score by detection time, not queue-delivery time. A UI stall may deliver
        # an on-time event after update_frame has marked that round as missed.
        target = next((r for r in self.rounds
                       if r.shown_at <= now <= r.ended_at), None)
        if target is None and self.current is not None:
            if self.current.shown_at <= now <= self.current.shown_at + self.TIMEOUT_S:
                target = self.current
            elif now > self.current.shown_at + self.TIMEOUT_S:
                self.current.outcome = "miss"
                self._finish_round()
        if target is None or target.outcome in ("hit", "wrong"):
            # Fired while nothing was asked for. This is the number that decides
            # whether an input is safe to leave switched on.
            self.stray += 1
            record["disposition"] = "outside active prompt or duplicate"
            self.feedback = f"{name} detected outside its prompt; not a hit. Open eyes and relax."
            return

        target.got = name
        target.latency_ms = (now - target.shown_at) * 1000
        target.outcome = "hit" if name == target.want else "wrong"
        record["disposition"] = target.outcome
        record["wanted"] = target.want
        self.feedback = (f"{target.outcome.upper()}: detected {name}, wanted {target.want}. "
                         "Open eyes and relax jaw.")
        if target is self.current:
            self._finish_round(ended_at=now)

    # ---------------------------------------------------------------- the state

    def update_frame(self, dt):
        now = self.clock()
        if self.finished:
            self.draw()
            return

        if self.current is None:
            neutral = (not self.require_neutral or
                       (self.neutral_since is not None and self.last_state_at is not None
                        and now - self.neutral_since >= .6 and now - self.last_state_at <= .3))
            if now >= self.waiting_until and neutral:
                self._next_round()
        elif now - self.current.shown_at > self.TIMEOUT_S:
            self.current.outcome = "miss"
            self.feedback = f"MISSED {self.current.want}: no matching input within {self.TIMEOUT_S:g} s."
            self._finish_round()
        self.draw()

    def _next_round(self):
        if len(self.rounds) >= self.total_rounds:
            self.finished = True
            return
        self.current = Round(self.rng.choice(self.inputs), self.clock())

    def _finish_round(self, ended_at=None):
        self.current.ended_at = (min(self.clock(), self.current.shown_at + self.TIMEOUT_S)
                                 if ended_at is None else ended_at)
        self.rounds.append(self.current)
        self.current = None
        self.neutral_since = None
        if len(self.rounds) >= self.total_rounds:
            # Finish the moment the last round lands. Waiting out the
            # between-rounds gap first left the summary a second late, staring at
            # "wait" after the drill was already over.
            self.finished = True
        else:
            self.waiting_until = self.clock() + self.GAP_S

    def report(self):
        return dict(finished=self.finished, tally=self.tally(), stray=self.stray,
            other_events=self.other_events, timeline=self.timeline,
            rounds=[dict(wanted=r.want, shown_at=r.shown_at-self.started_at,
                         ended_at=r.ended_at-self.started_at, outcome=r.outcome,
                         got=r.got, latency_ms=r.latency_ms) for r in self.rounds])

    def draw(self):
        """Headless model; the window supplies rendering."""

    # ------------------------------------------------------------ the reporting

    def tally(self):
        """Per-input counts and latencies. Also used by the self-test."""
        result = {}
        for name in self.inputs:
            mine = [r for r in self.rounds if r.want == name]
            hits = [r for r in mine if r.outcome == "hit"]
            latencies = sorted(r.latency_ms for r in hits)
            result[name] = {
                "asked": len(mine),
                "hit": len(hits),
                "wrong": sum(1 for r in mine if r.outcome == "wrong"),
                "miss": sum(1 for r in mine if r.outcome == "miss"),
                "median_ms": latencies[len(latencies) // 2] if latencies else None,
                "worst_ms": latencies[-1] if latencies else None,
            }
        return result

    def summary_lines(self):
        lines = []
        for name, row in self.tally().items():
            median = f"{row['median_ms']:.0f} ms" if row["median_ms"] else "--"
            worst = f"{row['worst_ms']:.0f} ms" if row["worst_ms"] else "--"
            lines.append(f"{self.PROMPT[name]:<16} {row['hit']}/{row['asked']} hit"
                         f"   {row['wrong']} wrong input   {row['miss']} missed"
                         f"   median {median}, worst {worst}")
        lines.append(f"fired when nothing was asked: {self.stray}")
        if self.other_events:
            spare = ", ".join(f"{k} x{v}" for k, v in sorted(self.other_events.items()))
            lines.append(f"other events seen: {spare}")
        return lines

class DrillWindow(DrillState, ActivityWindow):
    """Display the independently testable scoring model."""

    def __init__(self, parent, on_close, rounds=DrillState.ROUNDS,
                 inputs=(CLENCH, LONG_BLINK), seed=None, require_neutral=False,
                 report_path=None, profile=None):
        ActivityWindow.__init__(self, parent, "Two-input drill", 760, 380, on_close)
        DrillState.__init__(self, rounds, inputs, seed, require_neutral=require_neutral)
        self.report_path = Path(report_path) if report_path else None
        self.profile = profile
        self._saved_version = None

    def update_frame(self, dt):
        DrillState.update_frame(self, dt)
        self.status.configure(text=self.feedback)
        if self.finished:
            self.save_report()

    def save_report(self):
        version = (len(self.rounds), len(self.timeline), self.finished)
        if self.report_path is None or version == self._saved_version:
            return
        report = self.report()
        report["profile"] = self.profile
        try:
            self.report_path.parent.mkdir(exist_ok=True, parents=True)
            self.report_path.write_text(json.dumps(report, indent=2) + "\n")
            self._saved_version = version
        except OSError as exc:
            self.feedback = f"Could not save drill report: {exc}"

    def close(self):
        self.save_report()
        ActivityWindow.close(self)

    # ------------------------------------------------------------- the drawing

    def draw(self):
        canvas = self.canvas
        canvas.delete("all")
        now = time.monotonic()

        if now < self.flash_until:
            canvas.create_rectangle(0, 0, self.width, self.height,
                                    fill="#dbeafe", outline="")

        if self.finished:
            canvas.create_text(self.width / 2, 40, text="drill complete",
                               font=("Segoe UI", 20, "bold"), fill=INK)
            y = 95
            for line in self.summary_lines():
                canvas.create_text(30, y, anchor="w", text=line,
                                   font=("Consolas", 10), fill=INK)
                y += 24
            canvas.create_text(self.width / 2, self.height - 30,
                               text="the latency above is your reaction plus the "
                                    "system's; the gap between the two is ours",
                               font=("Segoe UI", 9), fill=HINT)
            return

        done = len(self.rounds)
        canvas.create_text(self.width - 20, 18, anchor="ne",
                           text=f"round {min(done + 1, self.total_rounds)} "
                                f"/ {self.total_rounds}",
                           font=("Segoe UI", 11), fill=HINT)

        if self.current is None:
            canvas.create_text(self.width / 2, self.height / 2,
                               text="OPEN EYES - RELAX JAW" if self.require_neutral else "wait",
                               font=("Segoe UI", 28, "bold"), fill=HINT)
        else:
            elapsed = now - self.current.shown_at
            canvas.create_text(self.width / 2, self.height / 2 - 40,
                               text=self.PROMPT[self.current.want],
                               font=("Segoe UI", 34, "bold"), fill=INK)
            canvas.create_text(self.width / 2, self.height / 2 - 4,
                               text=self.HOW[self.current.want],
                               font=("Segoe UI", 12), fill=HINT)
            # A draining bar, because a number you read after the fact does not
            # let you feel lag but a bar you are racing does.
            full = self.width - 120
            left = 60
            remaining = max(0.0, 1.0 - elapsed / self.TIMEOUT_S)
            bar_y = self.height / 2 + 40
            canvas.create_rectangle(left, bar_y, left + full, bar_y + 16,
                                    fill="#e2e8f0", outline="")
            canvas.create_rectangle(left, bar_y, left + full * remaining,
                                    bar_y + 16, fill=LIVE, outline="")
            canvas.create_text(self.width / 2, bar_y + 44,
                               text=f"{elapsed * 1000:.0f} ms",
                               font=("Consolas", 12), fill=HINT)

        # A running scoreboard, so a drill going badly is obvious before the end.
        tally = self.tally()
        y = self.height - 26
        for name in self.inputs:
            row = tally[name]
            canvas.create_text(20, y, anchor="w",
                               text=f"{self.PROMPT[name]}  {row['hit']}/{row['asked']}",
                               font=("Consolas", 9), fill=HINT)
            y -= 16
