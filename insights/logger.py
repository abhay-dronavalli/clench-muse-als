"""Read-only logger: listens on the core's /ws/console and records gestures, signal, messages and body
state into Tiger Data.

    python -m insights.logger            record into TIGER_DATABASE_URL
    python -m insights.logger --print    print the rows instead (no database needed)

It never sends a message to the core (the websocket library still answers pings, as every client
must). Clench does not depend on it: the core treats it like any open caregiver console, and this
process never blocks its socket on the database. Rows wait in a bounded buffer while the database is
unreachable (at most MAX_ROWS rows or MAX_AGE_S seconds), then the oldest are dropped with a log line.
When the core restarts, it reconnects on its own.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from collections import deque
from collections.abc import Callable
from typing import Any, Protocol

import psycopg
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidHandshake

from insights import config
from insights.db import connect as db_connect, insert_rows
from insights.mapping import Row, Tracker

log = logging.getLogger("insights.logger")

MAX_ROWS = 5000        # about 20 minutes of 4 Hz signal; far more than a brief outage needs
MAX_AGE_S = 60.0       # older than this and the row is dropped: the buffer is for blips, not outages
FLUSH_S = 1.0
DROP_LOG_EVERY_S = 30.0


class Buffer:
    """FIFO of rows waiting for the database. Bounded by count and by age; drops the oldest."""

    def __init__(self, max_rows: int = MAX_ROWS, max_age_s: float = MAX_AGE_S,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.max_rows = max_rows
        self.max_age_s = max_age_s
        self.clock = clock
        self._q: deque[tuple[float, Row]] = deque()
        self.dropped = 0

    def __len__(self) -> int:
        return len(self._q)

    def add(self, row: Row) -> None:
        self._q.append((self.clock(), row))
        self._trim()

    def take(self) -> list[tuple[float, Row]]:
        self.expire()
        batch = list(self._q)
        self._q.clear()
        return batch

    def put_back(self, batch: list[tuple[float, Row]]) -> None:
        """Return a batch that failed to write, ahead of anything that arrived meanwhile."""
        self._q.extendleft(reversed(batch))
        self._trim()
        self.expire()

    def expire(self) -> None:
        cutoff = self.clock() - self.max_age_s
        while self._q and self._q[0][0] < cutoff:
            self._q.popleft()
            self.dropped += 1

    def _trim(self) -> None:
        while len(self._q) > self.max_rows:
            self._q.popleft()
            self.dropped += 1


class Sink(Protocol):
    def write(self, rows: list[Row]) -> None: ...


class DbSink:
    """Writes batches in one transaction. Reconnects lazily after any connection failure."""

    def __init__(self, url: str) -> None:
        self.url = url
        self.conn: psycopg.Connection | None = None

    def write(self, rows: list[Row]) -> None:
        if self.conn is None or self.conn.closed:
            # A write must fail, not hang, when the database stalls mid-query or the network drops
            # silently: keepalives notice a dead peer, statement_timeout bounds a stuck statement.
            # The writer then takes its normal "database unavailable" path (buffer, drop, log).
            self.conn = db_connect(self.url, connect_timeout=5, keepalives=1, keepalives_idle=10,
                                   keepalives_interval=5, keepalives_count=3)
            self.conn.execute("SET statement_timeout = '15s'")
            log.info("database connected")
        by_table: dict[str, list[dict[str, Any]]] = {}
        for r in rows:
            by_table.setdefault(r.table, []).append(r.values)
        try:
            with self.conn.transaction():
                for table, values in by_table.items():
                    insert_rows(self.conn, table, values)
        except (psycopg.OperationalError, psycopg.InterfaceError):
            self.conn.close()
            self.conn = None
            raise


class PrintSink:
    def write(self, rows: list[Row]) -> None:
        for r in rows:
            print(r.table, r.values, flush=True)


class Writer:
    def __init__(self, buf: Buffer, sink: Sink, clock: Callable[[], float] = time.monotonic) -> None:
        self.buf = buf
        self.sink = sink
        self.clock = clock
        self.written = 0
        self._reported_drops = 0
        self._last_drop_log = float("-inf")
        self._failing = False

    async def flush(self) -> None:
        batch = self.buf.take()
        if batch:
            try:
                await asyncio.to_thread(self.sink.write, [r for _, r in batch])
                self.written += len(batch)
                if self._failing:
                    log.info("database writes resumed")
                    self._failing = False
            except (psycopg.OperationalError, psycopg.InterfaceError, OSError) as e:
                # Connection trouble: keep the rows for a retry (the buffer bounds how long).
                self.buf.put_back(batch)
                if not self._failing:
                    log.warning("database unavailable (%s); buffering up to %d rows / %.0f s",
                                type(e).__name__, self.buf.max_rows, self.buf.max_age_s)
                    self._failing = True
            except psycopg.Error as e:
                # The database refused the rows themselves (a CHECK, a type): retrying cannot help.
                self.buf.dropped += len(batch)
                log.error("database rejected %d rows (%s); dropped", len(batch), type(e).__name__)
        self._report_drops()

    def _report_drops(self) -> None:
        new = self.buf.dropped - self._reported_drops
        if new and self.clock() - self._last_drop_log >= DROP_LOG_EVERY_S:
            log.warning("dropped %d rows while the database was unavailable", new)
            self._reported_drops = self.buf.dropped
            self._last_drop_log = self.clock()

    async def run(self, interval: float = FLUSH_S) -> None:
        while True:
            await asyncio.sleep(interval)
            await self.flush()


def handle_frame(text: str | bytes, tracker: Tracker, buf: Buffer, now: float) -> int:
    if not isinstance(text, str):
        return 0
    try:
        msg = json.loads(text)
    except ValueError:
        return 0
    if not isinstance(msg, dict):
        return 0
    rows = tracker.handle(msg, now)
    for r in rows:
        buf.add(r)
    return len(rows)


async def read_core(url: str, tracker: Tracker, buf: Buffer, *, first_delay: float = 0.5,
                    max_delay: float = 10.0) -> None:
    """Listen forever, reconnecting with a short capped backoff whenever the core is down or restarts
    (the library's own reconnect backs off to 90 s, too slow for a core restart). This function has
    no send call on purpose."""
    delay = first_delay
    down_logged = False
    while True:
        try:
            async with connect(url, open_timeout=5, max_size=2**20) as ws:
                log.info("connected to %s", url)
                delay, down_logged = first_delay, False
                async for frame in ws:
                    handle_frame(frame, tracker, buf, time.time())
            log.info("core closed the connection; reconnecting")
        except ConnectionClosed:
            log.info("core connection lost; reconnecting")
        except (OSError, InvalidHandshake, TimeoutError) as e:
            if not down_logged:
                log.warning("core not reachable at %s (%s); retrying every %.0f s at most",
                            url, type(e).__name__, max_delay)
                down_logged = True
        await asyncio.sleep(delay)
        delay = min(delay * 2, max_delay)


async def run(url: str, sink: Sink) -> None:
    buf = Buffer()
    writer = Writer(buf, sink)
    tracker = Tracker(simulated=False)
    await asyncio.gather(read_core(url, tracker, buf), writer.run())


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    p = argparse.ArgumentParser(prog="python -m insights.logger")
    p.add_argument("--print", action="store_true", help="print rows instead of writing them")
    p.add_argument("--url", help="core console websocket (default INSIGHTS_CORE_WS or ws://127.0.0.1:CORE_PORT/ws/console)")
    args = p.parse_args(argv)
    cfg = config.load()
    if args.print:
        sink: Sink = PrintSink()
    elif cfg.database_url:
        sink = DbSink(cfg.database_url)
    else:
        sys.exit("TIGER_DATABASE_URL is not set (add it to .env), or run with --print.")
    try:
        asyncio.run(run(args.url or cfg.core_ws_url, sink))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
