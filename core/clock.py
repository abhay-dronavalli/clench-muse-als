"""Injectable time and timers, so the session and pointers can be tested without real sleeps."""

from __future__ import annotations

import asyncio
import heapq
import itertools
import time
from collections.abc import Callable
from typing import Protocol


class TimerHandle(Protocol):
    def cancel(self) -> None: ...


class Scheduler(Protocol):
    def now(self) -> float:
        """Monotonic seconds."""
        ...

    def call_later(self, delay: float, fn: Callable[[], None]) -> TimerHandle: ...


class AsyncioScheduler:
    """Real timers on the running asyncio loop. Call only from inside the loop."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock

    def now(self) -> float:
        return self._clock()

    def call_later(self, delay: float, fn: Callable[[], None]) -> TimerHandle:
        return asyncio.get_running_loop().call_later(delay, fn)


class _ManualHandle:
    def __init__(self) -> None:
        self.cancelled = False

    def cancel(self) -> None:
        self.cancelled = True


class ManualScheduler:
    """Fake clock for tests and scripted runs: time only moves when advance() is called."""

    def __init__(self, start: float = 0.0) -> None:
        self._now = start
        self._seq = itertools.count()
        self._queue: list[tuple[float, int, _ManualHandle, Callable[[], None]]] = []

    def now(self) -> float:
        return self._now

    def call_later(self, delay: float, fn: Callable[[], None]) -> TimerHandle:
        handle = _ManualHandle()
        heapq.heappush(self._queue, (self._now + max(delay, 0.0), next(self._seq), handle, fn))
        return handle

    def advance(self, seconds: float) -> None:
        """Move time forward, running every timer that falls due, in order."""
        end = self._now + seconds
        while self._queue and self._queue[0][0] <= end:
            when, _, handle, fn = heapq.heappop(self._queue)
            self._now = when
            if not handle.cancelled:
                fn()
        self._now = end
