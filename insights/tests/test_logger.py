import asyncio
import json
import socket

import psycopg
import pytest
from websockets.asyncio.server import serve

from insights.logger import Buffer, Writer, handle_frame, read_core
from insights.mapping import Row, Tracker


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def row(i: int) -> Row:
    return Row("gestures", {"i": i})


def test_buffer_drops_the_oldest_past_its_size():
    buf = Buffer(max_rows=3, clock=Clock())
    for i in range(5):
        buf.add(row(i))
    assert [r.values["i"] for _, r in buf.take()] == [2, 3, 4]
    assert buf.dropped == 2


def test_buffer_drops_rows_older_than_max_age():
    clock = Clock()
    buf = Buffer(max_age_s=60, clock=clock)
    buf.add(row(0))
    clock.now = 30
    buf.add(row(1))
    clock.now = 61
    assert [r.values["i"] for _, r in buf.take()] == [1]
    assert buf.dropped == 1


def test_put_back_keeps_order_ahead_of_newer_rows():
    buf = Buffer(clock=Clock())
    buf.add(row(0))
    buf.add(row(1))
    batch = buf.take()
    buf.add(row(2))
    buf.put_back(batch)
    assert [r.values["i"] for _, r in buf.take()] == [0, 1, 2]


class FlakySink:
    def __init__(self, fail_times: int, error: type[Exception] = psycopg.OperationalError) -> None:
        self.fail_times = fail_times
        self.error = error
        self.rows: list[Row] = []

    def write(self, rows: list[Row]) -> None:
        if self.fail_times:
            self.fail_times -= 1
            raise self.error("database down")
        self.rows.extend(rows)


def test_writer_keeps_rows_while_the_database_is_down_then_writes_them():
    buf = Buffer(clock=Clock())
    sink = FlakySink(fail_times=2)
    w = Writer(buf, sink, clock=Clock())
    buf.add(row(0))
    asyncio.run(w.flush())
    buf.add(row(1))
    asyncio.run(w.flush())
    assert sink.rows == [] and len(buf) == 2
    asyncio.run(w.flush())
    assert [r.values["i"] for r in sink.rows] == [0, 1] and len(buf) == 0


def test_writer_gives_up_on_an_outage_longer_than_the_buffer_and_logs_it(caplog):
    clock = Clock()
    buf = Buffer(max_age_s=60, clock=clock)
    w = Writer(buf, FlakySink(fail_times=100), clock=clock)
    buf.add(row(0))
    asyncio.run(w.flush())
    clock.now = 120
    with caplog.at_level("WARNING"):
        asyncio.run(w.flush())
    assert len(buf) == 0 and buf.dropped == 1
    assert "dropped 1 rows" in caplog.text


def test_writer_drops_rows_the_database_rejects_instead_of_retrying_forever():
    buf = Buffer(clock=Clock())
    w = Writer(buf, FlakySink(fail_times=1, error=psycopg.errors.CheckViolation), clock=Clock())
    buf.add(row(0))
    asyncio.run(w.flush())
    assert len(buf) == 0 and buf.dropped == 1


def test_bad_frames_are_ignored():
    buf = Buffer(clock=Clock())
    tr = Tracker()
    assert handle_frame(b"\x00binary", tr, buf, 0) == 0
    assert handle_frame("not json", tr, buf, 0) == 0
    assert handle_frame("[1, 2]", tr, buf, 0) == 0
    assert len(buf) == 0


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def _wait_for(pred, timeout: float = 5.0) -> None:
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while not pred():
        if loop.time() > end:
            raise AssertionError("timed out")
        await asyncio.sleep(0.02)


def test_logger_only_reads_and_reconnects_when_the_core_restarts():
    """A fake core on /ws/console: the logger must record what it sends, send nothing back, and
    come back on its own after the core goes away and returns on the same port."""
    port = _free_port()
    received: list[str] = []

    def core(frames: list[dict]):
        async def handler(ws):
            for f in frames:
                await ws.send(json.dumps(f))
            try:
                async for msg in ws:  # anything the logger sends lands here
                    received.append(msg)
            except Exception:
                pass
        return handler

    first = [{"type": "SETTINGS", "scan_ms": 1000},
             {"type": "INPUT_EVENT", "t": 1.0, "kind": "CLENCH", "source": "dev", "accepted": True,
              "reason": None, "strength": 1.0, "duration": None}]
    second = [{"type": "METRICS", "text": "hola", "selections": 2, "scan_steps": 0,
               "day1_selections": 5, "day1_scan_steps": 6}]

    async def scenario() -> list[Row]:
        buf = Buffer()
        task = asyncio.create_task(read_core(f"ws://127.0.0.1:{port}/ws/console", Tracker(), buf,
                                             first_delay=0.05, max_delay=0.2))
        try:
            async with serve(core(first), "127.0.0.1", port):
                await _wait_for(lambda: len(buf) == 1)
            await asyncio.sleep(0.3)  # core is down: the logger keeps retrying
            async with serve(core(second), "127.0.0.1", port):
                await _wait_for(lambda: len(buf) == 2)
                await asyncio.sleep(0.1)
        finally:
            task.cancel()
        return [r for _, r in buf.take()]

    rows = asyncio.run(scenario())
    assert [r.table for r in rows] == ["gestures", "messages"]
    assert received == []
