# Blink / clench library comparison

Evaluated 2026-09-26. Everything in this experiment lives in `test/`.
**No human accuracy or cross-user generalization has been demonstrated.**
At the initial synthetic evaluation there were no raw human EEG CSVs; saved calibration
summaries and Drill event logs cannot be used to re-run alternative detectors.
Two human recordings were subsequently collected and compared. At the user's
request, Station now defaults to rolling MNE blinks plus the saved calibrated jaw
detector; see [README.md](README.md#live-mne-blinks--calibrated-clenches) for live
behavior, limitations and replay commands. The whole-file benchmark below remains
separate from that rolling adapter and does not measure its live accuracy.

## What actually ran

| Candidate | Evaluation | Limits |
| --- | --- | --- |
| Current detector | Actual `read_levels` and `recognizer_from_calibration`, 20 Hz polling | Synthetic profiles use a separate training signal and simplified threshold construction, not the full interactive Station calibration. Human recordings require their matching saved profile. |
| MNE 1.13.2 | Actual `find_eog_events`, AF7/AF8, default 1–10 Hz, microvolts converted to volts | Offline blink peaks; no clench, double-blink grouping, or held-eye classifier. Default threshold uses the whole recording's range, including test actions. |
| NeuroKit2 0.2.13 EOG | Actual `eog_clean` + `eog_peaks`, average AF7/AF8; original and inverted polarity reported separately | Offline; polarity must be selected from calibration before a held-out test, not by choosing whichever score wins. No held-eye classifier. |
| NeuroKit2 EMG adapter | Actual `emg_activation`, supplied with our 20–110 Hz, 200 ms RMS envelope; max of TP9/TP10; threshold = baseline median + 6 robust sigmas; 80 ms minimum | Custom preprocessing, **not** the default NK EMG pipeline. Muscle activity is not necessarily a jaw clench. Emits offsets to match current release semantics. |
| muse-vtuber | Imported original blink/clench classes at `1d6453280c6547ceb6545bc58db60bd255bb3b25`; 4-sample frames and a separate 64-sample comparison | Default clench threshold 25 uV, five-frame duration requirement, average of raw TP9/TP10 before filtering. Blink detector expects negative pulses. No LONG_BLINK. No IMU supplied in this EEG-only comparison. |
| LibMuse | Public SDK availability reviewed; no executable SDK available locally | SDK application/licensing required. Current artifact API and headset compatibility unverified; **not tested**. |

NeuroKit's default `emg_process(..., sampling_rate=256)` executes, but in 0.2.13
the public `emg_amplitude(emg_cleaned)` has no sampling-rate parameter and its
internal envelope assumes 1000 Hz. Simply passing 256 to `emg_process` does not
fix that. The custom adapter avoids that assumption. Source inspection and an
actual 256 Hz invocation confirmed this distinction.

The upstream vtuber blink stage has median/MAD adaptation, shape checks, and
temporal high-frequency rejection. Its optional gyro rejection cannot help this
EEG-only run. `guard_speech=False` matches its pipeline configuration. Its
documentation references blink validation, but the referenced research document
was absent from the checkout; this is not independent accuracy evidence.

Primary sources:
[MNE API](https://mne.tools/stable/generated/mne.preprocessing.find_eog_events.html),
[NK EOG](https://neuropsychology.github.io/NeuroKit/functions/eog.html),
[NK amplitude source](https://neuropsychology.github.io/NeuroKit/_modules/neurokit2/emg/emg_amplitude.html),
[vtuber blink source](https://github.com/Ludentes/muse-vtuber/blob/1d6453280c6547ceb6545bc58db60bd255bb3b25/src/muse_vtuber/pipeline/blink.py),
[vtuber clench source](https://github.com/Ludentes/muse-vtuber/blob/1d6453280c6547ceb6545bc58db60bd255bb3b25/src/muse_vtuber/pipeline/clench.py),
[Muse SDK access](https://choosemuse.com/pages/developers).

## Measured synthetic results

Each case contains four single blinks and three 0.5-second clenches in 50 seconds.
Scoring starts at 10 seconds, after baseline/warm-up. Seeded noise and simple
Gaussian eye pulses / sine muscle bursts are engineering probes, not biological
models. Current thresholds come from separate synthetic training data. None uses
Taher's saved profile.

Blink cells are **hits / 4; extra triggers**. `Error` means a real library exception,
not zero misses. NK columns are different fixed-polarity experiments.

| Signal change | Current | MNE | NK original | NK inverted | vtuber 4 samples | vtuber 64 samples |
| --- | --- | --- | --- | --- | --- | --- |
| Reference negative blink | 4; 0 | 4; 0 | Error | 4; 0 | 4; 0 | 4; 0 |
| All amplitude/noise multiplied by 0.2 | 4; 0 | 4; 0 | Error | 4; 0 | 4; 0 | 4; 0 |
| Noise SD increased from 2 to 8 uV | 4; 4 | 4; 2 | Error | 4; 0 | 4; 0 | 4; 0 |
| AF8 blink amplitude multiplied by 0.1 | 3; 0 | 4; 0 | Error | 4; 0 | 4; 0 | 4; 0 |
| Each blink accompanied by 180 ms ear burst | 4; 0 | 4; 0 | Error | 4; 0 | 0; 0 | 1; 0 |
| Positive blink polarity | 4; 0 | 4; 0 | 4; 0 | Error | 0; 0 | 0; 0 |

Current and the custom NK EMG adapter found all three clenches in each case.
**Both also emitted four false clenches during the ear-burst blinks.**
Both vtuber frame sizes missed all three short clenches in every case. Its
zero false clenches therefore do not establish a useful clench classifier.
MNE and NK EOG have no clench output; their zero clench counts are not a benefit.
NK EOG raised `ZeroDivisionError` on the unfavorable-polarity probes; the harness
records that failure and continues evaluating other candidates.

These results reproduce a *possible* blink-to-clench contamination mechanism.
They do not identify whether Taher's real problem is band movement, facial
muscle activity, contact noise, thresholding, or some combination.

## Reproduce (PowerShell, repository root)

The isolated environment is already installed on this workstation. On another:

```powershell
.\test\.venv\Scripts\python.exe -m venv test/.venv-compare
.\test\.venv-compare\Scripts\python.exe -m pip install -r test/requirements-comparison.txt
git clone https://github.com/Ludentes/muse-vtuber.git test/recordings/muse-vtuber-evaluation
git -C test/recordings/muse-vtuber-evaluation checkout 1d6453280c6547ceb6545bc58db60bd255bb3b25
```

Only the original computational detector modules are imported. No upstream
application, network output, or interactive test scripts are run. The checkout
and environment are ignored by Git; optional dependencies are not added to the
live Station environment.

```powershell
.\test\.venv-compare\Scripts\python.exe test/compare_detectors.py --synthetic --output test/recordings/library-comparison-synthetic.json
.\test\.venv-compare\Scripts\python.exe -m pytest test -q -p no:cacheprovider
```

The JSON contains versions, errors, warnings, per-action matches, all emitted
events, false triggers, blink-associated clenches, and per-channel baseline noise,
eye/muscle RMS peaks and peak-to-baseline ratios. Those diagnostic features use
offline filtering and are not live latency or physiological SNR measurements.
Primitive events during unsupported same-family actions are explicitly unscored;
wrong-family triggers still count. No aggregate accuracy mixes unsupported tasks
with supported ones. `compute_seconds` is processing time, not response latency.

## Record Taher and a friend

Calibrate the correct profile in Station. Then disconnect Station from the
headband so the recorder can connect. Keep the band in the same position.

```powershell
.\test\.venv\Scripts\python.exe test/record_protocol.py --protocol blink-comparison --profile taher --label taher-blink-session1
```

About 2.5 minutes: 30 s rest, six gentle blinks, six firmer blinks, six comfortable
half-second clenches, 15 s small head turns/nods, 15 s rest. Follow the beeps;
keep the jaw relaxed during blinks. X retracts a missed action, M marks a slip,
Q stops and saves. An observer is useful for marking spontaneous blinks.
Labels record *cued intent*, not verified physical action; check surprising
scores against traces. Extra spontaneous blinks must not be mistaken for detector
false positives. Blink scoring windows allow 1.5 s after the cued hold for delayed
grouping; this is an accuracy test, not a speed test.

Files are `test/recordings/<timestamp>_taher-blink-session1_*`: EEG, available IMU
and other streams, labels, manifest, and a **unique calibration snapshot**.
A later calibration cannot overwrite the profile attached to an earlier file.
Repeat with the friend's own calibration/profile and `--label friend1-blink-session1`.
Use `--dry-run --speed 1000 --no-sound` to check cues without a headband.

```powershell
.\test\.venv-compare\Scripts\python.exe test/compare_detectors.py --manifest test/recordings/<timestamp>_taher-blink-session1_manifest.json --output test/recordings/taher-comparison.json
```

Replace `<timestamp>` with the filename printed by the recorder. The same command
works for the friend's manifest. Comparing the `channels` and `action_results`
sections gives noise/peak ratios and gentle-versus-firm misses for each person.
Recordings with sample-clock drift exceeding 100 ms are rejected before scoring.
IMU files are saved for later movement analysis, but are not fed to these adapters.

## Decision and next experiment

1. **High impact / low effort:** collect these real failures and compare every
   detector on identical files. MNE is the first blink candidate to investigate,
   not a proven live replacement.
2. **High impact / medium effort:** choose frontal channel/polarity from calibration,
   then test blink-versus-clench arbitration using temporal bursts and IMU. A
   blanket movement veto may merely replace false actions with missed actions.
3. **Medium effort:** if MNE's blink shapes win on held-out data, implement and
   evaluate a causal streaming counterpart; offline peak timestamps cannot prove
   interactive latency. Validate double blinks and held-eye behavior separately.
4. **Higher external dependency:** obtain LibMuse access and verify artifact
   callbacks for the actual headset/SDK before investing in an integration.

For evidence beyond Taher, freeze the algorithm and calibration rules, then test
all three other people without hand-tuning constants for each. Use a second
session after removing/refitting the band and repeat after several minutes to
test contact and drift. Keep calibration samples separate from scored actions.
Report per-person recall and false actions per minute, not just pooled accuracy.
Three repeats of this protocol give 36 blinks/person; add at least five minutes
of rest/movement for a useful false-trigger denominator. This initial comparison
does not validate LONG_BLINK, DOUBLE_BLINK or LONG_CLENCH.
