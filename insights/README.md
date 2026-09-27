# Clench Insights (Tiger Data)

Tracks how a Clench user's clench control changes over time, so a caregiver sees a decline early.
A read-only logger records what the core already tells its caregiver console. TimescaleDB on
Tiger Data stores it in hypertables, and continuous aggregates roll it up hourly and daily. A small
page on port 8300 charts the trends and shows a suggestion card when the clench signal is weakening.

Everything lives in `insights/`. The core, web app and sensor don't import it and don't depend on
it. Clench runs the same with the logger stopped, crashed, or its database down.

## What it stores (and what it never stores)

| Table | One row per | Kept |
|---|---|---|
| `signal` | SIGNAL sample (about 4 Hz) | channel standard deviations, jaw EMG, threshold, EMG/threshold, contact, blocked flag |
| `gestures` | INPUT_EVENT | kind, source (headband or keyboard), accepted, refusal reason code, strength, duration, peak margin |
| `messages` | METRICS (a confirmed message) | clenches, scan steps, the Day 1 comparison, wait and compose seconds |
| `body_state` | STATE | level, bpm, motion, eyes closed |

Every table has `simulated` (true only for seeded rows).

**Privacy.** Counts and timings only. The sentence in METRICS, the sensor's profile name, contact
names and free-text refusal reasons are never written. Reasons become one of `paused`, `no_board`,
`disconnected`, `blocked`, `stale`, `clock_skew`, `other`. `tests/test_mapping.py` checks this.

**Body state:** the core does not relay STATE to `/ws/console` today, so live `body_state` rows
appear only if the core starts relaying it. The simulated history fills it.

## Setup

1. Create a Tiger Data service (TimescaleDB 2.20 or newer), copy its connection string, and add
   it to the repo's `.env`. It is never printed or logged.

   ```
   TIGER_DATABASE_URL=postgres://tsdbadmin:...@....tsdb.cloud.timescale.com:.../tsdb?sslmode=require
   ```

2. Dependencies are separate from the core's. `uv` installs them on the fly:

   ```powershell
   uv run --no-project --with-requirements insights/requirements.txt python -m insights.db apply
   ```

   This creates the hypertables, continuous aggregates, refresh policies and the columnstore
   (compression) policy. It is safe to run again.

## Run (PowerShell, repo root)

```powershell
# 4 weeks of simulated history (fixed seed; about 1.2M signal rows; 1 to 3 minutes to Tiger)
uv run --no-project --with-requirements insights/requirements.txt python -m insights.seed
uv run --no-project --with-requirements insights/requirements.txt python -m insights.seed --reset --yes   # replace it

# Logger: reads ws://127.0.0.1:8000/ws/console (the running core) and writes to Tiger
uv run --no-project --with-requirements insights/requirements.txt python -m insights.logger
uv run --no-project --with-requirements insights/requirements.txt python -m insights.logger --print     # no database: print rows
uv run --no-project --with-requirements insights/requirements.txt python -m insights.logger --url ws://127.0.0.1:8001/ws/console

# Page: http://127.0.0.1:8300
uv run --no-project --with-requirements insights/requirements.txt python -m insights.app

# Row counts and the measured compression ratio
uv run --no-project --with-requirements insights/requirements.txt python -m insights.db stats
```

## Tests

```powershell
uv run --no-project --with-requirements insights/requirements.txt python -m pytest insights/tests
```

The mapping, buffering, reconnect and decline-rule tests always run. The aggregate tests need a
database: they use `INSIGHTS_TEST_DATABASE_URL` or else `TIGER_DATABASE_URL`. Each test creates a
throwaway schema (`insights_test_<random>`), loads fixtures, refreshes the aggregates, checks the
numbers and drops the schema. Without a database they are skipped. A local database works too:

```powershell
docker run -d --name clench-insights-tsdb -p 55432:5432 -e POSTGRES_PASSWORD=local timescale/timescaledb:latest-pg17
$env:INSIGHTS_TEST_DATABASE_URL = 'postgresql://postgres:local@127.0.0.1:55432/postgres'
```

The root `uv run pytest` does not collect these tests (its `testpaths` is `tests`).

## Settings (environment or `.env`)

| Variable | Default | What |
|---|---|---|
| `TIGER_DATABASE_URL` | none | the Tiger Data service |
| `INSIGHTS_CORE_WS` | `ws://127.0.0.1:${CORE_PORT:-8000}/ws/console` | the core's console socket |
| `INSIGHTS_PORT` | `8300` | the page |
| `INSIGHTS_DECLINE_DROP` | `0.15` | the card shows when the median margin fell by more than this (in threshold units) |
| `INSIGHTS_DECLINE_WINDOW_DAYS` | `14` | window the decline is measured over |
| `INSIGHTS_DECLINE_MIN_DAYS` | `7` | days with data needed before the rule says anything |
| `INSIGHTS_TEST_DATABASE_URL` | none | database for the aggregate tests |

## How it works

- **Logger** (`logger.py`, `mapping.py`). A console client that never sends. Rows go into a
  bounded buffer (5,000 rows or 60 s). A writer thread inserts them once a second. If the database
  is down, the buffer holds rows and then drops the oldest with one log line every 30 s. It
  reconnects to the core with a backoff capped at 10 s. Peak margin: for each headband CLENCH, the
  highest EMG/threshold in the SIGNAL samples of the 1.5 s before it (the sensor sends CLENCH on
  release). Compose seconds: from the first accepted clench to the METRICS that confirms the
  message (left empty past 10 minutes).
- **Aggregates** (`schema.sql`). `gestures_hourly` and `gestures_daily` hold exact medians
  (`percentile_cont`) of strength and peak margin, and refusal counts by reason. Both come from the
  raw table, because an exact median cannot be rolled up. `messages_daily` and `signal_daily` are
  hierarchical: built on their hourly views, since sums and counts roll up exactly. Daily buckets
  use America/New_York midnight. Real-time aggregation is on, so today shows before a refresh. Every
  view has a refresh policy.
- **Compression.** The signal table is in the columnstore (segment by `simulated`, order by
  `time DESC`). `add_columnstore_policy` converts chunks older than a day. The seed converts its
  chunks right after loading, and `db stats` reports `hypertable_columnstore_stats`.
- **Page** (`app.py`, `queries.py`, `static/`). Every query reads a continuous aggregate. The
  ability charts use headband gestures only, because the keyboard stand-in always reports strength
  1.0. Simulated days are a gray line under a "Simulated 4 weeks" caption. Live data is blue points
  in a shaded "Live today" column. Every chart has a table view.
- **Suggestion** (`rules.py`). Over the last 14 days with data, compare the median of the first 3
  daily median margins with the median of the last 3. If the drop is more than
  `INSIGHTS_DECLINE_DROP`, show "Clench signal is weakening. Consider recalibrating or switching to
  eyebrow raises." It is only a suggestion; nothing in `insights/` can change a Clench setting.

Decisions and caveats: `DECISIONS.md`.
