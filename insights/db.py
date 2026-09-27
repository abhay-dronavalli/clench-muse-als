"""Database helpers: apply the schema, insert rows, refresh aggregates, measure compression.

Usage (from the repo root):
    python -m insights.db apply        create or update the tables, aggregates and policies
    python -m insights.db stats        row counts and the signal table's compression ratio
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
import time
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

import psycopg

from insights import config
from insights.mapping import COLUMNS

log = logging.getLogger("insights.db")

SCHEMA = Path(__file__).with_name("schema.sql")
TABLES = ("signal", "gestures", "messages", "body_state")
# Refresh order matters: a daily view built on an hourly view reads what that view materialized.
AGGREGATES = ("gestures_hourly", "gestures_daily", "messages_hourly", "messages_daily",
              "signal_hourly", "signal_daily")


def connect(url: str, **kw: Any) -> psycopg.Connection:
    # autocommit: refresh_continuous_aggregate and the columnstore procedures refuse to run inside
    # a transaction block, and every statement in schema.sql stands alone.
    return psycopg.connect(url, autocommit=True, connect_timeout=kw.pop("connect_timeout", 10), **kw)


def statements(sql: str) -> list[str]:
    """Split schema.sql into statements. It has no dollar-quoted bodies, so a semicolon at the end
    of a line always ends a statement."""
    no_comments = re.sub(r"--[^\n]*", "", sql)
    return [s.strip() for s in re.split(r";\s*\n", no_comments + "\n") if s.strip()]


def apply_schema(conn: psycopg.Connection) -> None:
    installed = conn.execute("SELECT 1 FROM pg_extension WHERE extname = 'timescaledb'").fetchone()
    for stmt in statements(SCHEMA.read_text(encoding="utf-8")):
        # Tiger Cloud's managed extension hook runs even for IF NOT EXISTS and looks up its helper
        # functions in the current schema, so the statement fails when the search path starts with
        # another schema (the tests' throwaway schema). Skip it when TimescaleDB is already there.
        if installed and stmt.upper().startswith("CREATE EXTENSION"):
            continue
        conn.execute(stmt)


def insert_rows(conn: psycopg.Connection, table: str, rows: Sequence[dict[str, Any]]) -> None:
    if not rows:
        return
    cols = COLUMNS[table]
    sql = f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))})"
    with conn.cursor() as cur:
        cur.executemany(sql, [tuple(r[c] for c in cols) for r in rows])


def copy_rows(conn: psycopg.Connection, table: str, rows: Iterable[Sequence[Any]]) -> int:
    """Bulk load with COPY. Rows are tuples in COLUMNS[table] order."""
    cols = COLUMNS[table]
    n = 0
    with conn.cursor() as cur, cur.copy(f"COPY {table} ({', '.join(cols)}) FROM STDIN") as cp:
        for r in rows:
            cp.write_row(r)
            n += 1
    return n


def execute_retrying(conn: psycopg.Connection, sql: str, params: tuple[Any, ...] | None = None,
                     attempts: int = 60, wait_s: float = 0.5) -> None:
    """Run a statement that can collide with a background policy job (a refresh or a columnstore
    conversion running on the same table). TimescaleDB refuses the statement instead of waiting,
    so wait for the job and try again."""
    for attempt in range(attempts):
        try:
            conn.execute(sql, params)
            return
        except (psycopg.errors.LockNotAvailable, psycopg.errors.SerializationFailure):
            if attempt == attempts - 1:
                raise
            time.sleep(wait_s)


def refresh_all(conn: psycopg.Connection) -> None:
    """Refresh every aggregate over its whole range. Refresh policies start running as soon as
    the schema is applied, so a manual refresh can collide with one; execute_retrying waits it out."""
    for view in AGGREGATES:
        execute_retrying(conn, f"CALL refresh_continuous_aggregate('{view}', NULL, NULL)")


def compress_signal(conn: psycopg.Connection, older_than: str = "1 day") -> int:
    chunks = [r[0] for r in conn.execute(
        "SELECT c::text FROM show_chunks('signal', older_than => %s::interval) AS c", (older_than,))]
    for c in chunks:
        execute_retrying(conn, "CALL convert_to_columnstore(%s::regclass, if_not_columnstore => true, "
                         "recompress => true)", (c,))
    return len(chunks)


def row_counts(conn: psycopg.Connection) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    for t in TABLES:
        counts = {"simulated": 0, "live": 0}
        for sim, n in conn.execute(f"SELECT simulated, count(*) FROM {t} GROUP BY simulated"):
            counts["simulated" if sim else "live"] = n
        out[t] = counts
    return out


def compression_stats(conn: psycopg.Connection) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT total_chunks, number_compressed_chunks, before_compression_total_bytes, "
        "after_compression_total_bytes FROM hypertable_columnstore_stats('signal')").fetchone()
    if row is None or not row[2] or not row[3]:
        return None
    total, compressed, before, after = row
    return {"total_chunks": total, "compressed_chunks": compressed,
            "before_bytes": before, "after_bytes": after, "ratio": before / after}


def print_stats(conn: psycopg.Connection) -> None:
    for t, c in row_counts(conn).items():
        print(f"  {t:<11} simulated={c['simulated']:>9,}  live={c['live']:>7,}")
    s = compression_stats(conn)
    if s is None:
        print("  signal compression: no compressed chunks yet")
    else:
        print(f"  signal compression: {s['compressed_chunks']}/{s['total_chunks']} chunks, "
              f"{s['before_bytes'] / 1e6:.1f} MB -> {s['after_bytes'] / 1e6:.1f} MB, "
              f"ratio {s['ratio']:.1f}x")


def require_url() -> str:
    url = config.load().database_url
    if not url:
        sys.exit("TIGER_DATABASE_URL is not set (add it to .env; it is never printed).")
    return url


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    p = argparse.ArgumentParser(prog="python -m insights.db")
    p.add_argument("command", choices=["apply", "stats", "refresh"])
    args = p.parse_args(argv)
    with connect(require_url()) as conn:
        if args.command == "apply":
            apply_schema(conn)
            print("schema applied")
        elif args.command == "refresh":
            refresh_all(conn)
            print("aggregates refreshed")
        else:
            print_stats(conn)


if __name__ == "__main__":
    main()
