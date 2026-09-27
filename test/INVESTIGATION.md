# Detection investigation — 2026-09-26

Scope: `test/` only, as clarified by the user. Findings below refer to the
**original code at f94d30d**, so line numbers remain meaningful after fixes.
No physiological cause or measured human accuracy can be inferred from code alone.

## Current signal path and calibration

`config.py:80-101` selects BrainFlow Muse 2 or its synthetic board. There is no
replay source here yet. `eeg_channels_and_names` gets the board channel mapping;
callers take positions TP9, AF7, AF8, TP10 (`clench_detect.py:911-918`). Muse EEG is
256 Hz. Synthetic uses its own sampling rate and first four EEG channels.

`clench_detect.py:199-304`: every 50 ms, read the most recent 1 s. Use a fourth-order
zero-phase Butterworth filter, trimming the last 20 ms. Ear EMG: notch 50/60 Hz,
bandpass 20–110 Hz, RMS of a 200 ms tail, **max** of TP9/TP10. Blinks: 1–10 Hz,
peak-to-peak of a 300 ms tail, retain AF7 and AF8 separately. Holds: 0.5–8 Hz,
absolute mean of a 250 ms tail, **min** of AF7/AF8.

Important implementation defect: `_filtered` promises a copy but uses
`np.ascontiguousarray` (207). For an already contiguous float64 row, this aliases
the raw data. BrainFlow filters in place. `read_levels` computes blink before
hold (302-303), so hold receives an already 1–10 Hz-filtered signal.

Calibration (`405-594`) records derived Levels in memory, not raw samples:

| Phase | Duration / channels | Computation |
|---|---|---|
| Rest | default 10 s; all four | EMG median and 1.4826 × MAD of max ears. Blink and hold median/MAD of max forehead channels. Individual forehead resting medians also saved. |
| Clench | 3 × 2 s | Max EMG tick per trial, then median of trial maxima. If peak > 1.5 × rest, threshold = rest + 0.30 × (peak − rest); otherwise rest + kσ, default k=6. |
| Blink | 6 s, deliberate blinks about once/second | Count contiguous stretches with both channels above rest + 4σ. With ≥2 stretches, threshold halfway from that floor to smallest peak. Otherwise keep statistical floor. |
| Eye hold | 3 × 2 s, asked to close for about 1 s | Find longest both-channel run above rest + 5σ per trial. With ≥2 runs, threshold halfway from floor to weakest run's peak, even if runs are too short. |

Total: 28 s of collection plus human prompts and initial buffer fill; about 560
overlapping ticks, **not 560 independent examples**. Only 3 active clench trials.
`--no-clench-cal` records rest only. `--blink-only` reuses all prior clench numbers
(455-473), even though rest was just remeasured.

Saved JSON (`568-589`) contains board, fs, method tag, date, rest/sigma/peaks/
thresholds, trial summaries, blink separation, forehead rest medians and hold
durations. No per-ear noise, raw waveforms, quality decision or cross-gesture trial
responses. `calibration.<profile>.json` lives beside the script; synthetic has a
separate suffix (`49-59`). Files are git-ignored. No database is involved.
Loading checks board label and blink method (`74-93`); no fs/schema/finite-value
validation, expiry or physical wearer verification. Named profiles are isolated;
the default name can be reused accidentally. CLI names lack the station's name
validation. The old Kushagra file lacks the blink method and is rejected.

Detection really uses loaded per-user thresholds (`829-845` and game constructors).
Station captures the selected profile in its run arguments (`muse_station.py:682-698`)
and loads that name (`751-764`); there is no evidence of silent profile substitution.
Timings are CLI defaults or station constants, not learned from the person.

## Requested cause checklist

| Suspected cause | Verdict and evidence in original code |
|---|---|
| Hard-coded amplitude/timing | **Partly.** Gesture amplitudes are calibrated; 120 µV forehead warning is absolute (130,439-443). Station contact std uses <1 or >200 µV (`muse_station.py:66-68,294-306`). Fixed windows: 1 s filter, 200/300/250 ms measures, 60 ms eye coincidence, 80 ms clench minimum, 200/250/600 ms refractory (`97-130`). |
| Save/load/profile bug | **No obvious wrong-user load.** Separate names and synthetic files; see above. **Yes** weak/failed calibration overwrites selected file (589), no validation of numeric fields, same default profile is easy to reuse. |
| Absolute vs noise-relative | **Relative, with a serious defect.** Active clench formula replaces rest+6σ without retaining a floor (445,489-490). Release = 0.6 × absolute threshold (641), which can sit below the person's resting signal and never release. Blink uses the noisier channel's floor for both eyes (425-428), penalizing asymmetry. |
| Too few/bad samples | **Yes.** Three clench maxima; accepts even one nonempty trial (483-488). Empty rest becomes (0,1) (397-398). Weak clench/blink/hold only warn (494-498,520-527,551-566), then save. No rejection/retry or replay at final thresholds. |
| Timing fits one user | **Possible; not measured.** Long clench 1500 ms, double blink 700 ms, hold 400 ms. Station hard-codes all three (695-697). Hold calibration checks and saves constant 400 rather than supplied CLI setting (556,585). Double-blink interval measures detected rises, not quiet gap (805-806); closely spaced raw blinks can merge under the 300 ms envelope/hysteresis. |
| Contact/quality | **Advisory only.** Forehead warning and station raw std bars; no HSI, explicit rail check, nonfinite guard or gate. A warning does not establish poor contact as the unique cause. Streaming freshness monitored in station after 4 s (`muse_station.py:254-292`); standalone detector can reread stale data. |
| Channel selection/bad channel | **Correct channel groups; weak handling.** Max ears lets a noisy ear dominate. Both-eye rule rejects unilateral artifacts but a bad/noisy eye can block genuine blinks. No channel selection/normalization (301-304,687-721). |
| Drift | **Yes, static baseline.** No re-estimation in detection (829-871). Reusing a file after band movement is only discouraged in text. |
| Blink/clench confusion | **Possible, unmeasured.** Filters separate features but recognizers run independently (766-803); simultaneous jaw/eye events are possible. emit_start bypasses 80 ms validation (768-771). Existing envelope tests assume perfect separation, so they cannot prove raw-signal discrimination. |

## Measurements available now

Both `data/recordings/` (initial inventory) and `test/recordings/` had no raw
recordings. Six saved profiles exist: there is data from other people, but **only
summary data**, not event-labeled waveforms. These are calibration statistics,
not hits/misses, clinical SNR, or proof that any person performed a gesture badly.

| Profile | Ear max rest RMS / robust σ (µV) | Clench peak RMS (µV) | Peak/rest | Threshold above rest (σ) | AF7 / AF8 rest p2p (µV) | Hold runs (ms) |
|---|---:|---:|---:|---:|---:|---|
| taher | 5.07 / 0.88 | 156.08 | 30.76 | 51.36 | 42.10 / 45.06 | absent |
| taher2 | 4.96 / 0.64 | 112.88 | 22.76 | 50.41 | 37.62 / 47.79 | 256,154,205 |
| abhay | 4.54 / 0.70 | 26.05 | 5.74 | 9.22 | 52.04 / 84.58 | none |
| nikhil | 6.37 / 0.89 | 14.51 | 2.28 | 2.73 | 493.93 / 343.72 | 154,51,102 |
| madhu | 159.58 / 59.48 | 294.15 | 1.84 | 0.68 | 80.24 / 421.50 | 102,103 |
| kushagra (obsolete format) | 10.51 / 1.91 | 51.94 | 4.94 | 6.49 | absent | absent |

Individual TP9/TP10 values and per-channel clench SNR cannot be reconstructed.
Taher's signal clears his rest by much more than the others. Nikhil's original
release level is 5.29 µV, below 6.37 µV rest; Madhu's is 119.97, below 159.58 rest.
Replaying a **synthetic** 300 ms clench at Nikhil's saved levels yields no CLENCH
and one false LONG_CLENCH, remaining active at rest. This reproduces a mechanism
consistent with missed clenches, not the unavailable human recording itself.

A deterministic raw 100 µV eyelid step gives hold=51.1457 µV directly, but only
7.9173 µV via the original `read_levels`: the in-place filter bug is reproducible.
The original 24 gesture checks pass because they start after signal processing.
New regression tests fail in seven cases before fixes (four filter, three scaled
weak-clench loop cases).

## Drill: why a logged LONG_BLINK can coexist with a miss

`station_activities.py:381-400` correctly scores LONG_BLINK during an active
LONG_BLINK prompt. There is no event-name mismatch in station dispatch (844-849).
But every detection is logged, including events between prompts, after a 4 s
timeout or after completion. Between prompts it counts as stray; finished drills
ignore it. An earlier wrong input ends the round immediately. There is no need
to release/rearm before the next random prompt (404-433). Keeping eyes shut after
a hit can therefore produce another eyes-shut prompt while the detector is still
latched; it will not fire again until release. Timing is measured at queue
delivery, and no per-round event timeline is retained. These are falsifiable
explanations; the user's actual cause is still unconfirmed without a timeline.

## Ranked work and limits

1. Fix filter ownership and use baseline-relative hysteresis everywhere (high
   impact, low effort). Invalidate old hold calibration because its units changed.
2. Validate coverage, sustained activity and noise separation; never save a
   failed required clench calibration. Replay holds at their final threshold.
   Preserve explicit clench-only operation when eye calibration fails (high
   impact, medium effort). Prompt for comfortable repeatable strength, not maximum.
3. Drill release/rearm, explicit event disposition and a saved timeline (high
   diagnostic value, medium effort). Keep scores honest; don't turn late events
   into hits. Use the existing Drill as the post-calibration test.
4. Per-channel calibrated thresholds / bad-ear fallback, streaming contact gate,
   sensitivity control and drift monitoring (medium/large effort). Need labeled
   recordings first: a raw-amplitude gate during a real clench can reject the
   intended gesture, and adapting to noisy contact can normalize away a fault.
   Muse HSI isn't currently consumed. Existing station fit display is not HSI.

Saved profiles already exist. A web Caregiver Console is outside the clarified
scope; feedback belongs in Muse Station for this task. No event contracts need
changing. Automatic adaptive thresholds over time and a live sensitivity slider
are deferred; calibration-derived adaptive thresholds are the first priority.

## Record the missing comparison

For **you and one friend**, freshly calibrate a named profile, copy its JSON into
`test/recordings/`, disconnect Station, then run from the repo root:

```powershell
.\test\.venv\Scripts\python.exe test/record.py --seconds 180 --label friend01-generalization
# Repeat as --label taher-generalization. Keep the matching profile with each run.
```

Use the printed recording countdown (seconds):

- 0–30: rest, eyes open, jaw relaxed; allow natural blinks.
- 30–60: six comfortable 0.3–0.5 s clenches at 30,35,40,45,50,55.
- 60–90: five 2 s clenches at 60,66,72,78,84.
- 90–120: six double blinks at 90,95,100,105,110,115; usual comfortable spacing.
- 120–150: six 1 s eye closures at 120,125,130,135,140,145; **open fully between**.
- 150–180: rest with natural blinks; observer separately notes speech/head movement.

Use generated `<timestamp>_friend01-generalization_default.csv` and equivalent
Taher file. The auxiliary/ancillary files aren't inputs to this detector.
An observer must record actual actions, omissions and onset/release times against
the countdown; do not treat intended prompts as proof an action happened. Keep
timestamps relative to the **first CSV sample**, correcting any countdown offset.
Repeat after 5 minutes without moving the band to test drift. Record band
adjustments and current profile date; don't share one profile across participants.

Create `test/recordings/comparison.json`: array of entries like this (add all
observed actions; end includes filter/release delay, no overlapping windows):

```json
[
  {
    "recording": "<timestamp>_friend01-generalization_default.csv",
    "profile": "calibration.friend01.json",
    "board_id": 38,
    "baseline": [2, 28],
    "actions": [
      {"start": 30, "end": 32, "event": "CLENCH"},
      {"start": 60, "end": 63, "event": "LONG_CLENCH"},
      {"start": 90, "end": 93, "event": "DOUBLE_BLINK"},
      {"start": 120, "end": 122.5, "event": "LONG_BLINK"}
    ]
  }
]
```

`board_id=38` is Muse 2; use the actual board ID for other sources. The evaluator
runs `read_levels` and the recognizer at 20 Hz without sleeping. It reports per-file
hits, misses, false/duplicate triggers, timestamps, and per-channel 20–110 Hz
baseline RMS/MAD, clench peak/rest ratio and separation in robust sigma units.
It doesn't call these ratios validated physiological SNR. Ordinary BLINK is ignored
for false-trigger counts unless explicitly labeled. Bad/missing labels invalidate
accuracy estimates. Baseline/peak feature intervals include filter lag.

```powershell
.\test\.venv\Scripts\python.exe test/evaluate_detection.py --profiles
.\test\.venv\Scripts\python.exe test/evaluate_detection.py --manifest test/recordings/comparison.json --output test/recordings/comparison-results.json
```

Keep this report's pre-fix summaries for comparison. Recalibrate eye holds after
the filter fix; old and new hold thresholds are not interchangeable. Accuracy on
real people remains unverified until these recordings and a fresh Drill exist.

## Implemented calibration changes

- Filter functions own their buffers; all live consumers share a recognizer
  factory using baseline-relative release levels. Old hold feature versions are
  disabled when loading (the file itself is preserved).
- Rest checks flat, nonfinite, high-noise and repeated-extrema/clipping patterns
  per channel, with a named adjustment message. At least 80% sampling coverage,
  increasing timestamps, finite features and no >250 ms gap are required. These
  are conservative heuristics, not measured electrode impedance. Stream timestamp
  deduplication and a live contact/drift gate are still not implemented.
- Three clench trials must clear the noise floor by two additional robust sigma.
  Use their weakest 75th-percentile sustained level, retain rest+6σ / rest p99
  as a minimum, and require a 300 ms continuous crossing per trial. Failed clench
  calibration returns without replacing the previous profile.
- Blink requires at least three extracted samples and two-sigma separation above
  its floor, otherwise it is explicitly off. A hold must pass replay at the
  **final** threshold/duration in all three trials, otherwise it is off. A valid
  clench can be saved with eye inputs off; the UI says to retry the eyes.
- Saves use an atomic replacement; profile names, sampling rate and loaded
  thresholds receive validation. The explicit rest-only quick mode remains for
  synthetic/headband-free smoke checks, without claiming active-gesture accuracy.

These rules are deterministic engineering checks, not thresholds validated on a
human population. The measurements needed for individual channel normalization,
fatigue tolerance and drift behavior are still missing.

## Implemented Drill changes

The scoring model now has deterministic headless tests for on-time LONG_BLINK,
late detections, delayed queue delivery, wrong inputs, missing fresh samples and
consecutive prompts while eyes remain held. Station's Drill requires 600 ms of
released, neutral signal after refractory periods, with a fresh detector update,
before starting a prompt. It explicitly asks the person to open eyes and relax.
Each event records detection time and scoring reason. Saved reports in
`test/recordings/drill-<timestamp>.json` contain profile, rounds, outcomes and event
timeline. A late *delivery* can correct a timeout only if the detection timestamp
was within the original response window; an actually late gesture cannot.

This closes a reproducible Drill timing/rearm gap. It does not establish that the
same gap caused the user's particular run; the saved timeline will distinguish
that from weak signal, wrong-input termination or actual response timeout.

## Additional raw-signal finding

A synthetic ordinary double-blink waveform through the real filters can produce
LONG_BLINK at an arbitrarily low hold threshold, despite the original hand-written
envelope tests passing. Therefore successful held-eye trials alone are insufficient.
Hold calibration now also requires at least three recorded ordinary blink samples
and rejects a candidate if those samples or the clench trials trigger its hold
state machine. This is a calibration veto, not a claim that band separation alone
prevents confusion. Blinking or clenching in ways absent from calibration can
still behave differently; the friend protocol remains necessary.
