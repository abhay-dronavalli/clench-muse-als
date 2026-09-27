"""Step 7: Flappy Bird, flown with your jaw.

A slow, forgiving Flappy Bird in a matplotlib window. Clench to flap. That is the
whole game, and it is a much better test of the input than the scanning game:
flappy punishes *latency* immediately and visibly, so if this feels responsive,
clench-as-a-button is genuinely working.

    python clench_flappy.py --load         # reuse calibration.json (fastest)
    python clench_flappy.py                # calibrate first, then play
    python clench_flappy.py --bot          # no headband at all: watches itself play
    python clench_flappy.py --keyboard     # no headband: Space to flap

Controls: CLENCH or Space = flap.  R = restart.  Q or closing the window = quit.

The latency thing, which matters
--------------------------------
This flaps on the RISING edge of the clench -- the moment the envelope crosses
your threshold -- not when you let go. Waiting for the release adds the entire
length of your clench to the lag, which in the scanning game showed up as being
exactly one cell late, every single time. Rising edge costs you only the ~200 ms
envelope window, which is why the physics here are deliberately slow: gentle
gravity, wide gaps, unhurried pipes.
"""

import argparse
import json
import random
import sys
import time

import matplotlib

from clench_detect import (
    load_calibration,
    recognizer_from_calibration,
    WINDOW_SECONDS,
    calibrate,
    describe,
    read_levels,
)
from config import (board_label, build_parser, eeg_channels_and_names, get_board,
                    prepare_or_explain)

# --- world ---------------------------------------------------------------
WORLD_W, WORLD_H = 10.0, 10.0
BIRD_X = 2.5
BIRD_R = 0.32
GROUND_Y = 0.6

# --- physics: tuned SLOW on purpose --------------------------------------
GRAVITY = 5.5          # units/s^2 downward
FLAP_VELOCITY = 3.4    # units/s upward, applied instantly on a flap
MAX_FALL = -6.0        # terminal velocity, so a long fall stays recoverable
PIPE_SPEED = 1.5       # units/s leftward
PIPE_WIDTH = 1.1
GAP_HEIGHT = 3.8       # generous: about 12 bird-heights
PIPE_SPACING = 5.0     # units between pipes -> one every ~3.3 s
GRACE_SECONDS = 2.0    # calm air before the first pipe arrives
MAX_PIPES = 4          # artists are pooled, so this caps what can be on screen

SKY = "#8ecae6"
PIPE_GREEN = "#2a9d8f"
BIRD_YELLOW = "#ffd166"
GROUND_BROWN = "#a1683a"


class Flappy:
    """Pure game state. No matplotlib in here, so --bot can run it headless."""

    def __init__(self, rng=None):
        self.rng = rng or random.Random()
        self.reset()

    def reset(self):
        self.y = WORLD_H / 2
        self.velocity = 0.0
        self.pipes = []          # each: {"x", "gap_center", "scored"}
        self.score = 0
        self.best_this_run = 0
        self.alive = True
        self.elapsed = 0.0
        self.next_pipe_x = WORLD_W + PIPE_SPEED * GRACE_SECONDS

    def flap(self):
        if self.alive:
            self.velocity = FLAP_VELOCITY

    def step(self, dt):
        """Advance the world by dt seconds."""
        if not self.alive:
            return
        self.elapsed += dt

        # --- bird ---
        self.velocity = max(MAX_FALL, self.velocity - GRAVITY * dt)
        self.y += self.velocity * dt

        # --- pipes march left; spawn a new one when there is room ---
        for pipe in self.pipes:
            pipe["x"] -= PIPE_SPEED * dt
        self.pipes = [p for p in self.pipes if p["x"] + PIPE_WIDTH > -1.0]

        rightmost = max((p["x"] for p in self.pipes), default=-PIPE_SPACING)
        if rightmost < WORLD_W - PIPE_SPACING and len(self.pipes) < MAX_PIPES:
            if self.elapsed >= GRACE_SECONDS:
                margin = GAP_HEIGHT / 2 + 0.8
                self.pipes.append({
                    "x": WORLD_W + 0.5,
                    "gap_center": self.rng.uniform(GROUND_Y + margin, WORLD_H - margin),
                    "scored": False,
                })

        # --- scoring: a pipe counts once its trailing edge passes the bird ---
        for pipe in self.pipes:
            if not pipe["scored"] and pipe["x"] + PIPE_WIDTH < BIRD_X - BIRD_R:
                pipe["scored"] = True
                self.score += 1

        self.check_collision()

    def check_collision(self):
        if self.y - BIRD_R <= GROUND_Y or self.y + BIRD_R >= WORLD_H:
            self.alive = False
            return
        for pipe in self.pipes:
            overlaps_x = (pipe["x"] < BIRD_X + BIRD_R
                          and pipe["x"] + PIPE_WIDTH > BIRD_X - BIRD_R)
            if not overlaps_x:
                continue
            top_of_gap = pipe["gap_center"] + GAP_HEIGHT / 2
            bottom_of_gap = pipe["gap_center"] - GAP_HEIGHT / 2
            if self.y + BIRD_R > top_of_gap or self.y - BIRD_R < bottom_of_gap:
                self.alive = False
                return

    def bot_wants_flap(self):
        """Autopilot for --bot: aim at the next gap, flap when sagging below it."""
        ahead = [p for p in self.pipes if p["x"] + PIPE_WIDTH > BIRD_X - BIRD_R]
        target = ahead[0]["gap_center"] if ahead else WORLD_H / 2
        return self.y < target - 0.15 and self.velocity < 1.0


class Scene:
    """The matplotlib drawing. Artists are created once and then moved."""

    def __init__(self, plt, threshold):
        self.plt = plt
        self.threshold = threshold
        self.fig, self.ax = plt.subplots(figsize=(8, 8))
        self.fig.canvas.manager.set_window_title("Clench Flappy")
        self.ax.set_xlim(0, WORLD_W)
        self.ax.set_ylim(0, WORLD_H)
        self.ax.set_xticks([])
        self.ax.set_yticks([])
        self.ax.set_facecolor(SKY)
        self.ax.set_aspect("equal")   # otherwise the bird renders as an ellipse

        from matplotlib.patches import Circle, Rectangle
        self.ax.add_patch(Rectangle((0, 0), WORLD_W, GROUND_Y,
                                    color=GROUND_BROWN, zorder=2))

        # Pipe artists are pooled: MAX_PIPES pairs, hidden until needed. Creating
        # and destroying patches every frame is what makes naive animations crawl.
        self.pipe_artists = []
        for _ in range(MAX_PIPES):
            top = Rectangle((0, 0), PIPE_WIDTH, 1, color=PIPE_GREEN, zorder=3)
            bottom = Rectangle((0, 0), PIPE_WIDTH, 1, color=PIPE_GREEN, zorder=3)
            top.set_visible(False)
            bottom.set_visible(False)
            self.ax.add_patch(top)
            self.ax.add_patch(bottom)
            self.pipe_artists.append((top, bottom))

        self.bird = Circle((BIRD_X, WORLD_H / 2), BIRD_R, color=BIRD_YELLOW,
                           ec="#c98a00", lw=2, zorder=5)
        self.ax.add_patch(self.bird)
        # A face costs two artists and makes the thing read as a bird instantly.
        self.eye = Circle((BIRD_X + 0.11, WORLD_H / 2 + 0.10), 0.06,
                          color="#1d3557", zorder=6)
        self.ax.add_patch(self.eye)
        self.beak, = self.ax.plot([], [], color="#e76f51", lw=4,
                                  solid_capstyle="round", zorder=6)

        self.score_text = self.ax.text(WORLD_W / 2, WORLD_H - 0.8, "0",
                                       ha="center", va="top", fontsize=34,
                                       fontweight="bold", color="white", zorder=6)
        self.message = self.ax.text(WORLD_W / 2, WORLD_H / 2, "", ha="center",
                                    va="center", fontsize=17, fontweight="bold",
                                    color="#1d3557", zorder=7,
                                    bbox=dict(boxstyle="round,pad=0.6", fc="white",
                                              ec="#1d3557", alpha=0.92))

        # Clench meter along the bottom, with your threshold marked.
        self.meter_bg = Rectangle((0.3, 0.12), WORLD_W - 0.6, 0.3,
                                  color="white", alpha=0.5, zorder=4)
        self.meter_fill = Rectangle((0.3, 0.12), 0.0, 0.3,
                                    color="#e63946", zorder=5)
        self.ax.add_patch(self.meter_bg)
        self.ax.add_patch(self.meter_fill)
        mark_x = 0.3 + (WORLD_W - 0.6) * (1 / 1.8)   # threshold sits at 1/1.8 of full scale
        self.ax.plot([mark_x, mark_x], [0.10, 0.44], color="#1d3557", lw=2, zorder=6)

    def draw(self, game, emg_level):
        for (top, bottom), pipe in zip(self.pipe_artists,
                                       game.pipes + [None] * MAX_PIPES):
            if pipe is None:
                top.set_visible(False)
                bottom.set_visible(False)
                continue
            gap_top = pipe["gap_center"] + GAP_HEIGHT / 2
            gap_bottom = pipe["gap_center"] - GAP_HEIGHT / 2
            top.set_bounds(pipe["x"], gap_top, PIPE_WIDTH, WORLD_H - gap_top)
            bottom.set_bounds(pipe["x"], GROUND_Y, PIPE_WIDTH, gap_bottom - GROUND_Y)
            top.set_visible(True)
            bottom.set_visible(True)

        self.bird.center = (BIRD_X, game.y)
        self.eye.center = (BIRD_X + 0.11, game.y + 0.10)
        self.beak.set_data([BIRD_X + 0.26, BIRD_X + 0.52], [game.y, game.y - 0.04])
        self.score_text.set_text(str(game.score))

        full = WORLD_W - 0.6
        fraction = min(1.0, emg_level / (self.threshold * 1.8)) if self.threshold else 0.0
        self.meter_fill.set_bounds(0.3, 0.12, full * fraction, 0.3)
        self.meter_fill.set_color("#e63946" if emg_level > self.threshold else "#457b9d")

        if not game.alive:
            self.message.set_text(f"GAME OVER   score {game.score}\n"
                                  "clench (or press R) to play again")
        else:
            self.message.set_text("")


def run(game, scene, read_flap, plt, tick=0.02):
    """Main loop: real-time physics, redraw, poll the input source."""
    flap_requested = {"value": False, "quit": False, "restart": False}

    def on_key(event):
        if event.key == " ":
            flap_requested["value"] = True
        elif event.key in ("q", "escape"):
            flap_requested["quit"] = True
        elif event.key == "r":
            flap_requested["restart"] = True

    scene.fig.canvas.mpl_connect("key_press_event", on_key)
    plt.ion()
    plt.show(block=False)

    last = time.monotonic()
    while plt.fignum_exists(scene.fig.number) and not flap_requested["quit"]:
        now = time.monotonic()
        dt = min(now - last, 0.1)   # clamp: a stalled frame must not teleport the bird
        last = now

        clenched, emg_level = read_flap()
        if flap_requested["value"]:
            clenched = True
            flap_requested["value"] = False

        if game.alive:
            if clenched:
                game.flap()
            game.step(dt)
        elif clenched or flap_requested["restart"]:
            # A flap on the game-over screen restarts. Small pause first so the
            # clench that killed you does not instantly restart the round.
            if game.elapsed > 0.4:
                game.reset()
            flap_requested["restart"] = False

        scene.draw(game, emg_level)
        plt.pause(tick)

    return game.score


def main():
    parser = build_parser("Flappy Bird flown with jaw clenches.")
    parser.add_argument("--load", action="store_true",
                        help="reuse calibration.json instead of recalibrating")
    parser.add_argument("--no-clench-cal", action="store_true",
                        help="skip the active calibration phase")
    parser.add_argument("--baseline-seconds", type=float, default=10.0)
    parser.add_argument("--k", type=float, default=6.0)
    parser.add_argument("--long-ms", type=int, default=1500)
    parser.add_argument("--double-ms", type=int, default=700)
    parser.add_argument("--keyboard", action="store_true",
                        help="no headband: fly with the Space bar")
    parser.add_argument("--bot", action="store_true",
                        help="no headband: autopilot plays it (used for testing)")
    parser.add_argument("--save", default=None, metavar="PNG",
                        help="with --bot: simulate a few seconds, save a frame, exit")
    args = parser.parse_args()

    if args.save:
        matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    # ---------- headless / hardware-free paths ----------
    if args.bot:
        game = Flappy(rng=random.Random(7))   # seeded: same run every time
        scene = Scene(plt, threshold=60.0)
        if args.save:
            # Fixed timestep so the screenshot is deterministic.
            for _ in range(int(9.0 / 0.033)):
                if game.bot_wants_flap():
                    game.flap()
                game.step(0.033)
            scene.draw(game, 82.0)
            scene.fig.savefig(args.save, dpi=110)
            print(f"score {game.score}, alive={game.alive}, saved {args.save}")
            return 0
        print("Bot mode: autopilot flying. Close the window to stop.")
        run(game, scene, lambda: (game.bot_wants_flap(), 40.0), plt)
        return 0

    if args.keyboard:
        game = Flappy()
        scene = Scene(plt, threshold=60.0)
        print("Keyboard mode: Space to flap, R to restart, Q to quit.")
        score = run(game, scene, lambda: (False, 0.0), plt)
        print(f"Final score: {score}")
        return 0

    # ---------- the real thing ----------
    board = get_board(args)
    from brainflow.board_shim import BoardShim, BrainFlowPresets
    fs = BoardShim.get_sampling_rate(board.board_id, BrainFlowPresets.DEFAULT_PRESET)
    channels, _names = eeg_channels_and_names(board.board_id)
    window_samples = int(fs * WINDOW_SECONDS)
    rows = {"emg": [channels[0], channels[3]], "blink": [channels[1], channels[2]]}

    print(f"{board_label(board)} @ {fs} Hz")
    if not prepare_or_explain(board):
        return 1
    try:
        board.start_stream()
        time.sleep(WINDOW_SECONDS + 0.3)

        calibration = load_calibration(board, args.profile) if args.load else None
        if calibration:
            print(f"Loaded calibration from {calibration['saved_at']}.")
        else:
            if args.load:
                print("No usable saved calibration -- calibrating now.")
            calibration = calibrate(board, rows, fs, window_samples, args)
        if calibration is None:
            return 1
        describe(calibration)

        # emit_start=True is the whole point: flap on the rising edge.
        recognizer = recognizer_from_calibration(calibration, args, emit_start=True)

        def read_flap():
            levels = read_levels(board, rows, fs, window_samples)
            if levels is None:
                return False, 0.0
            events = recognizer.update(levels, time.monotonic())
            flapped = any(name == "CLENCH_START" for name, _ in events)
            return flapped, levels.emg

        game = Flappy()
        scene = Scene(plt, calibration["emg_threshold"])
        print("\nClench to flap. R restarts, Q quits, or just close the window.\n")
        score = run(game, scene, read_flap, plt)
        print(f"Final score: {score}")
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        for cleanup in (board.stop_stream, board.release_session):
            try:
                cleanup()
            except Exception:
                pass
        print("Session released.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
