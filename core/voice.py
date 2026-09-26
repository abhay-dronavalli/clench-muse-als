"""Voice service (PRD A3.2 Voice): everything the board says goes through Voice.speak().

Every utterance gets an id, sent to the board in SPEAK / PLAY_AUDIO and echoed back in AUDIO_DONE, so
the session only reacts to the phrase it is waiting for.

speak(text, lang, kind):
  - audio already in the disk cache: PLAY_AUDIO right away (no network).
  - not cached and ElevenLabs is available: synthesize with a short timeout (2.5 s for an echo, 4 s
    for a phrase or system line). In time: save it and send PLAY_AUDIO. Too slow or an error: send
    SPEAK so the browser voice says it at once; a slow request keeps going and is cached for next time.
  - no ElevenLabs key or voice id (NullTTS), or the circuit breaker is open: SPEAK.

The app therefore always talks, with no key and with no internet. After a 401, a 402 / quota error or
3 errors in a row, ElevenLabs is left alone for 5 minutes (one warning in the log).

Cache: data/audio_cache/<sha256(text|lang|voice_id|model)>.mp3, one audio_cache row per file (PRD A7),
served by the Core at /audio/<hash>.mp3. The file on disk is what counts; the row is the record.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import time
import uuid
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import httpx

from core.contracts import Lang, Message, PlayAudio, Speak, UtteranceKind
from core.db import Db
from core.menu import DATA_DIR

log = logging.getLogger("clench.voice")

Emit = Callable[[Message], None]

AUDIO_DIR = DATA_DIR / "audio_cache"
AUDIO_NAME = re.compile(r"^[0-9a-f]{64}\.mp3$")

# How long a live utterance waits for ElevenLabs before the browser voice says it instead.
TIMEOUTS: dict[UtteranceKind, float] = {"echo": 2.5, "phrase": 4.0, "system": 4.0}

ELEVENLABS_API = "https://api.elevenlabs.io/v1/text-to-speech"
DEFAULT_MODEL = "eleven_flash_v2_5"
OUTPUT_FORMAT = "mp3_44100_128"
HTTP_TIMEOUT_S = 20.0  # hard limit for one request; live speech never waits this long
# Only these models accept `language_code`; it keeps one-word labels ("Dolor") in the right accent.
LANGUAGE_CODE_MODELS = frozenset({"eleven_flash_v2_5", "eleven_turbo_v2_5"})

BREAKER_ERRORS = 3
BREAKER_COOLDOWN_S = 300.0


def new_utterance_id() -> str:
    return uuid.uuid4().hex[:12]


# --- TTS providers -----------------------------------------------------------------


class TTSError(Exception):
    """A TTS request failed. `fatal` (bad key, no credit, quota) opens the circuit breaker at once."""

    def __init__(self, detail: str, *, status: int | None = None, fatal: bool = False) -> None:
        super().__init__(detail)
        self.status = status
        self.fatal = fatal


class TTS(Protocol):
    name: str
    voice_id: str | None  # None = this provider makes no audio (browser speech only)
    model: str

    async def synthesize(self, text: str, lang: Lang) -> bytes | None:
        """mp3 bytes, or None when this provider has no voice. Raises TTSError on failure."""
        ...

    async def aclose(self) -> None: ...


class NullTTS:
    """No ElevenLabs key or voice id: every utterance goes to browser speech."""

    name = "browser speech"
    voice_id: str | None = None
    model = ""

    def __init__(self, reason: str = "no TTS configured") -> None:
        self.reason = reason

    async def synthesize(self, text: str, lang: Lang) -> bytes | None:
        return None

    async def aclose(self) -> None:
        pass


class ElevenLabsTTS:
    """POST /v1/text-to-speech/{voice_id} with the xi-api-key header and JSON {text, model_id}; mp3 back."""

    name = "ElevenLabs"

    def __init__(
        self,
        api_key: str,
        voice_id: str,
        model: str = DEFAULT_MODEL,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = HTTP_TIMEOUT_S,
    ) -> None:
        self._key = api_key
        self.voice_id: str | None = voice_id
        self.model = model
        self._transport = transport  # tests pass httpx.MockTransport
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None  # one client, so live requests reuse the connection

    def request_body(self, text: str, lang: Lang) -> dict[str, Any]:
        body: dict[str, Any] = {"text": text, "model_id": self.model}
        if self.model in LANGUAGE_CODE_MODELS:
            body["language_code"] = lang
        return body

    async def synthesize(self, text: str, lang: Lang) -> bytes:
        if self._client is None:
            self._client = httpx.AsyncClient(transport=self._transport, timeout=self._timeout)
        try:
            resp = await self._client.post(
                f"{ELEVENLABS_API}/{self.voice_id}",
                params={"output_format": OUTPUT_FORMAT},
                headers={"xi-api-key": self._key, "Accept": "audio/mpeg"},
                json=self.request_body(text, lang),
            )
        except httpx.TimeoutException as e:
            raise TTSError(f"ElevenLabs timed out after {self._timeout:.0f} s") from e
        except httpx.HTTPError as e:
            raise TTSError(f"ElevenLabs unreachable: {e.__class__.__name__}: {e}") from e
        if resp.status_code != 200:
            raise _elevenlabs_error(resp)
        if not resp.content:
            raise TTSError("ElevenLabs sent no audio", status=200)
        return resp.content

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None


def _elevenlabs_error(resp: httpx.Response) -> TTSError:
    """ElevenLabs errors look like {"detail": {"status": "quota_exceeded", "message": "..."}}."""
    try:
        body = resp.json()
    except ValueError:
        body = None
    detail = body.get("detail") if isinstance(body, dict) else None
    status = ""
    if isinstance(detail, dict):
        status = str(detail.get("status") or "")
        message = str(detail.get("message") or "")
    elif isinstance(detail, str):
        message = detail
    else:
        message = resp.text[:200]
    quota = "quota" in status or "quota" in message.lower()
    label = f" {status}" if status else ""
    return TTSError(
        f"ElevenLabs error (HTTP {resp.status_code}){label}: {message}".rstrip(": "),
        status=resp.status_code,
        fatal=resp.status_code in (401, 402) or quota,
    )


def build_tts(env: Mapping[str, str], *, transport: httpx.AsyncBaseTransport | None = None) -> TTS:
    """ElevenLabs when ELEVENLABS_API_KEY and ELEVENLABS_VOICE_ID are set, otherwise NullTTS."""
    key = env.get("ELEVENLABS_API_KEY", "").strip()
    voice_id = env.get("ELEVENLABS_VOICE_ID", "").strip()
    model = env.get("ELEVENLABS_MODEL", "").strip() or DEFAULT_MODEL
    missing = [name for name, value in (("ELEVENLABS_API_KEY", key), ("ELEVENLABS_VOICE_ID", voice_id)) if not value]
    if missing:
        return NullTTS(f"{', '.join(missing)} missing")
    return ElevenLabsTTS(key, voice_id, model, transport=transport)


# --- circuit breaker ---------------------------------------------------------------


class CircuitBreaker:
    """Stops calling ElevenLabs for `cooldown_s` after a fatal error or `max_errors` errors in a row."""

    def __init__(
        self,
        *,
        max_errors: int = BREAKER_ERRORS,
        cooldown_s: float = BREAKER_COOLDOWN_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._max_errors = max_errors
        self._cooldown_s = cooldown_s
        self._clock = clock
        self._errors = 0
        self._open_until: float | None = None

    @property
    def is_open(self) -> bool:
        return self._open_until is not None and self._clock() < self._open_until

    def allow(self) -> bool:
        if self._open_until is None:
            return True
        if self._clock() < self._open_until:
            return False
        self._open_until = None
        self._errors = 0
        log.info("voice: trying ElevenLabs again")
        return True

    def success(self) -> None:
        self._errors = 0

    def failure(self, reason: str, *, fatal: bool = False) -> None:
        if self._open_until is not None:
            return  # already open: requests that were in flight do not extend it or log again
        self._errors += 1
        if fatal or self._errors >= self._max_errors:
            self._open_until = self._clock() + self._cooldown_s
            why = reason if fatal else f"{self._errors} errors in a row (last: {reason})"
            log.warning(
                "voice: ElevenLabs switched off for %d min after %s. Using browser speech.",
                self._cooldown_s // 60,
                why,
            )
        else:
            log.warning("voice: ElevenLabs failed (%d in a row): %s", self._errors, reason)


# --- disk cache --------------------------------------------------------------------


class AudioCache:
    def __init__(self, directory: Path = AUDIO_DIR, db: Db | None = None) -> None:
        self.directory = directory
        self._db = db

    @staticmethod
    def key(text: str, lang: Lang, voice_id: str, model: str) -> str:
        return hashlib.sha256(f"{text}|{lang}|{voice_id}|{model}".encode()).hexdigest()

    @staticmethod
    def url(key: str) -> str:
        return f"/audio/{key}.mp3"

    def path(self, key: str) -> Path:
        return self.directory / f"{key}.mp3"

    def lookup(self, key: str) -> Path | None:
        """The cached file, or None. Never touches the network."""
        path = self.path(key)
        try:
            return path if path.stat().st_size > 0 else None
        except OSError:
            return None

    def put(self, key: str, text: str, lang: Lang, voice_id: str, audio: bytes) -> Path:
        """Write the file (atomically, so a half-written file is never served) and its audio_cache row."""
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.path(key)
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(audio)
        os.replace(tmp, path)
        if self._db is not None:
            try:
                self._db.add_audio(key, text=text, lang=lang, voice=voice_id, file_path=_display_path(path))
            except Exception:
                log.exception("could not record audio_cache row for %r", text)
        return path


def _display_path(path: Path) -> str:
    """data/audio_cache/<hash>.mp3 relative to the repo when possible."""
    try:
        return path.resolve().relative_to(DATA_DIR.parent.resolve()).as_posix()
    except ValueError:
        return str(path)


# --- the service -------------------------------------------------------------------


@dataclass
class PrewarmReport:
    made: int = 0
    cached: int = 0
    failed: int = 0
    chars: int = 0  # characters sent to ElevenLabs for audio that came back
    stopped: bool = False  # the circuit breaker opened, the rest was skipped


class Voice:
    def __init__(
        self,
        emit: Emit,
        *,
        tts: TTS | None = None,
        cache: AudioCache | None = None,
        breaker: CircuitBreaker | None = None,
        timeouts: Mapping[UtteranceKind, float] = TIMEOUTS,
    ) -> None:
        self._emit = emit
        self.tts: TTS = tts or NullTTS()
        self._cache = cache
        self.breaker = breaker or CircuitBreaker()
        self._timeouts = timeouts
        self._latest: str | None = None  # newest utterance id; a late echo older than this is dropped
        self._inflight: dict[str, asyncio.Task[bool]] = {}  # one request per text, shared by callers
        self._tasks: set[asyncio.Task[Any]] = set()
        self.chars_sent = 0  # characters of audio made since startup (free-tier quota watch)

    def describe(self) -> str:
        if self.tts.voice_id is None:
            reason = getattr(self.tts, "reason", "")
            return f"browser speech ({reason})" if reason else "browser speech"
        if self._cache is None:
            return "browser speech (no audio cache)"
        return f"{self.tts.name} (voice {self.tts.voice_id}, model {self.tts.model})"

    def speak(self, text: str, lang: Lang, kind: UtteranceKind) -> str:
        """Say `text` on the board and return the utterance id. Never blocks and never raises."""
        uid = new_utterance_id()
        self._latest = uid
        key = self._key(text, lang)
        if key is not None:
            assert self._cache is not None
            if self._cache.lookup(key) is not None:
                self._emit(self._play(uid, key, text, lang, kind, cached=True))
                return uid
            if self.breaker.allow():
                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError:  # no event loop (some tests): no time to fetch audio
                    loop = None
                if loop is not None:
                    self._keep(loop.create_task(self._speak_uncached(uid, key, text, lang, kind)))
                    return uid
        self._emit(Speak(id=uid, kind=kind, text=text, lang=lang))
        return uid

    async def prewarm(self, lines: Iterable[tuple[str, Lang]]) -> PrewarmReport:
        """Make audio for every line that is not cached yet, one request at a time."""
        report = PrewarmReport()
        todo = list(dict.fromkeys(lines))
        for text, lang in todo:
            key = self._key(text, lang)
            if key is None:
                break
            assert self._cache is not None
            if self._cache.lookup(key) is not None:
                report.cached += 1
                continue
            if not self.breaker.allow():
                report.stopped = True
                break
            if await self._fetch(key, text, lang):
                report.made += 1
                report.chars += len(text)
            else:
                report.failed += 1
        log.info(
            "voice prewarm: %d new, %d already cached, %d failed%s; %d characters sent to %s",
            report.made,
            report.cached,
            report.failed,
            ", stopped early (ElevenLabs switched off)" if report.stopped else "",
            report.chars,
            self.tts.name,
        )
        return report

    async def aclose(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        await self.tts.aclose()

    # --- internals ----------------------------------------------------------------

    def _key(self, text: str, lang: Lang) -> str | None:
        """Cache key, or None when there is no cloud voice (browser speech only)."""
        if self.tts.voice_id is None or self._cache is None:
            return None
        return AudioCache.key(text, lang, self.tts.voice_id, self.tts.model)

    def _play(self, uid: str, key: str, text: str, lang: Lang, kind: UtteranceKind, *, cached: bool) -> PlayAudio:
        return PlayAudio(id=uid, kind=kind, url=AudioCache.url(key), text=text, lang=lang, cached=cached)

    async def _speak_uncached(self, uid: str, key: str, text: str, lang: Lang, kind: UtteranceKind) -> None:
        job = self._fetch(key, text, lang)
        timeout = self._timeouts[kind]
        try:
            ok = await asyncio.wait_for(asyncio.shield(job), timeout)
        except TimeoutError:
            log.info("voice: %s slower than %.1f s for %r; browser speech now, cached for next time",
                     self.tts.name, timeout, text)
            ok = False
        msg: Message = self._play(uid, key, text, lang, kind, cached=False) if ok else Speak(
            id=uid, kind=kind, text=text, lang=lang
        )
        if kind == "echo" and self._latest != uid:
            log.debug("voice: echo %r dropped, something newer was said", text)
            return
        self._emit(msg)

    def _fetch(self, key: str, text: str, lang: Lang) -> asyncio.Task[bool]:
        """Start (or join) the request for `key`. The task outlives any caller that stops waiting."""
        task = self._inflight.get(key)
        if task is None:
            task = asyncio.get_running_loop().create_task(self._synthesize(key, text, lang))
            self._inflight[key] = task
            task.add_done_callback(lambda _: self._inflight.pop(key, None))
            self._keep(task)
        return task

    async def _synthesize(self, key: str, text: str, lang: Lang) -> bool:
        """One request; True when the audio is in the cache. Never raises."""
        assert self._cache is not None and self.tts.voice_id is not None
        try:
            audio = await self.tts.synthesize(text, lang)
        except TTSError as e:
            self.breaker.failure(str(e), fatal=e.fatal)
            return False
        except Exception as e:
            log.exception("voice: %s crashed on %r", self.tts.name, text)
            self.breaker.failure(f"{e.__class__.__name__}: {e}")
            return False
        if not audio:
            return False
        self.breaker.success()
        self.chars_sent += len(text)
        try:
            self._cache.put(key, text, lang, self.tts.voice_id, audio)
        except OSError:
            log.exception("voice: could not save audio for %r", text)
            return False
        return True

    def _keep(self, task: asyncio.Task[Any]) -> None:
        self._tasks.add(task)  # keep a reference so the task is not garbage-collected mid-request
        task.add_done_callback(self._tasks.discard)
