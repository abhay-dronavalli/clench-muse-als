# sensor/

Sensor Service (Python). Its only job is to turn signals into events (PRD A3.1).

- `sources/` synthetic, replay and Muse 2 sources behind one interface (BrainFlow, added in a later chunk).
- `detect/` clench, blink, body state and calibration.
- `main.py` (later chunk) picks a source from `SOURCE` in `.env` and sends events to the Core.

The keyboard stand-in is not here: it lives in the web app as a dev panel (`web/src/dev/`).
