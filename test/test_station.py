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

import pathlib
import sys
import time
import types

import muse_station as ms
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
            # A withdrawn window reports nothing as mapped, so the prompt is
            # detected by its text rather than by its visibility.
            if click_ready and self.station.prompt_label.cget("text"):
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
    check("calibrate finishes", driver.pump(40, until=driver.idle, click_ready=True),
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
    driver.pump(3)
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

    station._on_close()
    return 0


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
