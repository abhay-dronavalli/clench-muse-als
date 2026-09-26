"""Suggester: the one place the session asks the AI for anything (PRD A3.5, D6).

  - Builds the SuggestContext from the request, the local hour and a short summary of the history
    in SQLite (top 20 phrases, last 5 confirmed sentences) plus the contacts' first names. Nothing
    else leaves the laptop (D14).
  - Runs each request in the background with a 4 s limit. On a timeout or any error the result is
    None and the session uses the fixed phrases (D6). Every failure is logged.
  - Caches results in memory for 10 minutes, keyed on (method, path, lang, hour, what is on screen),
    and shares one request between callers asking the same thing.
  - After a bad key or a rate limit (ProviderError.pause_s) the AI is left alone for a while, so the
    board does not wait on requests that will fail anyway.

Results come back as a Pending, not an asyncio future, so the session (which is synchronous and
driven by callbacks) and its tests never need an event loop to use it.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Generic, Literal, Protocol, TypeVar

from core.contracts import Lang
from core.suggest.errors import ProviderError
from core.suggest.provider import LLMProvider, Option, SuggestContext, drop_known

log = logging.getLogger("clench.suggest")

TIMEOUT_S = 4.0
CACHE_TTL_S = 600.0
TOP_PHRASES = 20
RECENT_MESSAGES = 5

T = TypeVar("T")
Spawn = Callable[[Coroutine[Any, Any, None]], None]
Method = Literal["compose", "more_options"]


class History(Protocol):
    """What the Suggester reads from the database (core.db.Db implements it)."""

    def top_phrases(self, lang: Lang, limit: int) -> list[str]: ...

    def recent_messages(self, lang: Lang, limit: int) -> list[str]: ...


class Pending(Generic[T]):
    """A result that may not be here yet. None = the AI had nothing (off, timeout, error)."""

    def __init__(self) -> None:
        self.done = False
        self.result: T | None = None
        self._callbacks: list[Callable[[T | None], None]] = []

    @classmethod
    def ready(cls, result: T | None) -> Pending[T]:
        p: Pending[T] = cls()
        p.resolve(result)
        return p

    def on_done(self, fn: Callable[[T | None], None]) -> None:
        if self.done:
            fn(self.result)
        else:
            self._callbacks.append(fn)

    def resolve(self, result: T | None) -> None:
        if self.done:
            return
        self.done = True
        self.result = result
        callbacks, self._callbacks = self._callbacks, []
        for fn in callbacks:
            try:
                fn(result)
            except Exception:
                log.exception("suggestion callback failed")


@dataclass(frozen=True)
class _Request:
    method: Method
    path: tuple[str, ...]
    lang: Lang
    fixed_phrase: str | None
    shown: tuple[str, ...]


class Suggester:
    def __init__(
        self,
        provider: LLMProvider | None,
        *,
        patient_name: str,
        contacts: dict[Lang, tuple[str, ...]] | None = None,
        history: History | None = None,
        spawn: Spawn | None = None,
        timeout: float = TIMEOUT_S,
        ttl: float = CACHE_TTL_S,
        clock: Callable[[], float] = time.monotonic,
        local_hour: Callable[[], int] = lambda: datetime.now().hour,
        off_reason: str = "no provider",
    ) -> None:
        self.provider = provider
        self._patient_name = patient_name
        self._contacts = contacts or {}
        self._history = history
        self._spawn = spawn or self._spawn_task
        self._timeout = timeout
        self._ttl = ttl
        self._clock = clock
        self._local_hour = local_hour
        self._off_reason = off_reason
        self._cache: dict[tuple[Any, ...], tuple[float, Any]] = {}
        self._inflight: dict[tuple[Any, ...], Pending[Any]] = {}
        self._paused_until: float | None = None
        self._tasks: set[asyncio.Task[None]] = set()
        self.calls = 0  # requests sent to the provider since startup

    # --- public ---------------------------------------------------------------

    @property
    def available(self) -> bool:
        """True when AI results can be expected. False = the session goes straight to fixed phrases."""
        if self.provider is None:
            return False
        if self._paused_until is not None and self._clock() < self._paused_until:
            return False
        return True

    def describe(self) -> str:
        if self.provider is None:
            return f"off ({self._off_reason}): fixed phrases only"
        paused = " (paused)" if not self.available else ""
        return f"{self.provider.name} ({self.provider.model}){paused}"

    def compose(
        self, path: tuple[str, ...], lang: Lang, *, fixed_phrase: str | None = None, shown: tuple[str, ...] = ()
    ) -> Pending[list[str]]:
        """Up to 3 sentences for `path` (() = right now), none equal to `fixed_phrase` or `shown`."""
        return self._request(_Request("compose", path, lang, fixed_phrase, shown))

    def more_options(self, path: tuple[str, ...], lang: Lang, *, shown: tuple[str, ...] = ()) -> Pending[list[Option]]:
        """Up to 5 new options for the level at `path`, none of them already in `shown`."""
        return self._request(_Request("more_options", path, lang, None, shown))

    async def aclose(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        if self.provider is not None:
            await self.provider.aclose()

    # --- internals ------------------------------------------------------------

    def _request(self, req: _Request) -> Pending[Any]:
        if not self.available:
            return Pending.ready(None)
        hour = self._local_hour()
        key = (req.method, req.path, req.lang, hour, req.fixed_phrase, req.shown)
        hit = self._cache.get(key)
        if hit is not None:
            expires, value = hit
            if self._clock() < expires:
                return Pending.ready(value)
            del self._cache[key]
        pending = self._inflight.get(key)
        if pending is not None:
            return pending
        pending = Pending()
        self._inflight[key] = pending
        try:
            self._spawn(self._run(key, req, hour, pending))
        except RuntimeError:  # no event loop (some tests): nothing can run, so nothing arrives
            log.debug("no event loop: AI %s for %s dropped", req.method, _show(req))
            self._inflight.pop(key, None)
            pending.resolve(None)
        return pending

    async def _run(self, key: tuple[Any, ...], req: _Request, hour: int, pending: Pending[Any]) -> None:
        value: Any = None
        started = self._clock()
        try:
            value = await self._call(req, hour)
        except TimeoutError:
            log.warning("AI %s for %s timed out after %.1f s: fixed phrases instead", req.method, _show(req), self._timeout)
        except ProviderError as e:
            log.warning("AI %s for %s failed: %s. Fixed phrases instead", req.method, _show(req), e)
            if e.pause_s:
                self._pause(e.pause_s)
        except Exception as e:
            log.warning("AI %s for %s failed: %s: %s. Fixed phrases instead", req.method, _show(req), e.__class__.__name__, e)
        else:
            self._cache[key] = (self._clock() + self._ttl, value)
            log.info("AI %s for %s: %d in %.1f s", req.method, _show(req), len(value), self._clock() - started)
        finally:
            self._inflight.pop(key, None)
            pending.resolve(value)

    async def _call(self, req: _Request, hour: int) -> list[Any]:
        assert self.provider is not None
        ctx = self._context(req, hour)
        self.calls += 1
        if req.method == "compose":
            sentences = await asyncio.wait_for(self.provider.compose(ctx), self._timeout)
            known = req.shown + ((req.fixed_phrase,) if req.fixed_phrase else ())
            return drop_known(sentences.sentences, known)
        options = await asyncio.wait_for(self.provider.more_options(ctx), self._timeout)
        labels = set(drop_known([o.label for o in options.options], req.shown))
        texts = set(drop_known([o.text for o in options.options], req.shown))
        return [o for o in options.options if o.label in labels and o.text in texts]

    def _context(self, req: _Request, hour: int) -> SuggestContext:
        top: list[str] = []
        recent: list[str] = []
        if self._history is not None:
            try:
                top = self._history.top_phrases(req.lang, TOP_PHRASES)
                recent = self._history.recent_messages(req.lang, RECENT_MESSAGES)
            except Exception:
                log.exception("could not read the history for the AI; asking without it")
        return SuggestContext(
            path=req.path,
            lang=req.lang,
            hour=hour,
            patient_name=self._patient_name,
            fixed_phrase=req.fixed_phrase,
            top_phrases=tuple(top),
            recent_messages=tuple(recent),
            contacts=self._contacts.get(req.lang, ()),
            shown=req.shown,
        )

    def _pause(self, seconds: float) -> None:
        if self.available:
            log.warning("AI paused for %d s: fixed phrases only until then", seconds)
        self._paused_until = self._clock() + seconds

    def _spawn_task(self, coro: Coroutine[Any, Any, None]) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            coro.close()
            raise
        task = loop.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)


def _show(req: _Request) -> str:
    return " > ".join(req.path) if req.path else "right now"
