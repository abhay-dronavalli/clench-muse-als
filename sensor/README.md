# sensor/

Sensor Service (Python). Its only job is to turn signals into events (PRD A3.1).

- `sources/` synthetic and Muse 2 sources behind one interface (BrainFlow).
- `detect/` clench and blink preprocessing and detection (`clench.py`, shared with the
  Tkinter bench in `test/clench_detect.py`), mapped to CLENCH / LONG_CLENCH / DOUBLE_BLINK
  in `input.py`.
- `main.py` runs a headless WebSocket bridge to Core.
- `profiles.py` loads saved per-user calibration profiles.

Usually you start it from the web app: the Muse panel's **Connect headband** button
(board bottom bar or `/console`) runs it for you. By hand, from the repository root after
starting Core:

```powershell
.\.venv\Scripts\python.exe -m sensor.main --profile taher --url ws://127.0.0.1:8001/ws/sensor
```

Use `--source demo` for a headband-free smoke test (it cycles three clenches, a long
clench and a double blink every 30 s). `--blink auto|on|off` controls DOUBLE_BLINK; `auto`
follows the profile's eye calibration. Press `/` on the board to see every gesture the
Core received, including the ones it ignored and why. The real Muse source requires
the Muse 2 to be disconnected from Muse Station and available over Bluetooth.

The keyboard stand-in is not here: it lives in the web app as a dev panel (`web/src/dev/`).
