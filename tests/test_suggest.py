"""AI provider layer: validation, providers, Suggester (timeout, cache, fallback). No real network."""

import asyncio
import json
from types import SimpleNamespace

import pytest
from google.genai import errors, types

from core.db import Db
from core.suggest import build_provider, prompts
from core.suggest.errors import ProviderError
from core.suggest.fake import FakeProvider
from core.suggest.gemini import DEFAULT_MODEL, GeminiProvider
from core.suggest.provider import MAX_TEXT, Option, Options, Sentences, SuggestContext
from core.suggest.service import Pending, Suggester


def ctx(**overrides) -> SuggestContext:
    base = dict(path=("I need", "Pain"), lang="en", hour=12, patient_name="Luis")
    base.update(overrides)
    return SuggestContext(**base)


# --- validation -----------------------------------------------------------------------


def test_sentences_drop_empty_long_and_duplicate_items():
    s = Sentences.model_validate(
        {"sentences": ["  My back hurts. ", "", "   ", "x" * (MAX_TEXT + 1), "my back hurts", 42, "Turn me over.", "A.", "B."]}
    )
    assert s.sentences == ["My back hurts.", "Turn me over.", "A."]  # cleaned, deduped, at most 3


def test_options_drop_bad_items_and_ignore_action_and_contact():
    o = Options.model_validate(
        {
            "options": [
                {"label": "Arms", "text": "My arms hurt.", "action": "place_call", "contact": "carlos"},
                {"label": "", "text": "Empty label."},
                {"label": "Neck", "text": ""},
                {"label": "arms", "text": "Duplicate label."},
                {"label": "Knees", "text": "k" * (MAX_TEXT + 1)},
                {"label": "L" * 41, "text": "Label too long."},
                "not an object",
                {"label": "Neck"},
                {"label": "Neck", "text": "My neck hurts."},
            ]
        }
    )
    assert o.options == [Option(label="Arms", text="My arms hurt."), Option(label="Neck", text="My neck hurts.")]
    assert "action" not in o.options[0].model_dump()


def test_wrong_shape_fails():
    with pytest.raises(ValueError):
        Sentences.model_validate_json('{"sentences": "one string"}')
    with pytest.raises(ValueError):
        Options.model_validate_json('{"choices": []}')


# --- prompts --------------------------------------------------------------------------


def test_prompts_carry_the_rules_and_only_the_context():
    c = ctx(
        lang="es",
        path=("Personas", "María", "Mensaje"),
        fixed_phrase="Mija, estoy bien, llámame a las seis.",
        top_phrases=("Mija, ven un momento.",),
        recent_messages=("Gracias, mija.",),
        contacts=("María", "Carlos"),
        shown=("Ya en pantalla.",),
    )
    p = prompts.compose(c)
    assert "ONLY in Spanish" in p.system and "First person" in p.system and "12 words" in p.system
    for part in ["Personas > María > Mensaje", "Mija, estoy bien", "Mija, ven un momento.", "Gracias, mija.", "María, Carlos", "12:00", "Ya en pantalla."]:
        assert part in p.user
    m = prompts.more_options(ctx(path=("I need", "Pain"), shown=("Head", "Back")))
    assert "ONLY in English" in m.system
    assert "under Pain the options are other body parts" in m.user
    assert "- Head" in m.user and '"label"' in m.user
    assert "home screen" in prompts.compose(ctx(path=())).user


# --- providers ------------------------------------------------------------------------


def test_build_provider():
    assert build_provider({}) == (None, "GEMINI_API_KEY missing")  # gemini is the default
    assert build_provider({"LLM_PROVIDER": "gemini", "GEMINI_API_KEY": " "})[0] is None
    provider, why = build_provider({"LLM_PROVIDER": "fake"})
    assert isinstance(provider, FakeProvider) and why == ""
    provider, why = build_provider({"GEMINI_API_KEY": "k", "GEMINI_MODEL": "gemini-x"})
    assert isinstance(provider, GeminiProvider) and provider.model == "gemini-x"
    assert build_provider({"GEMINI_API_KEY": "k"})[0].model == DEFAULT_MODEL
    assert "not built yet" in build_provider({"LLM_PROVIDER": "claude"})[1]
    assert "not built yet" in build_provider({"LLM_PROVIDER": "openai"})[1]
    assert "unknown" in build_provider({"LLM_PROVIDER": "llama"})[1]


def test_fake_provider_is_deterministic_and_avoids_what_is_shown():
    fake = FakeProvider()
    a = asyncio.run(fake.compose(ctx()))
    b = asyncio.run(fake.compose(ctx()))
    assert a == b and len(a.sentences) == 3
    assert asyncio.run(fake.compose(ctx(shown=tuple(a.sentences)))).sentences[0] not in a.sentences
    es = asyncio.run(fake.more_options(ctx(lang="es", shown=("Sí", "No"))))
    assert [o.label for o in es.options] == ["Espera", "Gracias", "Luego", "No sé", "Otra vez"]


class FakeGeminiClient:
    """Stands in for genai.Client: records calls, answers with canned JSON or raises."""

    def __init__(self, reply=None, error=None):
        self.requests = []
        self._reply = reply
        self._error = error
        self.aio = SimpleNamespace(models=SimpleNamespace(generate_content=self._generate))

    async def _generate(self, *, model, contents, config):
        self.requests.append(SimpleNamespace(model=model, contents=contents, config=config))
        if self._error is not None:
            raise self._error
        return SimpleNamespace(text=self._reply, candidates=[SimpleNamespace(finish_reason="SAFETY")])


def test_gemini_asks_for_structured_json_at_low_temperature():
    client = FakeGeminiClient(json.dumps({"sentences": ["Me duele la espalda.", "", "Me duele la espalda."]}))
    g = GeminiProvider("key", "gemini-test", client=client)
    out = asyncio.run(g.compose(ctx(lang="es")))
    assert out.sentences == ["Me duele la espalda."]
    (req,) = client.requests
    assert req.model == "gemini-test"
    cfg: types.GenerateContentConfig = req.config
    assert cfg.response_mime_type == "application/json"
    assert cfg.response_schema is not None
    assert cfg.temperature is not None and cfg.temperature <= 0.3
    assert "ONLY in Spanish" in cfg.system_instruction
    assert "I need > Pain" in req.contents

    client = FakeGeminiClient(json.dumps({"options": [{"label": "Arms", "text": "My arms hurt.", "contact": "carlos"}]}))
    out = asyncio.run(GeminiProvider("key", client=client).more_options(ctx()))
    assert out.options == [Option(label="Arms", text="My arms hurt.")]


@pytest.mark.parametrize(
    ("client", "pause"),
    [
        (FakeGeminiClient(error=errors.ClientError(429, {"error": {"message": "quota", "status": "RESOURCE_EXHAUSTED"}})), 60.0),
        (FakeGeminiClient(error=errors.ClientError(400, {"error": {"message": "bad", "status": "INVALID_ARGUMENT"}})), None),
        (FakeGeminiClient(reply=None), None),  # blocked: no text
        (FakeGeminiClient(reply="not json"), None),
    ],
)
def test_gemini_errors_become_provider_errors(client, pause):
    with pytest.raises(ProviderError) as e:
        asyncio.run(GeminiProvider("key", client=client).compose(ctx()))
    assert e.value.pause_s == pause


# --- Suggester --------------------------------------------------------------------------


class Loop:
    """Runs the Suggester's background jobs when the test says so."""

    def __init__(self):
        self.jobs = []

    def spawn(self, coro):
        self.jobs.append(coro)

    def run(self):
        jobs, self.jobs = self.jobs, []
        for job in jobs:
            asyncio.run(job)


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def make(provider, **kw) -> tuple[Suggester, Loop, Clock]:
    loop, clock = Loop(), Clock()
    s = Suggester(provider, patient_name="Luis", spawn=loop.spawn, clock=clock, local_hour=lambda: 12, **kw)
    return s, loop, clock


def test_no_provider_means_no_ai():
    s, loop, _ = make(None, off_reason="GEMINI_API_KEY missing")
    assert not s.available
    p = s.compose(("I need",), "en")
    assert p.done and p.result is None and loop.jobs == []
    assert s.describe() == "off (GEMINI_API_KEY missing): fixed phrases only"


def test_result_arrives_later_and_is_cached():
    fake = FakeProvider()
    s, loop, clock = make(fake)
    p = s.compose(("I need", "Water"), "en", fixed_phrase="I'd like some water, please.")
    assert not p.done  # never blocks: the caller gets a Pending
    same = s.compose(("I need", "Water"), "en", fixed_phrase="I'd like some water, please.")
    assert same is p  # one request shared
    got = []
    p.on_done(got.append)
    loop.run()
    assert p.done and len(got[0]) == 3
    assert len(fake.calls) == 1

    hit = s.compose(("I need", "Water"), "en", fixed_phrase="I'd like some water, please.")
    assert hit.done and hit.result == got[0]
    assert len(fake.calls) == 1 and loop.jobs == []  # cache hit: no call

    s.compose(("I need", "Water"), "es", fixed_phrase="Quiero agua, por favor.")  # other language: new call
    loop.run()
    assert len(fake.calls) == 2
    clock.t += 601  # after 10 minutes the cache entry is stale
    s.compose(("I need", "Water"), "en", fixed_phrase="I'd like some water, please.")
    loop.run()
    assert len(fake.calls) == 3


class SlowProvider(FakeProvider):
    async def compose(self, ctx):
        await asyncio.sleep(1.0)
        return await super().compose(ctx)


def test_timeout_falls_back_and_is_not_cached(caplog):
    slow = SlowProvider()
    s, loop, _ = make(slow, timeout=0.05)
    p = s.compose(("I need",), "en")
    loop.run()
    assert p.done and p.result is None
    assert "timed out" in caplog.text
    s.compose(("I need",), "en")
    assert loop.jobs  # a failure is not cached: asked again next time
    loop.run()


class BrokenProvider(FakeProvider):
    def __init__(self, error):
        super().__init__()
        self.error = error

    async def more_options(self, ctx):
        self.calls.append(("more_options", ctx))
        raise self.error


def test_errors_fall_back_and_rate_limit_pauses_the_ai(caplog):
    s, loop, clock = make(BrokenProvider(ValueError("boom")))
    p = s.more_options(("I need",), "en")
    loop.run()
    assert p.result is None and "boom" in caplog.text and s.available

    s, loop, clock = make(BrokenProvider(ProviderError("Gemini error 429", pause_s=60)))
    s.more_options(("I need",), "en")
    loop.run()
    assert not s.available and "(paused)" in s.describe()
    assert s.more_options(("Room",), "en").result is None and loop.jobs == []
    clock.t += 61
    assert s.available


def test_results_never_repeat_what_is_on_screen():
    s, loop, _ = make(FakeProvider())
    p = s.more_options((), "en", shown=("Suggested", "I need", "yes", "No"))
    loop.run()
    assert [o.label for o in p.result] == ["Wait", "Thank you", "Later", "Not sure", "Again"]


def test_context_is_the_request_plus_a_short_history(tmp_path):
    db = Db(tmp_path / "t.db")
    db.sync_profile("Luis", "es", [])
    for _ in range(3):
        db.use_phrase("Mija, ven un momento.", "es")
    db.use_phrase("Tengo sed.", "es")
    db.use_phrase("I'm thirsty.", "en")
    db.log_event(node_id="x", path=["X"], action="speak", lang="es", confirmed=True, text="Tengo sed.")
    db.log_event(node_id="help", path=["Ayuda"], action="help_alert", lang="es", confirmed=True, text="Luis necesita ayuda")
    fake = FakeProvider()
    s, loop, _ = make(fake, history=db, contacts={"es": ("María", "Carlos")})
    s.compose(("Necesito",), "es", fixed_phrase="Necesito algo.")
    loop.run()
    (_, c), = fake.calls
    assert c == SuggestContext(
        path=("Necesito",),
        lang="es",
        hour=12,
        patient_name="Luis",
        fixed_phrase="Necesito algo.",
        top_phrases=("Mija, ven un momento.", "Tengo sed."),
        recent_messages=("Tengo sed.",),
        contacts=("María", "Carlos"),
        shown=(),
    )
    db.close()


def test_pending_callbacks():
    p: Pending[int] = Pending()
    got = []
    p.on_done(got.append)
    p.resolve(1)
    p.resolve(2)  # only the first counts
    p.on_done(got.append)  # already done: runs at once
    assert got == [1, 1]
