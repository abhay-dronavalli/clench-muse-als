# Insights decisions

Kept here instead of `docs/decisions.md` because this chunk may not edit shared files. Fold these
into `docs/decisions.md` when merging if you want one log.

1. **Read-only from `/ws/console`.** The console receives INPUT_EVENT, SIGNAL and METRICS
   (`core/hub.py`). The logger has no send call. The websocket library still answers
   protocol-level pings, which every client must do.
2. **STATE is not available there.** The core consumes STATE and never relays it to consoles, so
   the logger cannot record body state without a core change (out of scope). The table and the
   handler exist; the seed fills the simulated history.
3. **Margin means the peak at a clench,** not the resting signal. It is the highest emg/threshold in
   the SIGNAL samples of the 1.5 s before each headband CLENCH, stored on the gesture row. SIGNAL is
   4 Hz, so the true peak can be up to about 250 ms between samples and slightly higher than stored.
4. **Exact medians.** `percentile_cont` in the continuous aggregates, rather than toolkit sketches.
   Gesture volume is tiny, results are exact, and the tests assert exact values. The cost is that
   the daily gesture view reads the raw table rather than rolling up the hourly one.
5. **Keyboard stand-in excluded from ability charts.** The dev panel sends `strength: 1.0` for every
   clench and is never refused. It is stored (`source = 'dev'`) and counted on the page, but not
   charted.
6. **Strength and margin are relative to the calibration.** Recalibrating resets both and can hide
   a real decline, and recalibrating is what the card suggests. The page marks a day as
   "recalibrated" when the average threshold moves more than 5% from the day before.
7. **Refusal reasons are codes.** The core's fixed strings map to codes. The sensor's own `blocked`
   text is free-form and could name a person, so it becomes `blocked` and the text is dropped.
   Anything unknown is `other`.
8. **Compose seconds are approximate.** They run from the first accepted CLENCH after the previous
   message to the METRICS. They are left empty past 10 minutes (probably abandoned and resumed).
   `wait_s` follows the contract exactly: `scan_steps * scan_ms / 1000`, with `scan_ms` from the
   last SETTINGS.
9. **Daily buckets are America/New_York.** A continuous aggregate's definition cannot take a
   parameter, so the zone is fixed in `schema.sql` and `config.LOCAL_TZ`. Change both together.
10. **Decline rule:** median of the first 3 vs. the last 3 daily median margins in a 14-day window.
    It needs at least 7 days with data, and the default drop is 0.15x threshold. When a day has
    both simulated and live data, the live value wins. The card says when simulated days were
    involved.
11. **Deleting compressed simulated rows.** `seed --reset` lifts
    `timescaledb.max_tuples_decompressed_per_dml_transaction` for its own session only; the default
    100k limit refuses the delete. It re-converts with `recompress => true`.
12. **Manual refresh can race a policy.** Refresh policies start running as soon as the schema is
    applied. A manual `refresh_continuous_aggregate` over the same view then fails with
    `LockNotAvailable`, so `db.refresh_all` waits and retries.
13. **Simulated history is modest and labeled.** Over 28 days, strength goes 0.72 to 0.55 and
    margin 1.95x to 1.35x, and refusals rise from 4% to 12%. The seed is fixed. It illustrates the
    feature and is not a claim about any patient.
