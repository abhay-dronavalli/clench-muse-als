"""Drive muse_station.py end to end on the synthetic board, with no human and no headband.

The station is the piece most likely to break quietly: it has three threads, and
the interesting behaviour is all about *time* -- a link that drops, an activity
that has to unwind, a reconnect that must happen on its own. None of that shows
up in a unit test of the detection maths, and none of it is obvious from looking
at the window either, because a wedged station looks exactly like an idle one.

So this opens a real Station on the synthetic board, hides the window, and clicks
its buttons from the outside:

    python test_station.py

It takes about a minute and a half, mostly spent waiting for things that are
supposed to take time. Everything it touches is synthetic, so the only file it
writes is calibration.station-selftest.synthetic.json, which it deletes on the
way out.
"""

import json
import pathlib
import sys
import time
import types
from tkinter import simpledialog

import muse_station as ms
import station_activities as sa
from config import build_parser

PROFILE = "station-selftest"

failures = []
performed = 0


def check(label, ok, extra=""):
    global performed
    performed += 1
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{('  -- ' + str(extra)) if extra else ''}")
    if not ok:
        failures.append(label)


class Driver:
    """A Station with its event loop pumped by hand instead of by mainloop()."""

    def __init__(self):
        args = build_parser("station self-test").parse_args(["--synthetic"])
        self.station = ms.Station(args)
        # Withdrawn, not destroyed: every widget still works, nothing appears
        # on screen, and no window steals focus mid-test.
        self.station.withdraw()

    def pump(self, seconds, until=None, click_ready=False):
        """Run the UI for a while. Returns early and True once `until` holds."""
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.station.update()
            if click_ready:
                window = self.station.activity_window
                if window is not None and getattr(window, "awaiting_enter", False):
                    # What a person does: press Enter on the calibration screen.
                    window._on_enter(None)
                elif self.station.prompt_label.cget("text"):
                    # A withdrawn window reports nothing as mapped, so the prompt
                    # is detected by its text rather than by its visibility.
                    self.station._on_ready()
            if until and until():
                return True
            time.sleep(0.02)
        return bool(until()) if until else True

    def connected(self):
        return self.station.link.state == ms.CONNECTED

    def idle(self):
        return self.station.activity_name is None

    def button(self, name):
        return str(getattr(self.station, name).cget("state"))


def main():
    print("Muse Station drive test (synthetic board, no hardware):")
    driver = Driver()
    station = driver.station

    # ------------------------------------------------- connecting on request
    station._on_connect()
    if not driver.pump(30, until=driver.connected):
        check("Connect reaches CONNECTED", False, station.link.state)
        print(station.log_text.get("1.0", "end"))
        return 1
    check("Connect reaches CONNECTED", True)
    driver.pump(1)
    check("board and rate are shown", "@" in station.meta_label.cget("text"),
          station.meta_label.cget("text"))

    # Fit bars must move while merely connected, with no activity running: that
    # is what makes the window useful for seating the band.
    driver.pump(3)
    check('MNE selected by default', station.blink_detector_var.get() == 'mne')
    verdicts = [station.fit_rows[i][3].cget("text") for i in range(4)]
    check("electrode fit updates while idle", all(verdicts), verdicts)
    check("Calibrate offered once connected", driver.button("calibrate_button") == "normal")

    # ------------------------------------------------------------ calibrating
    station.quick_var.set(True)           # rest only: no clench or blink reps to fake
    station.profile_var.set(PROFILE)
    full_args = station._args_for_run
    station._args_for_run = lambda: types.SimpleNamespace(
        **{**vars(full_args()), "baseline_seconds": 2.0})

    station._on_calibrate()
    big = station.activity_window
    check("calibrate opens the big instruction window",
          isinstance(big, sa.CalibrationWindow), type(big).__name__)
    if big:
        big.withdraw()          # it is a zoomed, screen-filling window otherwise
        # Enter has to be the control: hunting for a small Ready button has the
        # same problem as reading small text.
        check("Enter is bound on the calibration window",
              bool(big.bind("<Return>")))
        check("the instruction is set before anything is asked",
              bool(big.headline), big.headline)
        check("the instruction is sized off the screen, not a fixed 12pt",
              big.headline_font[1] >= 28, big.headline_font)
        check("a stray keypress cannot close the calibration window",
              (big.on_key(types.SimpleNamespace(keysym="x")) is None) and big.alive)

        saw = {"countdown": False, "rest_headline": False}
        def watch():
            if big.countdown is not None:
                saw["countdown"] = True
            if "STILL" in big.headline.upper():
                saw["rest_headline"] = True
            return driver.idle()

        check("calibrate finishes", driver.pump(40, until=watch, click_ready=True),
              station.activity_name)
        check("the big screen showed the rest instruction", saw["rest_headline"],
              big.headline)
        check("the big screen showed a countdown", saw["countdown"])
        check("the calibration window closes when calibration ends",
              station.activity_window is None and not big.alive)
    else:
        check("calibrate finishes",
              driver.pump(40, until=driver.idle, click_ready=True),
              station.activity_name)
    log = station.log_text.get("1.0", "end")
    check("calibrate reports thresholds", "--- THRESHOLDS ---" in log)
    check("calibrate saves the profile", f"Saved to calibration.{PROFILE}" in log)
    check("the profile list picks it up", PROFILE in station.profile_box.cget("values"),
          station.profile_box.cget("values"))

    # ---------------------------------------------------------------- listening
    station._on_listen()
    check("listen starts", driver.pump(10, until=lambda:
                                      "--- LISTENING ---" in station.log_text.get("1.0", "end")))
    ready = driver.pump(25, until=lambda: 'MNE blinks ready' in station.log_text.get('1.0', 'end'))
    check('MNE worker reaches ready', ready,
          station.log_text.get('1.0', 'end') if not ready else '')
    check("live clench level is updating", "uV" in station.emg_value.cget("text"),
          station.emg_value.cget("text"))
    check("Stop offered while busy", driver.button("stop_button") == "normal")
    check("Calibrate refused while busy", driver.button("calibrate_button") == "disabled")

    station._on_stop()
    check("Stop ends the activity", driver.pump(10, until=driver.idle))
    check("Calibrate offered again after Stop",
          driver.button("calibrate_button") == "normal")

    # The whole point: two activities, still one connection.
    check("still connected after two activities", driver.connected(), station.link.state)

    # ------------------------------------------------- disconnecting on request
    station._on_disconnect()
    check("Disconnect releases the session",
          driver.pump(15, until=lambda: station.link.state == ms.OFF), station.link.state)
    check("Connect offered again", driver.button("connect_button") == "normal")

    station._on_connect()
    check("connects again after a manual disconnect",
          driver.pump(30, until=driver.connected), station.link.state)

    # ------------------------------------------- a drop nobody asked for
    # A real BLE drop does not raise: the ring buffer keeps handing back the same
    # stale samples, which is why the station judges liveness by timestamp. Faking
    # that is therefore the honest way to simulate losing the band.
    station._on_listen()
    driver.pump(3)
    fresh = ms.Link._is_fresh
    ms.Link._is_fresh = lambda self, data: False
    check("a stalled stream is noticed",
          driver.pump(25, until=lambda: station.link.state == ms.LOST), station.link.state)
    check("the drop stops the running activity", station.stop_event.is_set())
    check("the drop does NOT cancel what the user asked for", station.link.wanted is True)

    ms.Link._is_fresh = fresh
    check("reconnects on its own after a drop",
          driver.pump(45, until=driver.connected), station.link.state)
    driver.pump(2)
    check("the interrupted activity unwound rather than wedging", driver.idle(),
          station.activity_name)
    check("Listen usable again after a drop", driver.button("listen_button") == "normal")

    # ================================================= new calibration profiles
    # The dialog is the one thing a test cannot click, so it is replaced by a
    # function that returns what a person would have typed.
    typed = {"value": "second-self"}
    simpledialog.askstring = lambda *a, **k: typed["value"]

    station._on_new_profile()
    check("a new profile is selected once named",
          station.profile_var.get() == "second-self", station.profile_var.get())
    check("the new profile appears in the list",
          "second-self" in station.profile_box.cget("values"),
          station.profile_box.cget("values"))
    check("the new profile has no file yet (it is empty until calibrated)",
          "second-self" not in cd_profiles(), cd_profiles())

    # Names become filenames, so a name that could escape the directory or add a
    # second extension has to be refused.
    for bad in ("../escape", "has space", "dot.ted", ""):
        typed["value"] = bad
        before = station.profile_var.get()
        station._on_new_profile()
        check(f"profile name {bad!r} is refused",
              station.profile_var.get() == before, station.profile_var.get())

    typed["value"] = None            # the user pressed Cancel
    before = station.profile_var.get()
    station._on_new_profile()
    check("cancelling the dialog changes nothing",
          station.profile_var.get() == before)

    station.profile_var.set(PROFILE)  # back to the calibrated one

    # ============================================================ flappy window
    station._on_flappy()
    driver.pump(1)
    flappy = station.activity_window
    check("Flappy opens a window", isinstance(flappy, sa.FlappyWindow))
    if flappy:
        flappy.withdraw()            # keep the screen clean during the test
        check("flappy waits for the first pipe", flappy.game.score == 0)
        # A gesture goes in the way a real one does: through the queue, as the
        # work thread would post it.
        station.queue.put(("event", sa.CLENCH, "peak 90 uV", 1.0))
        driver.pump(0.3)
        check("a clench flaps the bird", flappy.game.velocity > 0,
              f"velocity {flappy.game.velocity:.2f}")
        y_after_flap = flappy.game.y
        driver.pump(1.0)
        check("physics advance between frames", flappy.game.y != y_after_flap)

        # Closing the game window must end the activity, not leave it streaming.
        flappy.close()
        check("closing flappy stops the activity", driver.pump(6, until=driver.idle),
              station.activity_name)
        check("the station forgot the closed window", station.activity_window is None)

    # ================================================================= the drill
    # MNE supplies ordinary blinks even when the profile has no eye calibration.
    station._on_drill()
    driver.pump(1)
    drill = station.activity_window
    check("Drill opens a window", isinstance(drill, sa.DrillWindow))
    if drill:
        drill.withdraw()
        check("MNE drill tests clench and blink without hold calibration",
              drill.inputs == [sa.CLENCH, sa.BLINK], drill.inputs)
        drill.close()
        driver.pump(6, until=driver.idle)

    # Give the profile a hold threshold, as a real long-blink calibration would,
    # and the drill should now offer both inputs.
    add_hold_threshold(station)
    station.blink_detector_var.set('calibrated')
    station._on_drill()
    driver.pump(1)
    drill = station.activity_window
    if drill:
        drill.withdraw()
        check("with hold calibration the drill tests both inputs",
              drill.inputs == [sa.CLENCH, sa.LONG_BLINK], drill.inputs)
        drill.close()
        driver.pump(6, until=driver.idle)

    # --- the drill's own bookkeeping, driven deterministically ---------------
    # Built directly rather than through the button: the outcomes under test are
    # hit / wrong / miss / stray, and provoking all four through a live detector
    # would mean faking four EEG traces.
    bench = sa.DrillWindow(station, lambda: None, rounds=3,
                           inputs=(sa.CLENCH, sa.LONG_BLINK), seed=4)
    bench.withdraw()

    def wait_for_prompt():
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline:
            station.update()
            bench.update()
            if bench.current is not None:
                return True
            time.sleep(0.02)
        return False

    check("the drill prompts for something", wait_for_prompt())
    asked = bench.current.want
    bench.on_gesture(asked, "")                      # round 1: do as asked
    check("doing as asked scores a hit",
          bench.rounds[-1].outcome == "hit", bench.rounds[-1].outcome)
    check("a hit records a latency", bench.rounds[-1].latency_ms is not None)

    # Between rounds nothing is being asked for, so anything that fires now is the
    # number that decides whether an input is safe to leave switched on.
    bench.on_gesture(sa.CLENCH, "")
    check("firing between rounds counts as a stray", bench.stray == 1, bench.stray)

    # A blink nobody asked for is not a drill failure -- people blink. It is only a
    # problem if it becomes a LONG_BLINK, so it is counted separately.
    check("involuntary blinks are not one of the drilled inputs",
          "BLINK" not in bench.inputs)
    bench.on_gesture("BLINK", "")
    check("a stray BLINK is counted apart from drill failures",
          bench.other_events.get("BLINK") == 1 and bench.stray == 1,
          f"other={bench.other_events} stray={bench.stray}")

    check("the drill prompts again", wait_for_prompt())
    other = [i for i in bench.inputs if i != bench.current.want][0]
    bench.on_gesture(other, "")                      # round 2: the wrong input
    check("the wrong input is recorded as wrong, not a miss",
          bench.rounds[-1].outcome == "wrong", bench.rounds[-1].outcome)

    check("the drill prompts a third time", wait_for_prompt())
    deadline = time.monotonic() + bench.TIMEOUT_S + 3
    while time.monotonic() < deadline and len(bench.rounds) < 3:
        station.update()
        bench.update()
        time.sleep(0.02)
    check("doing nothing times out as a miss",
          bench.rounds[-1].outcome == "miss", bench.rounds[-1].outcome)
    check("the drill finishes after its last round", bench.finished)

    tally = bench.tally()
    check("every round is accounted for in the tally",
          sum(row["asked"] for row in tally.values()) == 3, tally)
    check("the summary renders", bool(bench.summary_lines()))
    bench.close()

    station._on_close()
    return 0


def cd_profiles():
    import clench_detect as cd
    return cd.list_profiles()


def add_hold_threshold(station):
    """Bolt a plausible hold threshold onto the saved profile.

    A real long-blink calibration writes these; the quick calibration this test
    runs does not, and sitting through the full one would add a minute of holding
    your eyes shut to an automated test.
    """
    import clench_detect as cd
    path = cd.calibration_file(station.link.board, PROFILE)
    data = json.loads(path.read_text())
    data.update({"hold_rest": 1.0, "hold_sigma": 0.5, "hold_floor": 3.5,
                 "hold_method": "raw_deflection_v2",
                 "hold_peak": 80.0, "hold_threshold": 41.0,
                 "hold_trials": [80.0, 85.0, 82.0],
                 "hold_durations_ms": [900.0, 950.0, 880.0],
                 "long_blink_ms": cd.LONG_BLINK_MS})
    path.write_text(json.dumps(data, indent=2))


def cleanup():
    for path in pathlib.Path(__file__).parent.glob(f"calibration.{PROFILE}*.json"):
        path.unlink(missing_ok=True)


if __name__ == "__main__":
    try:
        main()
    finally:
        cleanup()
    print(f"\n{performed - len(failures)}/{performed} passed"
          + (f"\nFAILED: {', '.join(failures)}" if failures else ""))
    sys.exit(1 if failures else 0)
