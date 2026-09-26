"""Voice service tests. No real network: every ElevenLabs request goes to an httpx.MockTransport."""

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from core.contracts import Message, PlayAudio, Speak
from core.db import Db
from core.voice import (
    AudioCache,
    CircuitBreaker,
    ElevenLabsTTS,
    NullTTS,
    Voice,
    build_tts,
)

MP3 = b"ID3\x04fake-mp3"


class ElevenLabs:
    """Fake ElevenLabs: records requests and answers with `status` (or sleeps `delay` s first).

    With `hold=True` every request waits until the test calls release(), so a test decides exactly
    when a "slow" request finishes instead of racing real time.
    """

    def __init__(self, status: int = 200, body: object = None, delay: float = 0.0, hold: bool = False) -> None:
        self.status = status
        self.body = body
        self.delay = delay
        self.hold = hold
        self._released: asyncio.Event | None = None
        self.requests: list[httpx.Request] = []

    def release(self) -> None:
        self.hold = False
        if self._released is not None:
            self._released.set()

    async def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.hold:
            self._released = asyncio.Event()
            await self._released.wait()
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.status == 200:
            return httpx.Response(200, content=MP3, headers={"content-type": "audio/mpeg"})
        return httpx.Response(self.status, json=self.body)

    def tts(self, model: str = "eleven_flash_v2_5") -> ElevenLabsTTS:
        return ElevenLabsTTS("sk_SECRET", "VOICE123", model, transport=httpx.MockTransport(self.handler))


class FakeClock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def db():
    d = Db(":memory:")
    yield d
    d.close()


@pytest.fixture
def cache(tmp_path: Path, db: Db) -> AudioCache:
    return AudioCache(tmp_path / "audio_cache", db)


def make_voice(service: ElevenLabs, cache: AudioCache, sent: list[Message], **kw) -> Voice:
    return Voice(sent.append, tts=service.tts(), cache=cache, **kw)


async def settle() -> None:
    """Let background voice tasks run."""
    for _ in range(20):
        await asyncio.sleep(0)


async def until(condition, steps: int = 1000) -> None:
    """Let background tasks run until `condition()` holds. Counts event-loop turns, not seconds."""
    for _ in range(steps):
        if condition():
            return
        await asyncio.sleep(0)
    raise AssertionError("condition never became true")


# --- ElevenLabs request ----------------------------------------------------------------


def test_elevenlabs_request_shape():
    service = ElevenLabs()

    async def go():
        tts = service.tts()
        audio = await tts.synthesize("Dolor", "es")
        await tts.aclose()
        return audio

    assert asyncio.run(go()) == MP3
    (req,) = service.requests
    assert req.method == "POST"
    assert (req.url.scheme, req.url.host, req.url.path) == ("https", "api.elevenlabs.io", "/v1/text-to-speech/VOICE123")
    assert req.url.params["output_format"].startswith("mp3_")
    assert req.headers["xi-api-key"] == "sk_SECRET"
    assert req.headers["accept"] == "audio/mpeg"
    assert json.loads(req.content) == {"text": "Dolor", "model_id": "eleven_flash_v2_5", "language_code": "es"}


def test_language_code_only_for_models_that_take_it():
    service = ElevenLabs()

    async def go():
        tts = service.tts(model="eleven_multilingual_v2")
        await tts.synthesize("Water", "en")
        await tts.aclose()

    asyncio.run(go())
    assert json.loads(service.requests[0].content) == {"text": "Water", "model_id": "eleven_multilingual_v2"}


def test_build_tts_needs_key_and_voice():
    assert isinstance(build_tts({}), NullTTS)
    assert build_tts({"ELEVENLABS_API_KEY": "k"}).reason == "ELEVENLABS_VOICE_ID missing"  # type: ignore[attr-defined]
    tts = build_tts({"ELEVENLABS_API_KEY": "k", "ELEVENLABS_VOICE_ID": "v"})
    assert isinstance(tts, ElevenLabsTTS)
    assert (tts.voice_id, tts.model) == ("v", "eleven_flash_v2_5")
    assert build_tts({"ELEVENLABS_API_KEY": "k", "ELEVENLABS_VOICE_ID": "v", "ELEVENLABS_MODEL": "m2"}).model == "m2"


# --- speak: cache, fallback, timeout ---------------------------------------------------------


def test_null_tts_gives_speak(cache):
    sent: list[Message] = []
    voice = Voice(sent.append, tts=NullTTS(), cache=cache)
    uid = voice.speak("Agua", "es", "echo")
    assert sent == [Speak(id=uid, kind="echo", text="Agua", lang="es")]
    assert not cache.directory.exists()


def test_no_event_loop_gives_speak(cache):
    sent: list[Message] = []
    service = ElevenLabs()
    uid = make_voice(service, cache, sent).speak("Agua", "es", "phrase")  # sync caller, nothing to fetch with
    assert sent == [Speak(id=uid, kind="phrase", text="Agua", lang="es")]
    assert service.requests == []


def test_miss_writes_file_and_row_then_hit_makes_no_request(cache, db):
    service = ElevenLabs()
    sent: list[Message] = []

    async def go():
        voice = make_voice(service, cache, sent)
        first = voice.speak("Dolor", "es", "echo")
        await settle()
        second = voice.speak("Dolor", "es", "echo")
        await voice.aclose()
        return first, second

    first, second = asyncio.run(go())
    assert len(service.requests) == 1  # the second one came from disk
    key = AudioCache.key("Dolor", "es", "VOICE123", "eleven_flash_v2_5")
    url = f"/audio/{key}.mp3"
    assert sent == [
        PlayAudio(id=first, kind="echo", url=url, text="Dolor", lang="es", cached=False),
        PlayAudio(id=second, kind="echo", url=url, text="Dolor", lang="es", cached=True),
    ]
    assert cache.path(key).read_bytes() == MP3
    (row,) = db.audio_cache()
    assert (row["hash"], row["text"], row["lang"], row["voice"]) == (key, "Dolor", "es", "VOICE123")
    assert row["file_path"].endswith(f"{key}.mp3")
    assert row["created_at"] > 0


def test_cache_key_depends_on_text_lang_voice_and_model():
    keys = {
        AudioCache.key("Agua", "es", "V1", "m1"),
        AudioCache.key("Agua", "en", "V1", "m1"),
        AudioCache.key("Agua", "es", "V2", "m1"),
        AudioCache.key("Agua", "es", "V1", "m2"),
        AudioCache.key("Agua.", "es", "V1", "m1"),
    }
    assert len(keys) == 5
    assert all(len(k) == 64 for k in keys)


def test_cache_hit_needs_no_network_even_when_switched_off(cache):
    key = AudioCache.key("Agua", "es", "VOICE123", "eleven_flash_v2_5")
    cache.directory.mkdir(parents=True)
    cache.path(key).write_bytes(MP3)
    service = ElevenLabs(status=500)
    sent: list[Message] = []
    breaker = CircuitBreaker()
    breaker.failure("down", fatal=True)
    voice = make_voice(service, cache, sent, breaker=breaker)
    uid = voice.speak("Agua", "es", "phrase")  # no event loop needed either
    assert sent == [PlayAudio(id=uid, kind="phrase", url=f"/audio/{key}.mp3", text="Agua", lang="es", cached=True)]
    assert service.requests == []


def test_error_falls_back_to_speak(cache):
    service = ElevenLabs(status=500, body={"detail": {"status": "internal", "message": "boom"}})
    sent: list[Message] = []

    async def go():
        voice = make_voice(service, cache, sent)
        uid = voice.speak("My back hurts.", "en", "phrase")
        await settle()
        await voice.aclose()
        return uid

    uid = asyncio.run(go())
    assert sent == [Speak(id=uid, kind="phrase", text="My back hurts.", lang="en")]
    assert not list(cache.directory.glob("*.mp3"))


def test_timeout_falls_back_to_speak_and_still_caches(cache):
    # No real time: the request is held until release(), and a 0 s wait always times out first.
    service = ElevenLabs(hold=True)
    sent: list[Message] = []

    async def go():
        voice = make_voice(service, cache, sent, timeouts={"echo": 0.0, "phrase": 0.0, "system": 0.0})
        uid = voice.speak("Mucho", "es", "phrase")
        await until(lambda: sent)
        assert sent == [Speak(id=uid, kind="phrase", text="Mucho", lang="es")]  # said at once by the browser
        await until(lambda: service.requests)
        assert not list(cache.directory.glob("*.mp3"))  # ElevenLabs is still "working" on it
        service.release()  # the slow request finishes in the background
        await until(lambda: list(cache.directory.glob("*.mp3")))
        again = voice.speak("Mucho", "es", "phrase")
        await voice.aclose()
        return again

    again = asyncio.run(go())
    assert len(service.requests) == 1
    assert isinstance(sent[-1], PlayAudio) and sent[-1].id == again and sent[-1].cached


def test_same_text_twice_makes_one_request(cache):
    service = ElevenLabs(delay=0.05)
    sent: list[Message] = []

    async def go():
        voice = make_voice(service, cache, sent)
        voice.speak("Espalda", "es", "system")
        voice.speak("Espalda", "es", "system")
        await asyncio.sleep(0.2)
        await voice.aclose()

    asyncio.run(go())
    assert len(service.requests) == 1
    assert [type(m) for m in sent] == [PlayAudio, PlayAudio]


def test_late_echo_is_dropped_when_something_newer_was_said(cache):
    service = ElevenLabs(delay=0.05)
    sent: list[Message] = []

    async def go():
        voice = Voice(sent.append, tts=service.tts(), cache=cache)
        voice.speak("Necesito", "es", "echo")
        newer = voice.speak("Dolor", "es", "echo")
        await asyncio.sleep(0.2)
        await voice.aclose()
        return newer

    newer = asyncio.run(go())
    assert [(m.id, m.text) for m in sent] == [(newer, "Dolor")]


# --- circuit breaker ---------------------------------------------------------------------------


def test_breaker_opens_after_401_for_five_minutes(cache, caplog):
    service = ElevenLabs(status=401, body={"detail": {"status": "invalid_api_key", "message": "Invalid API key"}})
    clock = FakeClock()
    sent: list[Message] = []

    async def go():
        voice = make_voice(service, cache, sent, breaker=CircuitBreaker(clock=clock))
        voice.speak("Uno", "es", "echo")
        await settle()
        for text in ["Dos", "Tres", "Cuatro"]:
            voice.speak(text, "es", "echo")
            await settle()
        assert len(service.requests) == 1  # no retry on every word
        clock.t += 299
        voice.speak("Cinco", "es", "echo")
        await settle()
        assert len(service.requests) == 1
        clock.t += 2  # 5 minutes later: try again
        voice.speak("Seis", "es", "echo")
        await settle()
        assert len(service.requests) == 2
        await voice.aclose()

    asyncio.run(go())
    assert all(isinstance(m, Speak) for m in sent)
    assert [m.text for m in sent] == ["Uno", "Dos", "Tres", "Cuatro", "Cinco", "Seis"]
    warnings = [r.getMessage() for r in caplog.records if "switched off" in r.getMessage()]
    assert len(warnings) == 2  # once at first, once more after the retry failed
    assert "HTTP 401" in warnings[0] and "invalid_api_key" in warnings[0]


def test_breaker_opens_on_quota(cache):
    service = ElevenLabs(status=401, body={"detail": {"status": "quota_exceeded", "message": "This request exceeds your quota."}})
    sent: list[Message] = []

    async def go():
        voice = make_voice(service, cache, sent)
        voice.speak("Uno", "es", "echo")
        await settle()
        assert voice.breaker.is_open
        await voice.aclose()

    asyncio.run(go())


def test_breaker_opens_after_three_errors_in_a_row():
    clock = FakeClock()
    b = CircuitBreaker(clock=clock)
    b.failure("500")
    b.failure("500")
    b.success()  # a success resets the count
    b.failure("500")
    b.failure("500")
    assert b.allow()
    b.failure("500")
    assert b.is_open and not b.allow()
    clock.t += 300
    assert b.allow() and not b.is_open


def test_breaker_after_three_server_errors_stops_requests(cache):
    service = ElevenLabs(status=503, body={"detail": "busy"})
    sent: list[Message] = []

    async def go():
        voice = make_voice(service, cache, sent)
        for text in ["a", "b", "c", "d", "e"]:
            voice.speak(text, "en", "system")
            await settle()
        await voice.aclose()

    asyncio.run(go())
    assert len(service.requests) == 3
    assert [type(m) for m in sent] == [Speak] * 5


# --- prewarm ----------------------------------------------------------------------------------


def test_prewarm_skips_cached_and_counts_characters(cache):
    service = ElevenLabs()
    cached_key = AudioCache.key("Agua", "es", "VOICE123", "eleven_flash_v2_5")
    cache.directory.mkdir(parents=True)
    cache.path(cached_key).write_bytes(MP3)

    async def go():
        voice = make_voice(service, cache, [])
        report = await voice.prewarm([("Agua", "es"), ("Water", "en"), ("Dolor", "es"), ("Water", "en")])
        await voice.aclose()
        return report

    report = asyncio.run(go())
    assert [json.loads(r.content)["text"] for r in service.requests] == ["Water", "Dolor"]
    assert (report.made, report.cached, report.failed, report.chars) == (2, 1, 0, len("Water") + len("Dolor"))


def test_prewarm_stops_when_switched_off(cache):
    service = ElevenLabs(status=402, body={"detail": {"status": "payment_required", "message": "no credit"}})

    async def go():
        voice = make_voice(service, cache, [])
        report = await voice.prewarm([("a", "en"), ("b", "en"), ("c", "en")])
        await voice.aclose()
        return report

    report = asyncio.run(go())
    assert len(service.requests) == 1
    assert (report.made, report.failed, report.stopped) == (0, 1, True)


def test_prewarm_does_nothing_without_elevenlabs(cache):
    report = asyncio.run(Voice(lambda m: None, tts=NullTTS(), cache=cache).prewarm([("a", "en")]))
    assert (report.made, report.cached, report.failed) == (0, 0, 0)


# --- warm: make audio in advance without saying it -------------------------------------


def test_warm_caches_without_saying_anything(cache):
    service = ElevenLabs()
    sent: list[Message] = []

    async def run() -> None:
        voice = make_voice(service, cache, sent)
        voice.warm("Mija, estoy bien.", "es")
        voice.warm("Mija, estoy bien.", "es")  # same text: one request
        await settle()
        voice.warm("Mija, estoy bien.", "es")  # cached now: no request
        voice.speak("Mija, estoy bien.", "es", "phrase")
        await voice.aclose()

    asyncio.run(run())
    assert len(service.requests) == 1
    (msg,) = sent  # only the later speak() said anything
    assert isinstance(msg, PlayAudio) and msg.cached is True


def test_warm_does_nothing_without_a_key(tmp_path):
    sent: list[Message] = []
    Voice(sent.append, tts=NullTTS(), cache=AudioCache(tmp_path)).warm("Hola.", "es")
    assert sent == []
