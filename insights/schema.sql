-- Clench Insights schema for Tiger Data (TimescaleDB 2.20 or newer).
--
-- Apply:  uv run --no-project --with-requirements insights/requirements.txt python -m insights.db apply
-- (psql -f also works: every statement stands alone and none needs a transaction block.)
-- Idempotent: safe to apply again.
--
-- PRIVACY. These tables hold counts and timings only. They never hold what the person said (METRICS
-- `text`), contact names, the sensor profile name, or free-text reasons. Refusal reasons are stored
-- as one of a fixed set of codes (see insights/mapping.py).
--
-- Every table has `simulated`: true for the seeded illustration history (insights/seed.py), false
-- for what the logger recorded from a real core. Every aggregate groups by it, so the page never
-- mixes the two.

CREATE EXTENSION IF NOT EXISTS timescaledb;

-- ---------------------------------------------------------------------------------------------
-- Hypertables
-- ---------------------------------------------------------------------------------------------

-- SIGNAL telemetry, about 4 rows a second while the headband is on: the high-volume table and the
-- only one kept in the columnstore (compressed).
CREATE TABLE IF NOT EXISTS signal (
    time        timestamptz NOT NULL,
    simulated   boolean     NOT NULL DEFAULT false,
    ch          real[],              -- per-channel standard deviations (TP9, AF7, AF8, TP10); never raw waveforms
    emg         real,                -- jaw EMG level, microvolts
    threshold   real,                -- the calibrated clench threshold at that moment, microvolts
    margin      real,                -- emg / threshold
    connected   boolean,             -- headband contact; null when the sender did not say
    blocked     boolean              -- the sensor was refusing commands (reason text not stored)
) WITH (
    tsdb.hypertable,
    tsdb.partition_column = 'time',
    tsdb.chunk_interval = '1 day',
    tsdb.segmentby = 'simulated',
    tsdb.orderby = 'time DESC'
);

-- One row per INPUT_EVENT: every gesture the core received, accepted or refused.
CREATE TABLE IF NOT EXISTS gestures (
    time        timestamptz NOT NULL,
    simulated   boolean     NOT NULL DEFAULT false,
    kind        text        NOT NULL CHECK (kind IN ('CLENCH', 'LONG_CLENCH', 'DOUBLE_BLINK')),
    source      text        NOT NULL CHECK (source IN ('muse', 'dev')),
    accepted    boolean     NOT NULL,
    reason      text        CHECK (reason IN ('paused', 'no_board', 'disconnected', 'blocked',
                                              'stale', 'clock_skew', 'other')),
    strength    real,                -- CLENCH only, 0..1 relative to the calibration
    duration    real,                -- LONG_CLENCH only, seconds held
    peak_margin real                 -- CLENCH from the headband only: peak emg / threshold just before it
) WITH (
    tsdb.hypertable,
    tsdb.partition_column = 'time',
    tsdb.chunk_interval = '7 days',
    tsdb.columnstore = false
);

-- One row per METRICS (a confirmed message). The sentence itself is dropped by the logger.
CREATE TABLE IF NOT EXISTS messages (
    time             timestamptz NOT NULL,
    simulated        boolean     NOT NULL DEFAULT false,
    selections       integer     NOT NULL,   -- clenches since home, including the confirm
    scan_steps       integer     NOT NULL,
    day1_selections  integer     NOT NULL,   -- the same message in Day 1 mode
    day1_scan_steps  integer     NOT NULL,
    scan_ms          integer,                -- from the last SETTINGS; null if none seen yet
    wait_s           real,                   -- scan_steps * scan_ms / 1000 (the contract's definition)
    day1_wait_s      real,
    compose_s        real                    -- wall clock from the first accepted clench to the confirm; approximate
) WITH (
    tsdb.hypertable,
    tsdb.partition_column = 'time',
    tsdb.chunk_interval = '7 days',
    tsdb.columnstore = false
);

-- STATE. The core does not relay STATE to /ws/console today, so live rows only appear if it starts
-- to; the seed fills the simulated history.
CREATE TABLE IF NOT EXISTS body_state (
    time        timestamptz NOT NULL,
    simulated   boolean     NOT NULL DEFAULT false,
    level       text        NOT NULL CHECK (level IN ('calm', 'normal', 'elevated')),
    bpm         real,
    motion      real,
    eyes_closed boolean
) WITH (
    tsdb.hypertable,
    tsdb.partition_column = 'time',
    tsdb.chunk_interval = '7 days',
    tsdb.columnstore = false
);

-- ---------------------------------------------------------------------------------------------
-- Continuous aggregates
--
-- Medians use exact percentile_cont, so the hourly and daily gesture views are both built from the
-- raw table (an exact median cannot be rolled up). Counts and sums roll up exactly, so the daily
-- message and signal views are built on their hourly views (hierarchical aggregates).
-- Real-time aggregation is on (materialized_only = false) so "Live today" shows without waiting
-- for a refresh.
-- ---------------------------------------------------------------------------------------------

CREATE MATERIALIZED VIEW IF NOT EXISTS gestures_hourly
WITH (timescaledb.continuous, timescaledb.materialized_only = false) AS
SELECT
    time_bucket(INTERVAL '1 hour', time)                                AS bucket,
    simulated,
    source,
    count(*)                                                            AS gestures,
    count(*) FILTER (WHERE kind = 'CLENCH')                             AS clenches,
    count(*) FILTER (WHERE NOT accepted)                                AS refused,
    count(*) FILTER (WHERE reason = 'paused')                           AS refused_paused,
    count(*) FILTER (WHERE reason = 'no_board')                         AS refused_no_board,
    count(*) FILTER (WHERE reason = 'disconnected')                     AS refused_disconnected,
    count(*) FILTER (WHERE reason = 'blocked')                          AS refused_blocked,
    count(*) FILTER (WHERE reason = 'stale')                            AS refused_stale,
    count(*) FILTER (WHERE reason = 'clock_skew')                       AS refused_clock_skew,
    count(*) FILTER (WHERE reason = 'other')                            AS refused_other,
    percentile_cont(0.5) WITHIN GROUP (ORDER BY strength::float8)
        FILTER (WHERE kind = 'CLENCH')                                  AS median_strength,
    percentile_cont(0.5) WITHIN GROUP (ORDER BY peak_margin::float8)
        FILTER (WHERE kind = 'CLENCH')                                  AS median_margin,
    count(peak_margin)                                                  AS margin_samples
FROM gestures
GROUP BY 1, 2, 3
WITH NO DATA;

CREATE MATERIALIZED VIEW IF NOT EXISTS gestures_daily
WITH (timescaledb.continuous, timescaledb.materialized_only = false) AS
SELECT
    time_bucket(INTERVAL '1 day', time, 'America/New_York')             AS bucket,
    simulated,
    source,
    count(*)                                                            AS gestures,
    count(*) FILTER (WHERE kind = 'CLENCH')                             AS clenches,
    count(*) FILTER (WHERE NOT accepted)                                AS refused,
    count(*) FILTER (WHERE reason = 'paused')                           AS refused_paused,
    count(*) FILTER (WHERE reason = 'no_board')                         AS refused_no_board,
    count(*) FILTER (WHERE reason = 'disconnected')                     AS refused_disconnected,
    count(*) FILTER (WHERE reason = 'blocked')                          AS refused_blocked,
    count(*) FILTER (WHERE reason = 'stale')                            AS refused_stale,
    count(*) FILTER (WHERE reason = 'clock_skew')                       AS refused_clock_skew,
    count(*) FILTER (WHERE reason = 'other')                            AS refused_other,
    percentile_cont(0.5) WITHIN GROUP (ORDER BY strength::float8)
        FILTER (WHERE kind = 'CLENCH')                                  AS median_strength,
    percentile_cont(0.5) WITHIN GROUP (ORDER BY peak_margin::float8)
        FILTER (WHERE kind = 'CLENCH')                                  AS median_margin,
    count(peak_margin)                                                  AS margin_samples
FROM gestures
GROUP BY 1, 2, 3
WITH NO DATA;

CREATE MATERIALIZED VIEW IF NOT EXISTS messages_hourly
WITH (timescaledb.continuous, timescaledb.materialized_only = false) AS
SELECT
    time_bucket(INTERVAL '1 hour', time)    AS bucket,
    simulated,
    count(*)                                AS messages,
    sum(selections)                         AS clenches,
    sum(scan_steps)                         AS scan_steps,
    sum(day1_selections)                    AS day1_clenches,
    sum(day1_scan_steps)                    AS day1_scan_steps,
    sum(wait_s)                             AS wait_s,
    count(wait_s)                           AS wait_n,
    sum(day1_wait_s)                        AS day1_wait_s,
    sum(compose_s)                          AS compose_s,
    count(compose_s)                        AS compose_n
FROM messages
GROUP BY 1, 2
WITH NO DATA;

CREATE MATERIALIZED VIEW IF NOT EXISTS messages_daily
WITH (timescaledb.continuous, timescaledb.materialized_only = false) AS
SELECT
    time_bucket(INTERVAL '1 day', bucket, 'America/New_York') AS bucket,
    simulated,
    sum(messages)                           AS messages,
    sum(clenches)                           AS clenches,
    sum(scan_steps)                         AS scan_steps,
    sum(day1_clenches)                      AS day1_clenches,
    sum(day1_scan_steps)                    AS day1_scan_steps,
    sum(wait_s)                             AS wait_s,
    sum(wait_n)                             AS wait_n,
    sum(day1_wait_s)                        AS day1_wait_s,
    sum(compose_s)                          AS compose_s,
    sum(compose_n)                          AS compose_n
FROM messages_hourly
GROUP BY 1, 2
WITH NO DATA;

CREATE MATERIALIZED VIEW IF NOT EXISTS signal_hourly
WITH (timescaledb.continuous, timescaledb.materialized_only = false) AS
SELECT
    time_bucket(INTERVAL '1 hour', time)            AS bucket,
    simulated,
    count(*)                                        AS samples,
    count(*) FILTER (WHERE connected)               AS connected_samples,
    count(*) FILTER (WHERE blocked)                 AS blocked_samples,
    sum(threshold)                                  AS threshold_sum,
    count(threshold)                                AS threshold_n,
    min(threshold)                                  AS threshold_min,
    max(threshold)                                  AS threshold_max
FROM signal
GROUP BY 1, 2
WITH NO DATA;

CREATE MATERIALIZED VIEW IF NOT EXISTS signal_daily
WITH (timescaledb.continuous, timescaledb.materialized_only = false) AS
SELECT
    time_bucket(INTERVAL '1 day', bucket, 'America/New_York') AS bucket,
    simulated,
    sum(samples)                                    AS samples,
    sum(connected_samples)                          AS connected_samples,
    sum(blocked_samples)                            AS blocked_samples,
    sum(threshold_sum)                              AS threshold_sum,
    sum(threshold_n)                                AS threshold_n,
    min(threshold_min)                              AS threshold_min,
    max(threshold_max)                              AS threshold_max
FROM signal_hourly
GROUP BY 1, 2
WITH NO DATA;

-- ---------------------------------------------------------------------------------------------
-- Refresh policies. Each window spans well over two buckets (TimescaleDB refuses less).
-- ---------------------------------------------------------------------------------------------

SELECT add_continuous_aggregate_policy('gestures_hourly',
    start_offset => INTERVAL '3 days', end_offset => INTERVAL '1 hour',
    schedule_interval => INTERVAL '30 minutes', if_not_exists => true);
SELECT add_continuous_aggregate_policy('gestures_daily',
    start_offset => INTERVAL '7 days', end_offset => INTERVAL '1 hour',
    schedule_interval => INTERVAL '1 hour', if_not_exists => true);
SELECT add_continuous_aggregate_policy('messages_hourly',
    start_offset => INTERVAL '3 days', end_offset => INTERVAL '1 hour',
    schedule_interval => INTERVAL '30 minutes', if_not_exists => true);
SELECT add_continuous_aggregate_policy('messages_daily',
    start_offset => INTERVAL '7 days', end_offset => INTERVAL '1 hour',
    schedule_interval => INTERVAL '1 hour', if_not_exists => true);
SELECT add_continuous_aggregate_policy('signal_hourly',
    start_offset => INTERVAL '3 days', end_offset => INTERVAL '1 hour',
    schedule_interval => INTERVAL '30 minutes', if_not_exists => true);
SELECT add_continuous_aggregate_policy('signal_daily',
    start_offset => INTERVAL '7 days', end_offset => INTERVAL '1 hour',
    schedule_interval => INTERVAL '1 hour', if_not_exists => true);

-- ---------------------------------------------------------------------------------------------
-- Columnstore (compression) on the signal table: chunks older than a day are converted.
-- CREATE TABLE ... WITH (tsdb.hypertable) already adds this policy (after = the chunk interval);
-- stating it keeps the intent visible and covers a table created some other way. if_not_exists
-- keeps a re-apply from replacing the policy, which would start an immediate conversion run.
-- ---------------------------------------------------------------------------------------------

CALL add_columnstore_policy('signal', after => INTERVAL '1 day', if_not_exists => true);
