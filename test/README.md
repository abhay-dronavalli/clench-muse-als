# Muse 2 + BrainFlow test bench

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

## Run order

```powershell
python check_connection.py     # 1. does data arrive at all?
python live_bands.py           # 2. live delta/theta/alpha/beta/gamma + mindfulness
python record.py --seconds 60  # 3. save a session to recordings/*.csv
python live_plot.py            # 4. (optional) scope view of the 4 EEG channels
```

Add `--synthetic` to any of them for fake data with no hardware:

```powershell
python check_connection.py --synthetic
python live_bands.py --synthetic
python record.py --synthetic --seconds 10 --label smoke
python live_plot.py --synthetic
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

**`live_plot.py`** — matplotlib scope of the four EEG channels, bandpassed 1–40 Hz
with 50/60 Hz notch. `--save frame.png` grabs a single frame headlessly.

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
