"""Optional AI prior for the ranking: TypeSafe Jev (decisions.md "Learning").

Jev is a structured-choice model: it reads a state and answers typed questions with calibrated
probabilities. Each ranking asks ONE Choice question, "Which option does the patient most likely
want right now?", whose criteria map the tile ids to their labels or sentences (up to 255). The state
is a short plain summary from Ranker.jev_state() (time and weekday, language, where on the menu,
body state, last 5 messages with times, top 10 phrases with counts). The probabilities become the
score's AI prior.

Access, first one configured wins:
  a) TypeSafe API       TYPESAFE_API_KEY
     POST https://api.typesafe.ai/v1/systemone, Authorization: Bearer <key>
     {"model": "jev-latest", "state": ..., "questions": {...}} -> {"answers": {...}}
  b) Cloudflare Workers AI   CLOUDFLARE_ACCOUNT_ID + CLOUDFLARE_API_TOKEN
     POST https://api.cloudflare.com/client/v4/accounts/<account>/ai/run, Authorization: Bearer <token>
     {"model": "typesafe/jev", "input": {"state": ..., "questions": {...}}}
     -> {"answers": {...}} (also accepted inside Cloudflare's {"result": ..., "success": ...} envelope)
  c) neither: no Jev. The AI prior is 0 and ranking uses history and time only.

Both are plain REST calls through httpx (no SDK needed, and tests use httpx.MockTransport).
Never blocks: rank() returns a Pending. 1.5 s timeout, results cached for 10 minutes on the key the
caller gives (candidate ids, hour, last outcome id, language), and a circuit breaker like the
voice's: off for 5 minutes after a bad key, or after 3 failures in a row.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Coroutine, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from core.suggest.service import Pending
from core.voice import CircuitBreaker

log = logging.getLogger("clench.jev")

TIMEOUT_S = 1.5
CACHE_TTL_S = 600.0
MAX_CRITERIA = 255
MAX_LABEL = 200
QUESTION_ID = "next"
QUESTION = "Which option does the patient most likely want right now?"
TYPESAFE_URL = "https://api.typesafe.ai/v1/systemone"
TYPESAFE_MODEL = "jev-latest"
CLOUDFLARE_URL = "https://api.cloudflare.com/client/v4/accounts/{account}/ai/run"
CLOUDFLARE_MODEL = "typesafe/jev"
FATAL_STATUS = {401, 402, 403}  # bad key / no access: off for the whole cooldown at once

Spawn = Callable[[Coroutine[Any, Any, None]], None]


@dataclass(frozen=True)
class JevAnswer:
    choice: str | None
    probabilities: dict[str, float]  # tile id -> probability
    confidence: float
    latency_s: float


class JevError(Exception):
    def __init__(self, detail: str, *, fatal: bool = False) -> None:
        super().__init__(detail)
        self.fatal = fatal


class JevAccess(Protocol):
    name: str

    def request(self, state: str, questions: dict[str, Any]) -> tuple[str, dict[str, str], dict[str, Any]]:
        """(url, headers, JSON body) for one call."""
        ...

    def answers(self, data: Any) -> Any:
        """The `answers` object of a response body."""
        ...


class TypeSafeAccess:
    name = "TypeSafe API"

    def __init__(self, api_key: str, *, model: str = TYPESAFE_MODEL, url: str = TYPESAFE_URL) -> None:
        self._key = api_key
        self.model = model
        self._url = url

    def request(self, state: str, questions: dict[str, Any]) -> tuple[str, dict[str, str], dict[str, Any]]:
        body = {"model": self.model, "state": state, "questions": questions}
        return self._url, {"Authorization": f"Bearer {self._key}"}, body

    def answers(self, data: Any) -> Any:
        return data.get("answers") if isinstance(data, dict) else None


class CloudflareAccess:
    name = "Cloudflare Workers AI"
    model = CLOUDFLARE_MODEL

    def __init__(self, account_id: str, api_token: str) -> None:
        self._url = CLOUDFLARE_URL.format(account=account_id)
        self._token = api_token

    def request(self, state: str, questions: dict[str, Any]) -> tuple[str, dict[str, str], dict[str, Any]]:
        body = {"model": CLOUDFLARE_MODEL, "input": {"state": state, "questions": questions}}
        return self._url, {"Authorization": f"Bearer {self._token}"}, body

    def answers(self, data: Any) -> Any:
        if not isinstance(data, dict):
            return None
        if data.get("success") is False:
            raise JevError(f"Cloudflare error: {data.get('errors')}")
        result = data.get("result", data)
        return result.get("answers") if isinstance(result, dict) else None


def choice_question(criteria: Mapping[str, str]) -> dict[str, Any]:
    return {QUESTION_ID: {"type": "choice", "instructions": QUESTION, "criteria": dict(criteria)}}


def parse_answer(answers: Any, ids: set[str], latency_s: float) -> JevAnswer:
    """The Choice answer, keeping only probabilities for the ids that were asked about."""
    answer = answers.get(QUESTION_ID) if isinstance(answers, dict) else None
    if not isinstance(answer, dict):
        raise JevError(f"Jev sent no answer for {QUESTION_ID!r}: {str(answers)[:200]}")
    raw = answer.get("probabilities")
    probs: dict[str, float] = {}
    if isinstance(raw, dict):
        for k, v in raw.items():
            if k in ids and isinstance(v, (int, float)):
                probs[k] = min(max(float(v), 0.0), 1.0)
    choice = answer.get("choice")
    if not probs and choice not in ids:
        raise JevError(f"Jev answer has no usable probabilities: {str(answer)[:200]}")
    conf = answer.get("confidence")
    confidence = min(max(float(conf), 0.0), 1.0) if isinstance(conf, (int, float)) else 0.0
    return JevAnswer(choice if choice in ids else None, probs, confidence, latency_s)


class JevRanker:
    def __init__(
        self,
        access: JevAccess,
        *,
        timeout: float = TIMEOUT_S,
        ttl: float = CACHE_TTL_S,
        spawn: Spawn | None = None,
        clock: Callable[[], float] = time.monotonic,
        transport: httpx.AsyncBaseTransport | None = None,
        breaker: CircuitBreaker | None = None,
    ) -> None:
        self.access = access
        self._timeout = timeout
        self._ttl = ttl
        self._spawn = spawn or self._spawn_task
        self._clock = clock
        self._client = httpx.AsyncClient(transport=transport, timeout=httpx.Timeout(timeout))
        self.breaker = breaker or CircuitBreaker(clock=clock, name="Jev", fallback="Ranking with history only.", logger=log)
        self._cache: dict[tuple[Any, ...], tuple[float, JevAnswer]] = {}
        self._inflight: dict[tuple[Any, ...], Pending[JevAnswer]] = {}
        self._tasks: set[asyncio.Task[None]] = set()
        self.calls = 0

    @property
    def available(self) -> bool:
        return not self.breaker.is_open

    def describe(self) -> str:
        paused = " (paused)" if not self.available else ""
        return f"Jev via {self.access.name}{paused}"

    def cached(self, key: tuple[Any, ...]) -> JevAnswer | None:
        hit = self._cache.get(key)
        if hit is None:
            return None
        expires, answer = hit
        if self._clock() >= expires:
            del self._cache[key]
            return None
        return answer

    def rank(self, key: tuple[Any, ...], criteria: Mapping[str, str], state: str) -> Pending[JevAnswer]:
        """Ask which of `criteria` (id -> label or sentence) is most likely wanted. Resolves to None
        when Jev is off, fails or times out; the ranking then goes on without it."""
        if len(criteria) < 2 or not self.available:
            return Pending.ready(None)
        answer = self.cached(key)
        if answer is not None:
            return Pending.ready(answer)
        pending = self._inflight.get(key)
        if pending is not None:
            return pending
        pending = Pending()
        self._inflight[key] = pending
        trimmed = {k: v[:MAX_LABEL] for k, v in list(criteria.items())[:MAX_CRITERIA]}
        try:
            self._spawn(self._run(key, trimmed, state, pending))
        except RuntimeError:  # no event loop (some tests)
            self._inflight.pop(key, None)
            pending.resolve(None)
        return pending

    async def ask(self, criteria: Mapping[str, str], state: str) -> JevAnswer:
        """One call, raising on any failure (scripts/test_jev.py)."""
        url, headers, body = self.access.request(state, choice_question(criteria))
        started = self._clock()
        self.calls += 1
        try:
            resp = await asyncio.wait_for(self._client.post(url, headers=headers, json=body), self._timeout)
        except TimeoutError as e:
            raise JevError(f"no answer within {self._timeout} s") from e
        except httpx.HTTPError as e:
            raise JevError(f"{self.access.name} unreachable: {e.__class__.__name__}: {e}") from e
        if resp.status_code >= 400:
            raise JevError(
                f"{self.access.name} error (HTTP {resp.status_code}): {resp.text[:200]}", fatal=resp.status_code in FATAL_STATUS
            )
        try:
            data = resp.json()
        except ValueError as e:
            raise JevError(f"{self.access.name} sent something that is not JSON: {resp.text[:200]}") from e
        return parse_answer(self.access.answers(data), set(criteria), self._clock() - started)

    async def _run(self, key: tuple[Any, ...], criteria: dict[str, str], state: str, pending: Pending[JevAnswer]) -> None:
        answer: JevAnswer | None = None
        try:
            if not self.breaker.allow():
                return
            answer = await self.ask(criteria, state)
        except JevError as e:
            self.breaker.failure(str(e), fatal=e.fatal)
        except Exception as e:
            self.breaker.failure(f"{e.__class__.__name__}: {e}")
        else:
            self.breaker.success()
            self._cache[key] = (self._clock() + self._ttl, answer)
            log.info("Jev: %s (confidence %.2f) of %d in %.2f s", answer.choice, answer.confidence, len(criteria), answer.latency_s)
        finally:
            self._inflight.pop(key, None)
            pending.resolve(answer)

    async def aclose(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        await self._client.aclose()

    def _spawn_task(self, coro: Coroutine[Any, Any, None]) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            coro.close()
            raise
        task = loop.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)


def jev_access(env: Mapping[str, str]) -> tuple[JevAccess | None, str]:
    """(access, "") for the first configured path, or (None, why there is no Jev)."""
    key = env.get("TYPESAFE_API_KEY", "").strip()
    if key:
        return TypeSafeAccess(key, model=env.get("TYPESAFE_MODEL", "").strip() or TYPESAFE_MODEL), ""
    account = env.get("CLOUDFLARE_ACCOUNT_ID", "").strip()
    token = env.get("CLOUDFLARE_API_TOKEN", "").strip()
    if account and token:
        return CloudflareAccess(account, token), ""
    return None, "no TYPESAFE_API_KEY, no CLOUDFLARE_ACCOUNT_ID + CLOUDFLARE_API_TOKEN"


def build_jev(env: Mapping[str, str], *, transport: httpx.AsyncBaseTransport | None = None) -> tuple[JevRanker | None, str]:
    access, why = jev_access(env)
    if access is None:
        return None, why
    return JevRanker(access, transport=transport), ""
