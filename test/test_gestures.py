"""Proof that the gesture logic works, without anyone wearing the headband.

GestureRecognizer is pure arithmetic over a Levels tick and a timestamp, so
we can feed it a made-up envelope trace and assert the right events come out. This
is how you check a change to the thresholds or timings did not break CLENCH before
you put the band on.

    python test_gestures.py
"""

from clench_detect import GestureRecognizer, Levels, hold_runs

TICK = 0.05          # same 20 Hz tick the real loop runs at
EMG_THRESHOLD = 30.0
BLINK_THRESHOLD = 40.0
QUIET_EMG = 10.0     # comfortably below threshold
QUIET_BLINK = 12.0
HOLD_THRESHOLD = 50.0
QUIET_HOLD = 5.0     # the mean of noise averages towards zero, so rest is tiny
LONG_BLINK_MS = 400

# Measured offline against a simulated eyelid step (see the long-blink notes in
# clench_detect): a NORMAL blink's hold level is ~66 uV and a HELD one's is
# ~56-83 uV. They overlap, on purpose -- these tests exist to prove the two are
# told apart by duration and not by level, because level cannot do it.
BLINK_HOLD_LEVEL = 66.0
HELD_HOLD_LEVEL = 80.0


def run(script, hold_threshold=None):
    """Play a list of steps and return the event names that fired.

    Each step is (duration_s, emg, blink_left, blink_right, hold). Shorthands:
    a 3-tuple means both forehead channels see the same blink level, and a
    3- or 4-tuple leaves the eyelid-hold level at rest.

    Pass hold_threshold to enable LONG_BLINK; leaving it None reproduces a
    profile calibrated before that gesture existed.

    Timestamps are synthetic and advance by exactly TICK, so the test is
    deterministic -- no sleeping, no wall clock, runs in milliseconds.
    """
    recognizer = GestureRecognizer(EMG_THRESHOLD, BLINK_THRESHOLD,
                                   long_ms=1500, double_ms=700,
                                   hold_threshold=hold_threshold,
                                   long_blink_ms=LONG_BLINK_MS)
    now = 100.0
    fired = []
    for step in script:
        hold = QUIET_HOLD
        if len(step) == 3:
            duration, emg, blink = step
            left = right = blink
        elif len(step) == 4:
            duration, emg, left, right = step
        else:
            duration, emg, left, right, hold = step
        for _ in range(int(round(duration / TICK))):
            levels = Levels(emg, left, right, hold, hold)
            for name, _detail in recognizer.update(levels, now):
                fired.append(name)
            now += TICK
    return fired


# A blink is a swoop (p2p spikes) whose hold level rises with it; a LONG blink is
# the same swoop followed by the level staying up. These build both from one place
# so the only difference between the two scripts is how long the level persists.
def swoop(hold_level=BLINK_HOLD_LEVEL):
    return (0.15, QUIET_EMG, 120.0, 120.0, hold_level)


def held(seconds):
    return (seconds, QUIET_EMG, QUIET_BLINK, QUIET_BLINK, HELD_HOLD_LEVEL)


def quiet(seconds):
    return (seconds, QUIET_EMG, QUIET_BLINK, QUIET_BLINK, QUIET_HOLD)


def check(label, fired, expected):
    ok = fired == expected
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        expected {expected}")
        print(f"        got      {fired}")
    return ok


def main():
    print("Gesture logic tests (no hardware):")
    results = []

    # A 300 ms clench, then quiet. One CLENCH on release.
    results.append(check(
        "short clench -> CLENCH",
        run([(0.5, QUIET_EMG, QUIET_BLINK),
             (0.3, 80.0, QUIET_BLINK),
             (1.0, QUIET_EMG, QUIET_BLINK)]),
        ["CLENCH"]))

    # Held for 2 s. LONG_CLENCH fires at the 1.5 s mark, and the release must NOT
    # also produce a CLENCH -- one gesture, one event.
    results.append(check(
        "2 s hold -> LONG_CLENCH only",
        run([(0.5, QUIET_EMG, QUIET_BLINK),
             (2.0, 80.0, QUIET_BLINK),
             (1.0, QUIET_EMG, QUIET_BLINK)]),
        ["LONG_CLENCH"]))

    # Two clenches 500 ms apart both count: the refractory gap is only 250 ms.
    results.append(check(
        "two separate clenches -> CLENCH CLENCH",
        run([(0.5, QUIET_EMG, QUIET_BLINK),
             (0.2, 80.0, QUIET_BLINK),
             (0.5, QUIET_EMG, QUIET_BLINK),
             (0.2, 80.0, QUIET_BLINK),
             (1.0, QUIET_EMG, QUIET_BLINK)]),
        ["CLENCH", "CLENCH"]))

    # A 50 ms twitch is below MIN_EVENT_MS and must be ignored.
    results.append(check(
        "brief twitch -> nothing",
        run([(0.5, QUIET_EMG, QUIET_BLINK),
             (0.05, 80.0, QUIET_BLINK),
             (1.0, QUIET_EMG, QUIET_BLINK)]),
        []))

    # Two blinks 300 ms apart -> DOUBLE_BLINK, and no stray single BLINK.
    results.append(check(
        "two fast blinks -> DOUBLE_BLINK",
        run([(0.5, QUIET_EMG, QUIET_BLINK),
             (0.1, QUIET_EMG, 120.0),
             (0.3, QUIET_EMG, QUIET_BLINK),
             (0.1, QUIET_EMG, 120.0),
             (1.5, QUIET_EMG, QUIET_BLINK)]),
        ["DOUBLE_BLINK"]))

    # One blink alone -> BLINK, only after the double-blink window expires.
    results.append(check(
        "one blink -> BLINK",
        run([(0.5, QUIET_EMG, QUIET_BLINK),
             (0.1, QUIET_EMG, 120.0),
             (1.5, QUIET_EMG, QUIET_BLINK)]),
        ["BLINK"]))

    # Blinks 1.2 s apart are two singles, not a double.
    results.append(check(
        "two slow blinks -> BLINK BLINK",
        run([(0.5, QUIET_EMG, QUIET_BLINK),
             (0.1, QUIET_EMG, 120.0),
             (1.2, QUIET_EMG, QUIET_BLINK),
             (0.1, QUIET_EMG, 120.0),
             (1.5, QUIET_EMG, QUIET_BLINK)]),
        ["BLINK", "BLINK"]))

    # --- the both-channels-must-agree rule (the new blink detector) ---

    # One forehead channel spiking alone is NOT a blink. This is the whole point:
    # a loose electrode or a stray movement usually hits one side only.
    results.append(check(
        "left forehead channel alone -> nothing",
        run([(0.5, QUIET_EMG, QUIET_BLINK, QUIET_BLINK),
             (0.1, QUIET_EMG, 120.0, QUIET_BLINK),
             (1.5, QUIET_EMG, QUIET_BLINK, QUIET_BLINK)]),
        []))

    results.append(check(
        "right forehead channel alone -> nothing",
        run([(0.5, QUIET_EMG, QUIET_BLINK, QUIET_BLINK),
             (0.1, QUIET_EMG, QUIET_BLINK, 120.0),
             (1.5, QUIET_EMG, QUIET_BLINK, QUIET_BLINK)]),
        []))

    # Both channels spiking, but 250 ms apart, is not one blink either.
    results.append(check(
        "channels spiking 250 ms apart -> nothing",
        run([(0.5, QUIET_EMG, QUIET_BLINK, QUIET_BLINK),
             (0.1, QUIET_EMG, 120.0, QUIET_BLINK),
             (0.25, QUIET_EMG, QUIET_BLINK, QUIET_BLINK),
             (0.1, QUIET_EMG, QUIET_BLINK, 120.0),
             (1.5, QUIET_EMG, QUIET_BLINK, QUIET_BLINK)]),
        []))

    # Sitting still must never fire anything. This is the one that matters most:
    # a false CLENCH means the board picks a tile nobody asked for.
    results.append(check(
        "30 s of rest -> nothing",
        run([(30.0, QUIET_EMG, QUIET_BLINK)]),
        []))

    # ================= the long blink: the second input =================
    # The whole reason this gesture exists: a deliberate hold must be
    # distinguishable from the blinks a person cannot help making.

    # Lid closes and stays down for 750 ms. LONG_BLINK fires once, at the 400 ms
    # mark, and the swoop that started it must NOT also be reported as a BLINK.
    results.append(check(
        "eyes held 750 ms -> LONG_BLINK only",
        run([quiet(0.5), swoop(), held(0.6), quiet(1.5)],
            hold_threshold=HOLD_THRESHOLD),
        ["LONG_BLINK"]))

    # The one that matters most. An ordinary blink's hold level is as high as a
    # held one's, so if this fired LONG_BLINK the gesture would be unusable --
    # every involuntary blink would trigger it.
    results.append(check(
        "ordinary 150 ms blink -> BLINK, never LONG_BLINK",
        run([quiet(0.5), swoop(), quiet(1.5)],
            hold_threshold=HOLD_THRESHOLD),
        ["BLINK"]))

    # A 2 s hold is still one gesture, not four.
    results.append(check(
        "2 s hold -> one LONG_BLINK, not repeats",
        run([quiet(0.5), swoop(), held(2.0), quiet(1.5)],
            hold_threshold=HOLD_THRESHOLD),
        ["LONG_BLINK"]))

    # --- the two inputs must not trigger each other ---

    # A hard clench is EMG on the ear electrodes; it must leave the eyelid
    # detector alone.
    results.append(check(
        "hard clench -> CLENCH, no LONG_BLINK",
        run([quiet(0.5), (0.3, 80.0, QUIET_BLINK, QUIET_BLINK, QUIET_HOLD),
             quiet(1.0)],
            hold_threshold=HOLD_THRESHOLD),
        ["CLENCH"]))

    # And the reverse: holding the eyes shut must not read as a jaw clench.
    results.append(check(
        "long blink -> LONG_BLINK, no CLENCH",
        run([quiet(0.5), swoop(), held(0.6), quiet(1.5)],
            hold_threshold=HOLD_THRESHOLD),
        ["LONG_BLINK"]))

    # Two fast blinks still pair up with the hold detector switched on: adding the
    # second input must not cost us the first behaviour.
    results.append(check(
        "double blink still works alongside the hold detector",
        run([quiet(0.5), swoop(), quiet(0.3), swoop(), quiet(1.5)],
            hold_threshold=HOLD_THRESHOLD),
        ["DOUBLE_BLINK"]))

    # A profile saved before this gesture existed has no hold threshold, and must
    # keep behaving exactly as it did -- no LONG_BLINK, no crash.
    results.append(check(
        "profile without hold calibration -> no LONG_BLINK",
        run([quiet(0.5), swoop(), held(0.6), quiet(1.5)]),
        ["BLINK"]))

    # Rest, with everything enabled.
    results.append(check(
        "30 s of rest with the hold detector on -> nothing",
        run([quiet(30.0)], hold_threshold=HOLD_THRESHOLD),
        []))

    # =============== hold_runs: what the calibration measures ===============
    # Calibration decides the long-blink threshold from how high AND how long the
    # holds were. On synthetic data there are no eyelid steps to find, so only the
    # "no holds detected" branch ever runs there -- these drive the other one.

    def samples(*steps):
        """(duration_s, left, right) steps -> the (time, Levels) list collect returns."""
        out = []
        now = 0.0
        for duration, left, right in steps:
            for _ in range(int(round(duration / TICK))):
                out.append((now, Levels(QUIET_EMG, QUIET_BLINK, QUIET_BLINK,
                                        left, right)))
                now += TICK
        return out

    FLOOR = 20.0

    runs = hold_runs(samples((0.5, 2.0, 2.0), (0.8, 80.0, 75.0), (0.5, 2.0, 2.0)),
                     FLOOR)
    results.append(check(
        "hold_runs finds one hold and times it",
        [(round(peak), round(ms / 50) * 50) for peak, ms in runs],
        [(75, 750)]))

    # Both lids move together, so one channel offset alone is a bad electrode.
    results.append(check(
        "hold_runs ignores a single channel drifting",
        hold_runs(samples((0.5, 2.0, 2.0), (0.8, 80.0, 2.0), (0.5, 2.0, 2.0)), FLOOR),
        []))

    # Two holds in one recording: calibration takes the longest as the deliberate
    # one, so both have to be found separately rather than merged.
    runs = hold_runs(samples((0.3, 2.0, 2.0), (0.2, 70.0, 70.0), (0.3, 2.0, 2.0),
                             (0.8, 90.0, 90.0), (0.3, 2.0, 2.0)), FLOOR)
    results.append(check(
        "hold_runs separates two holds",
        [round(ms / 50) * 50 for _peak, ms in runs],
        [150, 750]))

    # A hold still going when the recording stops must not be dropped.
    runs = hold_runs(samples((0.3, 2.0, 2.0), (0.5, 60.0, 60.0)), FLOOR)
    results.append(check(
        "hold_runs keeps a hold that runs to the end of the recording",
        len(runs), 1))

    results.append(check(
        "hold_runs finds nothing in a quiet recording",
        hold_runs(samples((2.0, 2.0, 2.0)), FLOOR),
        []))

    passed = sum(results)
    print(f"\n{passed}/{len(results)} passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
