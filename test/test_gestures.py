"""Proof that the gesture logic works, without anyone wearing the headband.

GestureRecognizer is pure arithmetic over a Levels tick and a timestamp, so
we can feed it a made-up envelope trace and assert the right events come out. This
is how you check a change to the thresholds or timings did not break CLENCH before
you put the band on.

    python test_gestures.py
"""

from clench_detect import GestureRecognizer, Levels

TICK = 0.05          # same 20 Hz tick the real loop runs at
EMG_THRESHOLD = 30.0
BLINK_THRESHOLD = 40.0
QUIET_EMG = 10.0     # comfortably below threshold
QUIET_BLINK = 12.0


def run(script):
    """Play a list of steps and return the event names that fired.

    Each step is (duration_s, emg, blink_left, blink_right). A 3-tuple is
    accepted as shorthand meaning both forehead channels see the same thing.

    Timestamps are synthetic and advance by exactly TICK, so the test is
    deterministic -- no sleeping, no wall clock, runs in milliseconds.
    """
    recognizer = GestureRecognizer(EMG_THRESHOLD, BLINK_THRESHOLD,
                                   long_ms=1500, double_ms=700)
    now = 100.0
    fired = []
    for step in script:
        if len(step) == 3:
            duration, emg, blink = step
            left = right = blink
        else:
            duration, emg, left, right = step
        for _ in range(int(round(duration / TICK))):
            for name, _detail in recognizer.update(Levels(emg, left, right), now):
                fired.append(name)
            now += TICK
    return fired


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

    passed = sum(results)
    print(f"\n{passed}/{len(results)} passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
