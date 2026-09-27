# Muse 2 + BrainFlow test bench

## Live MNE blinks + calibrated clenches

Muse Station and `clench_detect.py` now default to **MNE blinks** on AF7/AF8
and the existing per-profile jaw detector on TP9/TP10. Select the wearer's
profile (for example `nikhil`) and click **Listen** or **Drill**. The Blinks
selector offers `calibrated` to use the older eye detector for comparison.
Saved profiles are not rewritten when listening. MNE ignores the saved blink
threshold and `blink_enabled` flag. Calibration in MNE mode collects rest and
three jaw trials; no old eye-threshold or eye-hold trials are needed.

```powershell
.\test\.venv\Scripts\python.exe -m pip install -r test/requirements.txt
.\test\.venv\Scripts\python.exe test/muse_station.py --profile nikhil
# No headband:
.\test\.venv\Scripts\python.exe test/clench_flappy.py --keyboard
```

MNE finds blink peaks; it does **not** classify sustained eye closure. MNE mode
emits BLINK and DOUBLE_BLINK, disables LONG_BLINK, and Drill asks for **BLINK
ONCE** instead of holding the eyes shut. The jaw's CLENCH, CLENCH_START and
LONG_CLENCH behavior and saved thresholds remain unchanged.

The live adapter uses a 20-second rolling raw window, MNE's default EOG settings,
a 0.5-second trailing guard, and a scan every 0.25 seconds. It runs in a separate
worker with a bounded latest-window queue so jaw polling does not wait for MNE.
It waits up to 20 seconds for history and never emits warm-up history. Single
blinks also wait for the 0.7-second double-blink interval to finish processing;
expect roughly 1.2-1.5 seconds plus computation before a single-blink event.
Drill scores by the detected peak timestamp, not delivery time. Its latency
column therefore omits the MNE delivery delay. Packet gaps pause eye processing,
and a worker failure is logged while jaw detection continues.

**This is an integration, not proof of generalization.** Rolling MNE changes
channel/threshold selection as history moves, so whole-recording benchmark scores
do not transfer directly. Replays on the two available sessions matched 10/12
and 9/12 single-blink prompt windows, with 42/31 unmatched single-blink events and
12/6 double-blink events respectively. Some unmatched events can be unmarked
natural blinks. The jaw replay remains 5/6 and 3/6 within strict release windows;
the latter still has many extra jaw events. No parameters were tuned per recording.

Reproduce the combined path with the original manifest and an optional explicit
profile override (useful when the recording was labelled `friend1`):

```powershell
.\test\.venv\Scripts\python.exe test/evaluate_hybrid.py --manifest test/recordings/SESSION_manifest.json --profile test/calibration.nikhil.json --output test/recordings/hybrid-result.json
.\test\.venv\Scripts\python.exe -m pytest test/test_mne_blinks.py -q
.\test\.venv\Scripts\python.exe test/test_station.py
```

Synthetic regressions cover scale/noise variation, jaw equivalence, overlapping
windows, double-blink grouping, worker failure, delayed Drill events and keyboard
mode. They do not establish specificity on human noise or motion.

## Library comparison (2026-09-26)

See [LIBRARY_COMPARISON.md](LIBRARY_COMPARISON.md) for actual MNE, NeuroKit2 and
muse-vtuber runs, measured synthetic limitations, and the commands to compare
the same raw recording with every detector. For live integration, see above.
To capture gentle versus firm blinks and false clenches, use
`record_protocol.py --protocol blink-comparison --profile taher` after disconnecting
Station from the headband. This takes about 2.5 minutes and saves a separate
calibration snapshot with the recording. Repeat with the friend's own profile.
Raw waveforms also stream to `*_live_default.csv` (EEG), `*_live_auxiliary.csv`
(movement) and `*_live_ancillary.csv` (PPG when available) throughout acquisition.
`*_progress.json` saves cue intent as it happens, using Unix timestamps aligned
with the raw timestamp rows. These backups remain if the console closes early;
use Q or Ctrl+C for an orderly stop and final labeled exports. An abrupt exit
can lose the latest buffered samples and leaves progress requiring recovery.

## Reliability update (2026-09-26)

See [INVESTIGATION.md](INVESTIGATION.md) for the original-code audit, measured
profile comparison, remaining limits and the exact friend-recording protocol.
After updating, **recalibrate eye holds**: older profiles used a filter bug that
changed the hold feature. Loading one now disables LONG_BLINK with an explanation;
it does not modify the stored file.

Calibration now checks signal quality during rest, keeps the noise floor under
clench thresholds, measures sustained clench strength, and requires three usable
clench trials. Use a comfortable repeatable clench. A failed required calibration
leaves the saved profile intact and asks you to retry. Unusable blink/hold trials
leave those eye inputs explicitly disabled while retaining a valid clench input.
Hold samples must actually pass the final detector threshold and selected duration.
The same hold settings must also reject the recorded ordinary blinks and clenches;
otherwise LONG_BLINK stays off with a retry message.
Rest contact messages identify the channel to adjust; these are heuristics, not HSI.
The raw-amplitude contact gate only runs at rest, not during intentional gestures.

Run the headband-free regressions explicitly (`test/` is not in root pytest discovery):

```powershell
# From the repository root; uses the test-bench virtualenv.
.\test\.venv\Scripts\python.exe -m pip install -r test/requirements.txt
.\test\.venv\Scripts\python.exe -m pytest test -q
.\test\.venv\Scripts\python.exe test/test_station.py
.\test\.venv\Scripts\python.exe test/evaluate_detection.py --profiles
```

After calibration, run **Drill** as the "test it" step. It measures prompted hits,
misses, wrong inputs and unprompted triggers. A passing calibration is not an
accuracy guarantee. Live sensitivity controls, individual ear fallback and drift
adaptation remain future work pending labeled recordings.

Drill waits for the detectors to release and the signal to be quiet before each
prompt. Between prompts, **open your eyes and relax your jaw**. The status explains
whether a logged gesture was a hit, a wrong input or outside the prompt. Detection
timestamps survive the worker-to-window queue, so a delayed UI update cannot turn
an on-time detection into a miss. Truly late detections remain misses. Reports,
including the selected profile, round timings and event dispositions, are saved
to `test/recordings/drill-<timestamp>.json` on completion or closing the window.

The historical descriptions below describe the original bench; the update above
and investigation take precedence where calibration behavior differs.

Five small scripts to prove a Muse 2 headband is working before you build anything
on top of it. Everything runs in **synthetic mode** too, so teammates without a
headset can develop against the exact same code.

Board: `BoardIds.MUSE_2_BOARD` over the laptop's own Bluetooth. No BLED112 dongle,
and **not** `MUSE_2_BLED_BOARD` (that one is for the dongle).

---

## Setup

From this `test/` directory, in PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

macOS / Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Check the environment (prints the board layout and any platform warnings):

```powershell
python config.py --synthetic
```

### Platform notes

| OS | What to watch for |
|---|---|
| **Windows** | Needs build **10.0.19041** or newer for native BLE. `python config.py` prints your build and warns if it's too old. You do *not* need to pair the Muse in Windows Settings. |
| **macOS** | Avoid **12.0–12.2** (BLE scanning bugs) — update to 12.3+. Grant Bluetooth permission to your terminal or IDE: *System Settings → Privacy & Security → Bluetooth*. |
| **Linux** | `sudo apt install libdbus-1-dev`, and you may have to build BrainFlow from source with BLE enabled — the pip wheels often ship without it. |

**Only one app may hold the Muse at a time.** Close the Muse phone app, `muselsl`,
Mind Monitor, and any other copy of these scripts before connecting.

---

## Start here: the station

```powershell
python muse_station.py              # a window that holds the connection open
python muse_station.py --synthetic  # same, fake data, no headband
```

Every script below opens its own Bluetooth session and hands it back when it
exits. That is fine once and tiresome all day: you reconnect before each thing
you do, BLE needs ~5 s between a release and the next connect, and an unheld
Muse powers itself off after a few minutes.

The station connects **once** and holds it. Leave the window open, seat the band
using the live fit bars, and press **Calibrate** and **Listen** as often as you
like — all on the same connection. Nothing disconnects until you press
**Disconnect**. If the link drops by itself (you walked out of range, the band
slipped) it reconnects on its own, because a drop is not you asking to stop.

| Control | What it does |
|---|---|
| **Connect** / **Disconnect** | The only two things that change whether the headband is held. Board choice and device name are frozen while connected. |
| **Electrode fit** | Per-channel spread in µV, refreshed twice a second, with the same `FLAT?` / `NOISY?` verdicts `check_connection.py` gives. Watch these while seating the band. |
| **New...** | Claims a new calibration profile name. The profile is empty until you press Calibrate; the name has to be letters, digits, `-` or `_`, because it becomes a filename. |
| **Calibrate** | The guided rest / clench / blink / long-blink calibration. Opens a **screen-filling window showing one instruction and one timer, both enormous** — you take your glasses off to put the band on, which is exactly when small text stops working. **Enter** is the only control. Esc cancels. Saves to the selected profile. |
| **Listen** | The live detector: meters, and CLENCH / LONG_CLENCH / BLINK / DOUBLE_BLINK / LONG_BLINK as they fire. |
| **Flappy** | Flappy Bird, flapped on the rising edge of a clench. The latency instrument: if this feels fair, clench-as-a-button works. |
| **Drill** | The reliability instrument. See below. |
| **Stop** | Ends whatever is running. Keeps the connection. |

Because only one program may hold a Muse, **the scripts below cannot run while
the station is connected.** Press Disconnect first, or just do the thing in the
station. The station is not a replacement for them yet — recording, band powers,
the plots and the games are still terminal scripts.

Self-test, no headband needed:

```powershell
python test_station.py    # ~90 s, drives the window end to end on synthetic data
```

### Two inputs, and how we know they work

The board needs at least two inputs that can be told apart. Clench is the first.
The second is a **long blink** — eyes deliberately held shut, default 400 ms.

Why not a double blink: it has a latency floor you cannot engineer away. A
DOUBLE_BLINK may not fire until the 700 ms pairing window proves a second blink
arrived, and a *single* blink is only confirmed once that same window expires. So
every blink-based decision costs ~700 ms before the system even knows what you
did. A held blink fires the instant the hold passes threshold, exactly as
LONG_CLENCH fires at its 1500 ms mark, which makes the latency a number we choose.

Separation is structural rather than statistical:

| Input | What it is | Electrodes | Band | Measured as |
|---|---|---|---|---|
| CLENCH | masseter muscle (EMG) | TP9 / TP10 (ears) | 20–110 Hz | RMS over 200 ms |
| LONG_BLINK | eyelid held down (EOG step) | AF7 / AF8 (forehead) | 0.5–8 Hz | mean of a 250 ms tail, held past `--long-blink-ms` |

Different electrodes *and* different bands, so neither can easily masquerade as
the other. The subtle part: an ordinary blink's hold level (~66 µV simulated) is
about as high as a held one's (~56–83 µV), so **level cannot separate them —
only duration can.** That is why the measure feeds a duration detector instead of
a bare threshold, and why an ordinary blink never becomes a LONG_BLINK.

One consequence: because the hold level rises on ordinary blinks too, the BLINK
verdict now waits for the lid to come back up. Calling something a short blink
while the eyes are still shut would be wrong, and it also raced LONG_BLINK.

**The drill** answers the two questions a log cannot. Over 20 prompted rounds it
reports, per input: hits, how often the *other* input fired instead, how often
nothing fired, and median and worst response times — plus how often an input
fired when nothing was asked for, which is the number that decides whether a
gesture is safe to leave switched on. Involuntary BLINKs are counted separately,
because people blink and that is not a failure.

Read the latency honestly: the drill measures *your* reaction plus the system's
and cannot separate them. What is ours is the **gap between the two inputs** — a
clench fires on its rising edge, a long blink cannot fire until the hold
completes, so expect roughly the hold length between the two columns. If the
clench column is much slower than your own reaction time, that is real lag.

Profiles calibrated before the long blink existed keep working; they just cannot
fire LONG_BLINK until recalibrated, and both the log and the drill say so.

---

## Run order

```powershell
python check_connection.py     # 1. does data arrive at all?
python live_bands.py           # 2. live delta/theta/alpha/beta/gamma + mindfulness
python record.py --seconds 60  # 3. save a session to recordings/*.csv
python live_plot.py            # 4. (optional) scope view of the 4 EEG channels
python clench_detect.py        # 5. calibrate, then emit CLENCH / BLINK input events
python clench_game.py          # 6. play the scan-and-clench game
python clench_flappy.py --load # 7. fly Flappy Bird with your jaw
```

Add `--synthetic` to any of them for fake data with no hardware:

```powershell
python check_connection.py --synthetic
python live_bands.py --synthetic
python record.py --synthetic --seconds 10 --label smoke
python live_plot.py --synthetic
python clench_detect.py --synthetic --no-clench-cal
python clench_game.py --synthetic --no-clench-cal --rounds 3
python clench_flappy.py --bot          # autopilot, no headband
python clench_flappy.py --keyboard     # Space to flap
```

Or set it once for the whole shell:

```powershell
$env:USE_SYNTHETIC = "1"     # PowerShell
export USE_SYNTHETIC=1        # bash/zsh
```

### Flags shared by every script

| Flag | Meaning |
|---|---|
| `--synthetic` | Use `BoardIds.SYNTHETIC_BOARD`. Same as `USE_SYNTHETIC=1`. |
| `--name Muse-XXXX` | Connect to a specific headband by BLE name. Use this if discovery is slow or there are several Muses in the room. |
| `--mac AA:BB:CC:DD:EE:FF` | Connect by MAC address. |
| `--debug` | Turn on BrainFlow's dev logger — verbose native BLE output, useful when a connection fails. |

Script-specific: `--seconds`, `--label` (record), `--window`, `--interval`
(live_bands), `--window`, `--save FILE.png` (live_plot).

---

## What each script does

**`muse_station.py`** — the window described above. Owns one `BoardShim` and runs
calibration and detection on it in a worker thread, so a session survives across
many activities. Three threads, one rule: only the Tk thread touches a widget;
the link and work threads post messages onto a queue. `test_station.py` drives it
end to end on the synthetic board.

**`station_activities.py`** — the two windows the station can open on a live
connection: `FlappyWindow` (feel the latency) and `DrillWindow` (measure the
reliability). Both are Toplevels driven by `after()` on the Tk thread and never
touch the board — the station's work thread posts gestures onto the queue, so a
plain event name arrives on the right thread. Flappy reuses `clench_flappy.Flappy`
unchanged, which is possible only because that class is pure state with no
matplotlib in it.

**`config.py`** — shared setup. `get_board(args)` returns a `BoardShim` for either
the Muse or the synthetic board; `available_presets()`, `enable_ppg()` and
`eeg_channels_and_names()` hide the differences between the two. Run it directly as
an environment check.

**`check_connection.py`** — connects, streams 5 s, prints samples per preset, the
EEG channel names, the sampling rate, and mean/std in µV per channel, with a clear
**PASS**/**FAIL** and troubleshooting hints on failure.

**`live_bands.py`** — every 0.5 s, runs `DataFilter.get_avg_band_powers` on the last
2 s and prints the five band powers plus a mindfulness score from BrainFlow's
`MLModel` (`BrainFlowMetrics.MINDFULNESS`, `BrainFlowClassifiers.DEFAULT_CLASSIFIER`).

**`record.py`** — records N seconds (default 60) and writes one timestamped CSV per
preset into `recordings/` with `DataFilter.write_file`. Read them back with
`DataFilter.read_file(path)`.

**`record_protocol.py`** — the same CSVs, but *labelled*. It cues a fixed protocol
out loud (rest, quick clenches, 1 s clenches, 3 s clenches, blinks, talking,
chewing) with a beep and a countdown per rep, then writes the timestamps it cued
beside the CSVs so the recording can be replayed in tests. See "Recording a
labelled session" below.

**`live_plot.py`** — matplotlib scope of the four EEG channels, bandpassed 1–40 Hz
with 50/60 Hz notch. `--save frame.png` grabs a single frame headlessly.

**`clench_detect.py`** — the input layer. Calibrates to you, then prints `CLENCH`,
`LONG_CLENCH`, `BLINK` and `DOUBLE_BLINK` as you make them, with a live meter so you
can see how close to the threshold you are. See "Using it as input" below.

**`clench_game.py`** — a cursor scans a row of cells, you clench on the target.
Scores hits, misses and timeouts, and reports your reaction time so you can pick a
scan speed. Same interaction a scanning communication board uses.

**`clench_flappy.py`** — Flappy Bird in a matplotlib window, flown by clenching.
Slow gravity, wide gaps, unhurried pipes. `--bot` flies itself, `--keyboard` uses
Space, so both work with no headband.

**`test_gestures.py`** — feeds made-up envelope traces through the gesture logic and
asserts the right events come out. Run it after changing any threshold or timing;
it needs no headband and finishes instantly.

### Presets (what data lives where)

| Preset | Contents | Rate | Notes |
|---|---|---|---|
| `DEFAULT_PRESET` | EEG: **TP9, AF7, AF8, TP10** | 256 Hz | Rows from `BoardShim.get_eeg_channels(board_id)` |
| `AUXILIARY_PRESET` | Accelerometer + gyroscope | 52 Hz | On by default |
| `ANCILLARY_PRESET` | PPG (heart rate) | 64 Hz | Off until `board.config_board("p50")` after `prepare_session()`; also turns on a 5th EEG channel |

All BrainFlow arrays are **channels × samples**.

---

## Sensor fit

The single biggest factor in whether any of this looks like brain activity:

- **Slightly wet the two forehead sensors** (AF7/AF8) — a damp fingertip or a dab
  of water. Dry skin is the #1 cause of flat or noisy signals.
- **Push hair out of the way behind the ears** so TP9/TP10 touch skin directly.
- **Sit still**, jaw relaxed, and keep the charging cable unplugged — mains hum and
  muscle tension both swamp real EEG.
- `check_connection.py` flags each channel as `ok`, `FLAT?` (no contact) or
  `NOISY?` (loose electrode / mains pickup). Re-seat the band until all four say `ok`.

---

## Demo ideas

| Demo | What to do | What you should see |
|---|---|---|
| **Alpha waves** | Run `live_bands.py`, close your eyes and relax for ~15 s, then open them | `alpha` rises noticeably with eyes closed, drops when you open them |
| **Blinks** | Run `live_plot.py` and blink hard | Big slow spikes on AF7/AF8 (the forehead channels) |
| **Jaw clench** | Run `live_plot.py` and clench your teeth for a second | A burst of fast high-amplitude noise on all four channels — this is muscle (EMG), not EEG, and it's the easiest signal on the whole headband to detect |
| **Head tilt** | `record.py --seconds 20 --label tilt`, tilting your head left/right | The accelerometer columns in `*_auxiliary.csv` swing with the tilt |
| **Heart rate** | `record.py --seconds 30`, sitting still | `*_ancillary.csv` holds the PPG trace; its periodicity is your pulse (~1 Hz) |

---

## Recording a labelled session

Detection can only be tuned offline against signal whose true events are known.
`record_protocol.py` cues the actions for you, so the labels come out of the run
itself instead of your memory of it:

```powershell
.\test\.venv\Scripts\python.exe test/record_protocol.py --dry-run --speed 6   # practise the cues
.\test\.venv\Scripts\python.exe test/record_protocol.py --profile taher       # the real ~3 min run
```

Calibrate that profile first and don't move the band afterwards: the recording is
only comparable to the thresholds it was made under.

| Phase | Length | What you do |
|---|---|---|
| rest | 30 s | still, eyes open, jaw loose (this becomes the baseline window) |
| quick clenches | 5 x 0.4 s | one comfortable bite per beep |
| 1 s clenches | 5 x 1 s | hold until the bar empties |
| 3 s clenches | 2 x 3 s | the call-for-help hold |
| blinks | 5 | one deliberate blink per beep |
| talking | 20 s | read out loud — a false-trigger source, not a gesture |
| chewing / yawning | 20 s | same, the worst realistic jaw artifact |

High beep = go, low beep = release. Three keys while it runs:

| Key | Meaning |
|---|---|
| `X` | the rep just cued did **not** happen — drops that label, so a missed rep is not scored as a detection miss |
| `M` | mark this moment (a twitch, a swallow, the band slipping) |
| `Q` | stop early but keep everything recorded so far |

Each run leaves four or five files in `recordings/` sharing one prefix:
`*_default.csv` (EEG, the only one the detector reads), `*_auxiliary.csv`,
`*_ancillary.csv`, `*_labels.json` (every phase, rep, dropped rep and marker, in
seconds from the **first EEG sample**) and `*_manifest.json`, ready for the
evaluator, with the matching calibration copied in beside it:

```powershell
.\test\.venv\Scripts\python.exe test/evaluate_detection.py --manifest test/recordings/<stamp>_<label>_manifest.json
```

The labels are what you were *asked* to do. They are corrected only where you
pressed `X`, so before believing a miss, look at the waveform for that window.
Send the whole prefix as one set; the CSVs are git-ignored and a 3 min EEG file is
a few MB, so zip it.

Edit the `PROTOCOL` list at the top of the script to change or extend the run —
double blinks and eye holds are not in it yet, and the cues, labels and manifest
all follow from that list.

## Using it as input

`clench_detect.py` is where this stops being a signal viewer and starts being a
controller. Two different physiological signals, deliberately kept apart:

| Gesture | What it really is | Where it shows up | Band | Measured as |
|---|---|---|---|---|
| Jaw clench | **EMG** — masseter muscle firing | TP9 / TP10 (ears) | 20–110 Hz | RMS over 200 ms |
| Blink | **EOG** — eyelid movement artifact | AF7 / AF8 (forehead) | 1–10 Hz | peak-to-peak over 300 ms, **both channels** |

Neither is brainwaves, and that is exactly why they are good input: they are huge,
fast, and voluntary, where real EEG intent is small, slow and unreliable.

**Why the two are measured differently.** A clench is a sustained buzz, so "how
loud on average, just now" (RMS) is the right question. A blink is one quick
swoop, and averaging over a window mixes the swoop with the quiet either side of
it, which makes blinks look smaller than they are. Peak-to-peak measures the
event itself.

**Why a blink needs both channels.** AF7 and AF8 sit above one eye each. A real
blink moves both eyelids, so both channels spike within a few tens of
milliseconds. A loose electrode or a stray movement usually hits one channel, or
hits both but far apart in time. Demanding agreement discards most false blinks
for free. The detector ticks at 20 Hz, so the 60 ms coincidence window means
"same tick, or one tick apart".

**Mains hum.** The 50/60 Hz notch runs *before* the 20–110 Hz bandpass, not
after — the hum sits inside the EMG band, so notching afterwards leaves it in the
number being measured. A venue has far more electrical hum than a bedroom, so
**recalibrate on site** before it matters.

### Profiles: one headband, several people

Every script takes `--profile NAME`. Each person gets their own
`calibration.<name>.json`, so calibrating for someone else never touches yours:

```powershell
python clench_detect.py --profile taher            # calibrate and save as taher
python clench_flappy.py --profile taher --load     # play as taher
python clench_flappy.py --profile alex             # calibrate alex, saves separately
```

Asking for a profile that does not exist tells you which ones do, rather than
loading the wrong thresholds silently. Synthetic runs write
`calibration.<name>.synthetic.json`, and a calibration recorded on one board is
refused on the other.

### Calibrate every time you put the band on

Microvolt levels depend on skin moisture and how the ear-tips are seated, so a
threshold from yesterday is meaningless today. Calibration takes about 30 seconds:

```powershell
python clench_detect.py
```

1. **Rest** (10 s) — sit still, jaw relaxed and slightly open, **stare at one
   fixed spot and do not talk**. Eye movement lands on the forehead sensors and
   would inflate the blink noise floor. Measures your noise floor.
2. **Clench** (3 × 2 s) — clench hard on each prompt. Measures your ceiling.
3. **Blink** (6 s) — separate, deliberate blinks about once a second, not
   fluttering. Each blink is detected and sized individually.

The blink threshold lands halfway between the resting noise (`rest + 4σ`) and
your **smallest** blink of the session — under your weakest one, so none get
missed, but clear of the noise. The ratio between those two is printed as
**separation**: 3x or better is good, 2x is usable, below 2x means fix forehead
contact or fall back to double-clench for BACK.

Calibration also warns when a forehead channel is noisy at rest, which is the
single most common cause of unreliable blinks.

The threshold lands 30% of the way from rest up to your real peak, which is far
more reliable than a fixed multiple of the noise. It is saved to `calibration.json`;
reuse it with `--load` as long as the band has not moved.

### What good numbers look like

The `--- THRESHOLDS ---` block prints your rest level, the firing threshold, and
your measured peak with a **headroom** figure. Headroom is peak ÷ threshold:

- **3x or more** — excellent, clench detection will feel instant and never misfire.
- **1.5x–3x** — usable.
- **under 1.5x** — re-seat the ear-tips and recalibrate. The script warns you.

### Tuning

| Flag | Use it when |
|---|---|
| `--long-ms 1500` | LONG_CLENCH (the help signal) fires too fast or too slow |
| `--double-ms 700` | Your two blinks are not being caught as one DOUBLE_BLINK — raise it |
| `--k 6` | Only affects the fallback threshold when you skip active calibration. Raise it if resting noise causes false CLENCHes |
| `--no-clench-cal` | Quick start with a purely statistical threshold, no prompts. Also skips the long-blink phase, so LONG_BLINK stays off |
| `--long-blink-ms 400` | How long the eyes must stay shut for LONG_BLINK. Lower it if your holds are not registering, raise it if ordinary blinks are |

If you get false CLENCHes while sitting still, the cause is almost always a loose
ear-tip rather than a bad threshold — check `live_plot.py` first.

## The clench game

Once calibration looks good, this is the fastest way to find out whether clench
really works as a button for you:

```powershell
python clench_game.py --load
```

A cursor sweeps left to right; `[*]` means it is sitting on the target. Clench
there. Ten rounds, then a scorecard.

```
   .  * [ ] .  .  .     [########-|----]   24.3 uV
  round  3/10   HIT   reaction  420 ms
```

**Reading the scorecard**

| Result | What it means | What to change |
|---|---|---|
| Timeouts | The threshold is too high — your clench never crossed it | Clench harder, or recalibrate without `--load` |
| Misses | You clenched late, on the cell after the target | Slow the scan: `--scan-ms 1200` |
| Hits, reaction near the dwell time | Working, but rushed | Slow the scan a little |
| Hits, reaction well under the dwell | Comfortable | Speed up: `--scan-ms 700` |

Reaction time includes the detector's own lag — about one 200 ms envelope window —
so it is the honest end-to-end number, not just your reflexes.

Options: `--rounds`, `--cells`, `--scan-ms`, `--max-sweeps`.

## Latency: rising edge vs release

Worth knowing if you build on this. `CLENCH` fires on the **release** edge — it has
to, because the duration is what separates a clench from a LONG_CLENCH. But that
means the event lands after your whole clench is over, plus the ~200 ms envelope
window. In the scanning game that showed up as missing by exactly one cell, every
single round.

For anything interactive, pass `emit_start=True` to `GestureRecognizer` and act on
`CLENCH_START`, which fires the instant the envelope crosses the threshold. That is
what `clench_flappy.py` does, and it cuts the lag to roughly the envelope window
alone. Use release-edge `CLENCH` when you need to tell short from long; use
`CLENCH_START` when you need it to feel immediate.

## Troubleshooting a real Muse

1. **Power-cycle it.** Hold the button until it turns off, wait 3 s, turn it back on.
   A slowly breathing white LED means it's advertising; solid means already connected
   to something else.
2. **Close every other app** that could hold it (phone app, `muselsl`, another shell).
3. **Name it:** `python check_connection.py --name Muse-XXXX`.
4. **Check Bluetooth permission / radio** — see the platform table above.
5. **Sit close** to the laptop for the first connection.
6. **Prove the code is fine** with `--synthetic`, then re-run the real test with
   `--debug` to see the native BLE log.
