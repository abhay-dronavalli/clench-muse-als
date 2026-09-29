"""Suggester: the one place the session asks the AI for anything (PRD A3.5, D6).

  - Builds the SuggestContext from the request, the local hour and a short summary of the history
    in SQLite (top 20 phrases, last 5 confirmed sentences) plus the contacts' first names. Nothing
    else leaves the laptop (D14).
  - Runs each request in the background with a 4 s limit. On a timeout or any error the result is
    None and the session uses the fixed phrases (D6). Every failure is logged.
  - Caches results in memory for 10 minutes, keyed on (method, path, lang, hour, what is on screen),
    and shares one request between callers asking the same thing.
  - level_bundle(): the per-level prefetch. ONE request brings the sentences for every leaf on the
    level, its "Other..." options and (at home) the Suggested sentences, and each part lands in the
    cache under the key compose() / more_options() use. So opening a level costs at most one
    request, and none when everything is cached; a later pick finds its result (or the request
    still in flight) without asking again.
  - After a bad key or a rate limit (ProviderError.pause_s) the AI is left alone for a while, so the
    board does not wait on requests that will fail anyway.
  - Day 1 mode (use_history = False): the AI is told nothing about the patient's history.

Results come back as a Pending, not an asyncio future, so the session (which is synchronous and
driven by callbacks) and its tests never need an event loop to use it.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Generic, Literal, Protocol, TypeVar

from core.contracts import Lang
from core.suggest.errors import ProviderError
from core.suggest.provider import (
    Bundle,
    LevelContext,
    LevelLeaf,
    LLMProvider,
    Option,
    SuggestContext,
    SearchContext,
    drop_known,
)

log = logging.getLogger("clench.suggest")

TIMEOUT_S = 4.0
CACHE_TTL_S = 600.0
TOP_PHRASES = 20
RECENT_MESSAGES = 5
RATE_WINDOW_S = 60.0  # requests are counted over the last minute (debug log)

T = TypeVar("T")
Spawn = Callable[[Coroutine[Any, Any, None]], None]
Method = Literal["compose", "more_options"]
Key = tuple[Any, ...]


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
        self._cache: dict[Key, tuple[float, Any]] = {}
        self._inflight: dict[Key, Pending[Any]] = {}
        self._paused_until: float | None = None
        self._tasks: set[asyncio.Task[None]] = set()
        self._recent_calls: deque[float] = deque()
        self.calls = 0  # requests sent to the provider since startup
        self.use_history = True  # False in Day 1 mode: no top phrases or recent messages for the AI

    # --- public ---------------------------------------------------------------

    def search_suggestions(self, site: str, lang: Lang, *, shown=(), recent_searches=()) -> Pending[list[str]]:
        if site not in ("youtube", "spotify", "google") or not self.available:
            return Pending.ready(None)
        top, _ = self._summary(lang)
        ctx = SearchContext(site, lang, self._local_hour(), self._patient_name,
                            tuple(recent_searches[:5]) if self.use_history else (), top, tuple(shown[:15]))
        key = ("search", ctx, self.use_history)
        hit, value = self._cached(key)
        if hit:
            return Pending.ready(value)
        if key in self._inflight:
            return self._inflight[key]
        pending = self._inflight[key] = Pending()
        try:
            self._spawn(self._run_search(key, ctx, pending))
        except RuntimeError:
            self._inflight.pop(key, None)
            pending.resolve(None)
        return pending

    async def _run_search(self, key, ctx, pending):
        value = None
        try:
            self._count_call()
            response = await asyncio.wait_for(self.provider.search_suggestions(ctx), self._timeout)
            value = drop_known(response.queries, ctx.shown)
        except ProviderError as error:
            if error.pause_s:
                self._pause(error.pause_s)
            log.warning("AI search failed: using local search choices")
        except Exception:
            log.warning("AI search unavailable or timed out: using local search choices")
        finally:
            # Cache failures too: repeated DOM navigations must not retry a failing service.
            self._cache[key] = (self._clock() + self._ttl, value)
            self._inflight.pop(key, None)
            pending.resolve(value)

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

    def level_bundle(
        self,
        path: tuple[str, ...],
        lang: Lang,
        *,
        leaves: tuple[tuple[str, str | None], ...] = (),
        now: bool = False,
        other_shown: tuple[str, ...] | None = None,
    ) -> None:
        """Prefetch a whole menu level in one request: compose(path + (label,), fixed_phrase=...) for
        each (label, fixed phrase) in `leaves`, compose(()) when `now`, and more_options(path,
        shown=other_shown) unless other_shown is None. Parts already cached or in flight are left
        out; with nothing left no request is made, and a single part is asked on its own. The results
        are picked up by the usual compose() / more_options() calls."""
        if not self.available:
            return
        hour = self._local_hour()
        wanted: list[tuple[_Request, LevelLeaf | None]] = [
            (_Request("compose", path + (label,), lang, fixed, ()), LevelLeaf(f"L{i + 1}", label, fixed))
            for i, (label, fixed) in enumerate(leaves)
        ]
        if now:
            wanted.append((_Request("compose", (), lang, None, ()), None))
        if other_shown is not None:
            wanted.append((_Request("more_options", path, lang, None, other_shown), None))
        parts: dict[Key, tuple[_Request, LevelLeaf | None]] = {}
        for req, leaf in wanted:
            key = self._key(req, hour)
            if not self._known(key):
                parts.setdefault(key, (req, leaf))
        if not parts:
            log.debug("AI level %s: everything cached or on its way, no request", _where(path))
            return
        if len(parts) == 1:
            (req, _), = parts.values()
            self._request(req)  # same cost, smaller prompt
            return
        pendings: dict[Key, Pending[Any]] = {}
        for key in parts:
            pendings[key] = self._inflight[key] = Pending()
        ctx = self._level_context(path, lang, hour, list(parts.values()), other_shown)
        try:
            self._spawn(self._run_bundle(ctx, parts, pendings))
        except RuntimeError:  # no event loop (some tests): nothing can run, so nothing arrives
            log.debug("no event loop: AI level bundle for %s dropped", _where(path))
            for key, pending in pendings.items():
                self._inflight.pop(key, None)
                pending.resolve(None)

    async def aclose(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        if self.provider is not None:
            await self.provider.aclose()

    # --- internals ------------------------------------------------------------

    def _key(self, req: _Request, hour: int) -> Key:
        return (req.method, req.path, req.lang, hour, req.fixed_phrase, req.shown, self.use_history)

    def _cached(self, key: Key) -> tuple[bool, Any]:
        hit = self._cache.get(key)
        if hit is None:
            return False, None
        expires, value = hit
        if self._clock() < expires:
            return True, value
        del self._cache[key]
        return False, None

    def _known(self, key: Key) -> bool:
        """Cached, or already being asked."""
        return self._cached(key)[0] or key in self._inflight

    def _request(self, req: _Request) -> Pending[Any]:
        if not self.available:
            return Pending.ready(None)
        hour = self._local_hour()
        key = self._key(req, hour)
        hit, value = self._cached(key)
        if hit:
            return Pending.ready(value)
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

    async def _run(self, key: Key, req: _Request, hour: int, pending: Pending[Any]) -> None:
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
        self._count_call()
        if req.method == "compose":
            sentences = await asyncio.wait_for(self.provider.compose(ctx), self._timeout)
            return _fresh_sentences(req, sentences.sentences)
        options = await asyncio.wait_for(self.provider.more_options(ctx), self._timeout)
        return _fresh_options(req, options.options)

    async def _run_bundle(
        self,
        ctx: LevelContext,
        parts: dict[Key, tuple[_Request, LevelLeaf | None]],
        pendings: dict[Key, Pending[Any]],
    ) -> None:
        assert self.provider is not None
        where = _where(ctx.path)
        values: dict[Key, Any] = {}
        started = self._clock()
        try:
            self._count_call()
            bundle: Bundle = await asyncio.wait_for(self.provider.level_bundle(ctx), self._timeout)
        except TimeoutError:
            log.warning("AI level bundle for %s timed out after %.1f s: fixed phrases instead", where, self._timeout)
        except ProviderError as e:
            log.warning("AI level bundle for %s failed: %s. Fixed phrases instead", where, e)
            if e.pause_s:
                self._pause(e.pause_s)
        except Exception as e:
            log.warning("AI level bundle for %s failed: %s: %s. Fixed phrases instead", where, e.__class__.__name__, e)
        else:
            expires = self._clock() + self._ttl
            for key, (req, leaf) in parts.items():
                if leaf is not None:
                    got = bundle.for_leaf(leaf.id)
                    if got is None:
                        continue  # the model left it out: not cached, so a pick asks again
                    value: Any = _fresh_sentences(req, got)
                elif req.method == "compose":
                    value = _fresh_sentences(req, bundle.now)
                else:
                    value = _fresh_options(req, bundle.options)
                values[key] = value
                self._cache[key] = (expires, value)
            log.info("AI level bundle for %s: %d of %d parts in %.1f s", where, len(values), len(parts), self._clock() - started)
        finally:
            for key, pending in pendings.items():
                self._inflight.pop(key, None)
                pending.resolve(values.get(key))

    def _summary(self, lang: Lang) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """(top phrases, recent messages) for the AI; nothing in Day 1 mode."""
        if self._history is None or not self.use_history:
            return (), ()
        try:
            top = self._history.top_phrases(lang, TOP_PHRASES)
            recent = self._history.recent_messages(lang, RECENT_MESSAGES)
        except Exception:
            log.exception("could not read the history for the AI; asking without it")
            return (), ()
        return tuple(top), tuple(recent)

    def _context(self, req: _Request, hour: int) -> SuggestContext:
        top, recent = self._summary(req.lang)
        return SuggestContext(
            path=req.path,
            lang=req.lang,
            hour=hour,
            patient_name=self._patient_name,
            fixed_phrase=req.fixed_phrase,
            top_phrases=top,
            recent_messages=recent,
            contacts=self._contacts.get(req.lang, ()),
            shown=req.shown,
        )

    def _level_context(
        self,
        path: tuple[str, ...],
        lang: Lang,
        hour: int,
        parts: list[tuple[_Request, LevelLeaf | None]],
        other_shown: tuple[str, ...] | None,
    ) -> LevelContext:
        top, recent = self._summary(lang)
        return LevelContext(
            path=path,
            lang=lang,
            hour=hour,
            patient_name=self._patient_name,
            leaves=tuple(leaf for _, leaf in parts if leaf is not None),
            now=any(leaf is None and req.method == "compose" for req, leaf in parts),
            options=any(req.method == "more_options" for req, _ in parts),
            top_phrases=top,
            recent_messages=recent,
            contacts=self._contacts.get(lang, ()),
            shown=other_shown or (),
        )

    def _count_call(self) -> None:
        self.calls += 1
        now = self._clock()
        self._recent_calls.append(now)
        while self._recent_calls and now - self._recent_calls[0] > RATE_WINDOW_S:
            self._recent_calls.popleft()
        log.debug("AI requests in the last minute: %d", len(self._recent_calls))

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


def _fresh_sentences(req: _Request, sentences: list[str]) -> list[str]:
    return drop_known(sentences, req.shown + ((req.fixed_phrase,) if req.fixed_phrase else ()))


def _fresh_options(req: _Request, options: list[Option]) -> list[Option]:
    labels = set(drop_known([o.label for o in options], req.shown))
    texts = set(drop_known([o.text for o in options], req.shown))
    return [o for o in options if o.label in labels and o.text in texts]


def _where(path: tuple[str, ...]) -> str:
    return " > ".join(path) or "home"


def _show(req: _Request) -> str:
    return " > ".join(req.path) if req.path else "right now"
