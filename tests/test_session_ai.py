"""Session with the AI layer: suggestions screen, "Other..." tile, prefetch, loading, fallbacks.

The AI is FakeProvider behind a real Suggester whose background jobs only run when the test calls
loop.run(), so "the result has not arrived yet" can be tested without real time or network.
"""

import asyncio
import json

import pytest

from core.clock import ManualScheduler
from core.contracts import ActionResult, Clench, Confirm, DoubleBlink, LongClench, PlayAudio, Screen, Settings, Speak
from core.db import Db
from core.menu import load_menu
from core.profile import load_profile
from core.session import CLENCH_DEBOUNCE_S, LOADING_MAX_S, OTHER_PAGES, Session, SessionState
from core.suggest.fake import FakeProvider
from core.suggest.provider import Bundle, Options, Sentences
from core.suggest.service import Suggester
from core.voice import Voice

SCAN_S = 1.0


@pytest.fixture(scope="module")
def menu():
    return load_menu()


@pytest.fixture(scope="module")
def profile(menu):
    return load_profile(menu.contacts)


class Loop:
    """Runs the Suggester's background jobs when the test says so."""

    def __init__(self):
        self.jobs = []

    def spawn(self, coro):
        self.jobs.append(coro)

    def run(self):
        while self.jobs:
            jobs, self.jobs = self.jobs, []
            for job in jobs:
                asyncio.run(job)

    def drop(self):
        for job in self.jobs:
            job.close()
        self.jobs = []


class RecordingVoice(Voice):
    """Browser-speech voice that also records what it was asked to make in advance."""

    def __init__(self, emit):
        super().__init__(emit)
        self.warmed = []

    def warm(self, text, lang):
        self.warmed.append((text, lang))


@pytest.fixture
def sched():
    return ManualScheduler(start=100.0)


@pytest.fixture
def sent():
    return []


@pytest.fixture
def loop():
    lp = Loop()
    yield lp
    lp.drop()


def run_now(coro) -> None:
    asyncio.run(coro)


def make_session(menu, profile, sched, sent, loop, provider=None, *, lang="en", db=None, timeout=4.0):
    voice = RecordingVoice(sent.append)
    suggester = Suggester(
        provider if provider is not None else FakeProvider(),
        patient_name="Luis",
        spawn=loop.spawn,
        local_hour=lambda: 12,
        history=db,
        timeout=timeout,
    )
    s = Session(
        menu, sent.append, sched, profile=profile, voice=voice, suggester=suggester, spawn=run_now, lang=lang, db=db
    )
    s.start()
    return s


@pytest.fixture
def fake():
    return FakeProvider()


@pytest.fixture
def session(menu, profile, sched, sent, loop, fake):
    return make_session(menu, profile, sched, sent, loop, fake)


@pytest.fixture
def plain(menu, profile, sched, sent):
    """No AI at all (no key): the default Suggester."""
    s = Session(menu, sent.append, sched, profile=profile, spawn=run_now, lang="en")
    s.start()
    return s


def last_screen(sent) -> Screen:
    return next(m for m in reversed(sent) if isinstance(m, Screen))


def labels(sent) -> list[str]:
    return [t.label for t in last_screen(sent).tiles]


def said(sent, kind: str) -> list[str]:
    return [m.text for m in sent if isinstance(m, (Speak, PlayAudio)) and m.kind == kind]


def pick(session, sched, sent, label: str) -> None:
    """Wait for the scan to reach the tile labelled `label`, then clench."""
    index = labels(sent).index(label)
    sched.advance(CLENCH_DEBOUNCE_S + 0.05 + index * SCAN_S)
    assert session.highlight == index
    session.handle(Clench(t=0.0, strength=1.0))


def confirm(session, sched) -> None:
    sched.advance(CLENCH_DEBOUNCE_S + 0.05)
    session.handle(Clench(t=0.0, strength=1.0))


# --- the "Other..." tile ----------------------------------------------------------------


def test_home_has_six_tiles_with_other_last(plain, sent):
    screen = last_screen(sent)
    assert [(t.id, t.kind) for t in screen.tiles] == [
        ("suggested", "branch"),
        ("need", "branch"),
        ("people", "branch"),
        ("feel", "branch"),
        ("room", "branch"),
        ("other", "other"),
    ]
    assert screen.tiles[-1].label == "Other..."


def test_other_is_always_the_last_tile(session, sched, sent, loop):
    loop.run()
    screens = []
    for tile in ["I need", "Pain", "Back"]:
        pick(session, sched, sent, tile)
        screens.append(last_screen(sent))
    loop.run()
    pick(session, sched, sent, "A lot")  # results are cached: the suggestions screen opens at once
    screens.append(last_screen(sent))
    pick(session, sched, sent, "Other...")
    screens.append(last_screen(sent))
    for screen in screens:
        assert screen.tiles[-1].kind == "other"
        assert [t.kind for t in screen.tiles].count("other") == 1
        assert len(screen.tiles) <= 6
    assert screens[3].screen == "suggestions"


def test_other_in_spanish(menu, profile, sched, sent, loop):
    make_session(menu, profile, sched, sent, loop, lang="es")
    assert labels(sent)[-1] == "Otro..."


def test_no_key_mode_end_to_end(plain, sched, sent):
    assert not plain.suggester.available
    # Home "Other...": the old "Say something" phrases.
    pick(plain, sched, sent, "Other...")
    assert labels(sent) == ["Yes", "No", "Good morning", "Wait a moment", "Other..."]
    assert last_screen(sent).path == ["Other"]
    # Second "Other..." in a row: nothing new without AI, so it loops back to home's own options.
    pick(plain, sched, sent, "Other...")
    assert labels(sent) == ["Suggested", "I need", "People", "How I feel", "Room", "Other..."]
    assert last_screen(sent).path == []
    assert plain.highlight == 0
    assert said(sent, "system") == []  # no "Spelling is coming soon"
    # A leaf goes straight to the confirm screen with its fixed phrase (no suggestions screen).
    for tile in ["I need", "Pain", "Back", "A lot"]:
        pick(plain, sched, sent, tile)
    assert sent[-1] == Confirm(text="My back hurts a lot. Can you help me turn over?", action="speak")
    confirm(plain, sched)
    assert said(sent, "phrase") == ["My back hurts a lot. Can you help me turn over?"]


def test_no_key_other_uses_the_fixed_more_list(plain, sched, sent):
    for tile in ["I need", "Pain"]:
        pick(plain, sched, sent, tile)
    pick(plain, sched, sent, "Other...")
    assert labels(sent) == ["Arms", "Other..."]
    pick(plain, sched, sent, "Arms")
    pick(plain, sched, sent, "A lot")
    assert sent[-1] == Confirm(text="My arms hurt a lot. Can you move them for me?", action="speak")


def test_no_key_suggested_shows_the_fixed_list(plain, sched, sent):
    pick(plain, sched, sent, "Suggested")
    assert labels(sent) == ["I'm hungry", "Water, please", "Turn me over", "Thank you, I love you", "How are you?", "Other..."]


def test_other_pages_then_loop_back_to_the_level(session, sched, sent, loop):
    loop.run()
    home = labels(sent)
    pages = []
    for n in range(1, OTHER_PAGES + 1):
        pick(session, sched, sent, "Other...")
        loop.run()
        pages.append(labels(sent))
        assert pages[-1][-1] == "Other..."  # never "Spell it"
        assert last_screen(sent).path == ["Other"] * n
        assert session.highlight == 0
    for a in range(len(pages)):
        for b in range(a):
            assert not set(pages[a][:-1]) & set(pages[b][:-1])  # every page is new
    # After 3 AI pages the next "Other..." loops back to home's own options.
    pick(session, sched, sent, "Other...")
    assert labels(sent) == home
    assert last_screen(sent).path == []
    assert session.highlight == 0
    # ...and the loop starts again.
    pick(session, sched, sent, "Other...")
    assert last_screen(sent).path == ["Other"]


def test_other_loops_back_when_the_ai_has_nothing_new(menu, profile, sched, sent, loop):
    class Empty(FakeProvider):
        async def more_options(self, ctx):
            return Options(options=[])

        async def level_bundle(self, ctx):
            return Bundle()

    s = make_session(menu, profile, sched, sent, loop, Empty())
    loop.run()
    for tile in ["I need", "Pain"]:
        pick(s, sched, sent, tile)
        loop.run()
    pick(s, sched, sent, "Other...")
    loop.run()
    assert labels(sent) == ["Arms", "Other..."]  # the AI had nothing: the fixed `more` list
    pick(s, sched, sent, "Other...")
    loop.run()
    assert last_screen(sent).path == ["I need", "Pain"]  # nothing new: back to Pain's own options
    assert labels(sent)[0] == "Head"
    assert said(sent, "system") == []


# --- prefetch and loading ---------------------------------------------------------------


def test_prefetch_does_not_block_scanning(session, sched, sent, loop, fake):
    assert loop.jobs  # home asked for Suggested and for "Other..." in the background
    assert fake.calls == []  # ...but nothing has run: the AI is slow
    for step in [1, 2, 3, 4, 5, 0]:
        sched.advance(SCAN_S)
        assert last_screen(sent).highlight == step  # the highlight moves on regardless
    pick(session, sched, sent, "I need")
    assert session.state is SessionState.SCANNING
    methods = sorted({m for m, _ in fake.calls})
    assert methods == []
    loop.run()
    # One request per level: home asked for Suggested (right now) and its "Other...", I need for
    # each of its leaves and its "Other...".
    home, need = fake.calls
    assert home[0] == "level_bundle" and home[1].path == () and home[1].now and home[1].options
    assert need[0] == "level_bundle" and need[1].path == ("I need",) and need[1].options and not need[1].now
    assert [leaf.label for leaf in need[1].leaves] == ["Water", "Food", "Bathroom"]


def test_pick_before_results_shows_loading_then_suggestions(session, sched, sent, loop):
    loop.run()
    pick(session, sched, sent, "I need")  # the prefetch for this level has not come back yet
    pick(session, sched, sent, "Water")
    assert session.state is SessionState.LOADING
    screen = last_screen(sent)
    assert screen.loading is True and screen.path == ["I need"]
    n = len(sent)
    sched.advance(3 * SCAN_S)
    assert len(sent) == n  # scanning paused while loading
    session.handle(Clench(t=0.0, strength=1.0))  # ignored while loading
    assert session.state is SessionState.LOADING
    loop.run()  # the sentences arrive
    screen = last_screen(sent)
    assert screen.screen == "suggestions" and screen.loading is False
    assert [t.kind for t in screen.tiles] == ["suggestion"] * 4 + ["other"]
    assert screen.tiles[3].label == "I'd like some water, please."  # the fixed phrase after the AI's
    assert screen.path == ["I need", "Water"]


def test_loading_gives_up_after_4_s_and_uses_the_fixed_phrase(session, sched, sent, loop):
    loop.run()
    pick(session, sched, sent, "I need")
    loop.drop()
    pick(session, sched, sent, "Water")
    sched.advance(LOADING_MAX_S - 0.1)
    assert session.state is SessionState.LOADING
    sched.advance(0.2)
    assert sent[-1] == Confirm(text="I'd like some water, please.", action="speak")


def test_double_blink_while_loading_goes_back_to_scanning(session, sched, sent, loop):
    loop.run()
    pick(session, sched, sent, "I need")
    loop.drop()
    pick(session, sched, sent, "Water")
    session.handle(DoubleBlink(t=0.0))
    assert session.state is SessionState.SCANNING
    assert last_screen(sent).loading is False and last_screen(sent).path == ["I need"]
    sched.advance(LOADING_MAX_S + 1)
    assert not any(isinstance(m, Confirm) for m in sent)  # the late fallback does nothing


def test_long_clench_while_loading_starts_help(session, sched, sent, loop):
    loop.run()
    pick(session, sched, sent, "I need")
    loop.drop()
    pick(session, sched, sent, "Water")
    session.handle(LongClench(t=0.0, duration=1.6))
    assert session.state is SessionState.HELP_COUNTDOWN
    session.handle(DoubleBlink(t=0.0))
    assert session.state is SessionState.SCANNING
    assert last_screen(sent).path == ["I need"]


class SlowFake(FakeProvider):
    async def compose(self, ctx):
        await asyncio.sleep(0.5)
        return await super().compose(ctx)

    async def level_bundle(self, ctx):
        await asyncio.sleep(0.5)
        return await super().level_bundle(ctx)


def test_ai_timeout_falls_back_to_the_fixed_phrase(menu, profile, sched, sent, loop):
    s = make_session(menu, profile, sched, sent, loop, SlowFake(), timeout=0.01)
    pick(s, sched, sent, "I need")
    pick(s, sched, sent, "Water")
    assert s.state is SessionState.LOADING
    loop.run()  # every compose times out
    assert sent[-1] == Confirm(text="I'd like some water, please.", action="speak")


# --- suggestions --------------------------------------------------------------------------


def test_suggested_shows_ai_sentences_first(session, sched, sent, loop):
    loop.run()
    pick(session, sched, sent, "Suggested")
    screen = last_screen(sent)
    assert [t.kind for t in screen.tiles] == ["suggestion"] * 3 + ["leaf", "leaf", "other"]
    assert screen.tiles[0].label == "Good afternoon. How are you?"  # FakeProvider at 12:00
    assert screen.tiles[0].id == "ai:suggested.s1"
    pick(session, sched, sent, "Good afternoon. How are you?")
    assert sent[-1] == Confirm(text="Good afternoon. How are you?", action="speak")


def test_picking_a_sentence_confirms_exactly_it(session, sched, sent, loop):
    loop.run()
    for tile in ["People", "Maria"]:
        pick(session, sched, sent, tile)
    loop.run()
    pick(session, sched, sent, "Text")
    sentence = labels(sent)[1]
    pick(session, sched, sent, sentence)
    assert sent[-1] == Confirm(text=sentence, action="send_message")
    assert said(sent, "phrase") == []  # D5: nothing said before the confirm clench
    confirm(session, sched)
    assert said(sent, "phrase") == [sentence]
    assert [m for m in sent if isinstance(m, ActionResult)] == [
        ActionResult(action="send_message", ok=True, detail="dry run", contact="Maria")
    ]


def test_double_blink_on_confirm_goes_back_to_the_suggestions(session, sched, sent, loop):
    loop.run()
    pick(session, sched, sent, "I need")
    loop.run()
    pick(session, sched, sent, "Water")
    first = labels(sent)[0]
    pick(session, sched, sent, first)
    session.handle(DoubleBlink(t=0.0))
    assert last_screen(sent).screen == "suggestions"
    session.handle(DoubleBlink(t=0.0))
    assert last_screen(sent).path == ["I need"]


def test_other_on_the_suggestions_screen_brings_more_sentences(session, sched, sent, loop):
    loop.run()
    pick(session, sched, sent, "I need")
    loop.run()
    pick(session, sched, sent, "Water")
    first = labels(sent)[:-1]
    loop.run()
    pick(session, sched, sent, "Other...")
    screen = last_screen(sent)
    assert screen.screen == "suggestions"
    assert all(t.kind == "suggestion" for t in screen.tiles[:-1])
    assert not set(first) & {t.label for t in screen.tiles}
    assert screen.path == ["I need", "Water", "Other"]


def test_echo_rules(session, sched, sent, loop):
    loop.run()
    pick(session, sched, sent, "I need")
    loop.run()
    pick(session, sched, sent, "Water")
    sentence = labels(sent)[0]
    pick(session, sched, sent, "Other...")
    loop.run()
    session.handle(DoubleBlink(t=0.0))  # up one level: I need
    pick(session, sched, sent, "Water")
    pick(session, sched, sent, sentence)
    assert said(sent, "echo") == ["I need", "Water", "Other", "Water"]  # never the sentence itself


def test_only_the_top_sentence_is_made_in_advance(session, sched, sent, loop):
    loop.run()
    pick(session, sched, sent, "I need")
    loop.run()
    pick(session, sched, sent, "Water")
    top, second = labels(sent)[:2]
    assert session._voice.warmed == [(top, "en")]
    pick(session, sched, sent, second)
    assert session._voice.warmed == [(top, "en"), (second, "en")]  # made once it is on the confirm screen


# --- the AI never chooses who or what ---------------------------------------------------


class Hijacker(FakeProvider):
    """Tries to smuggle in an action and a contact, and writes about someone else."""

    async def more_options(self, ctx):
        self.calls.append(("more_options", ctx))
        return Options.model_validate(
            {
                "options": [
                    {"label": "Call Carlos", "text": "Call Carlos now.", "action": "place_call", "contact": "carlos"},
                    {"label": "Love you", "text": "I love you.", "action": "help_alert"},
                ]
            }
        )

    async def compose(self, ctx):
        self.calls.append(("compose", ctx))
        return Sentences.model_validate({"sentences": ["Carlos, call me."], "action": "place_call", "contact": "carlos"})

    async def level_bundle(self, ctx):
        self.calls.append(("level_bundle", ctx))
        options = (await self.more_options(ctx)).options if ctx.options else []
        return Bundle.model_validate(
            {
                "leaves": [
                    {"id": leaf.id, "sentences": ["Carlos, call me."], "action": "place_call", "contact": "carlos"}
                    for leaf in ctx.leaves
                ],
                "options": [o.model_dump() for o in options],
                "action": "place_call",
            }
        )


def test_the_ai_cannot_change_action_or_contact(menu, profile, sched, sent, loop):
    s = make_session(menu, profile, sched, sent, loop, Hijacker())
    loop.run()
    for tile in ["People", "Maria"]:
        pick(s, sched, sent, tile)
    loop.run()
    pick(s, sched, sent, "Other...")
    assert labels(sent) == ["Call Carlos", "Love you", "Other..."]
    loop.run()
    pick(s, sched, sent, "Call Carlos")
    pick(s, sched, sent, "Carlos, call me.")
    # People > Maria: the AI option inherits (speak, maria) from the path, whatever the AI said.
    assert sent[-1] == Confirm(text="Carlos, call me.", action="speak")
    confirm(s, sched)
    assert [m for m in sent if isinstance(m, ActionResult)] == []  # speak only: nothing sent to Carlos

    s.handle(DoubleBlink(t=0.0))
    s.start()
    loop.run()
    for tile in ["People", "Maria"]:
        pick(s, sched, sent, tile)
    loop.run()
    pick(s, sched, sent, "Text")
    pick(s, sched, sent, "Carlos, call me.")
    assert sent[-1] == Confirm(text="Carlos, call me.", action="send_message")  # the leaf's action
    confirm(s, sched)
    (result,) = [m for m in sent if isinstance(m, ActionResult)]
    assert (result.action, result.contact) == ("send_message", "Maria")  # the leaf's contact


def test_room_options_inherit_room_control(session, sched, sent, loop):
    loop.run()
    pick(session, sched, sent, "Room")
    loop.run()
    pick(session, sched, sent, "Other...")
    option = labels(sent)[0]
    loop.run()
    pick(session, sched, sent, option)
    pick(session, sched, sent, labels(sent)[0])
    assert isinstance(sent[-1], Confirm) and sent[-1].action == "room_control"


# --- records and language ---------------------------------------------------------------


def test_ai_options_are_logged_with_the_ai_prefix(menu, profile, sched, sent, loop, tmp_path):
    db = Db(tmp_path / "t.db")
    db.sync_profile("Luis", "en", [])
    s = make_session(menu, profile, sched, sent, loop, db=db)
    loop.run()
    for tile in ["I need", "Pain"]:
        pick(s, sched, sent, tile)
    loop.run()
    pick(s, sched, sent, "Other...")
    option = labels(sent)[0]
    loop.run()
    pick(s, sched, sent, option)
    sentence = labels(sent)[0]
    pick(s, sched, sent, sentence)
    confirm(s, sched)
    rows = [(r["node_id"], r["confirmed"], r["text"], json.loads(r["path"])) for r in db.events()]
    slug = option.lower().replace(" ", "_")
    assert rows == [
        ("need", 0, None, ["I need"]),
        ("need.pain", 0, None, ["I need", "Pain"]),
        ("need.pain.other", 0, None, ["I need", "Pain", "Other"]),
        (f"ai:need.pain.{slug}", 0, None, ["I need", "Pain", "Other", option]),
        (f"ai:need.pain.{slug}", 0, None, ["I need", "Pain", "Other", option]),  # the sentence's pick
        (f"ai:need.pain.{slug}", 1, sentence, ["I need", "Pain", "Other", option]),
    ]
    db.close()


def test_history_reaches_the_ai(menu, profile, sched, sent, loop, fake, tmp_path):
    db = Db(tmp_path / "t.db")
    db.sync_profile("Luis", "en", [])
    db.use_phrase("Honey, come here.", "en")
    s = make_session(menu, profile, sched, sent, loop, fake, db=db)
    loop.run()
    assert all(c.top_phrases == ("Honey, come here.",) for _, c in fake.calls)
    assert all(c.patient_name == "Luis" and c.hour == 12 for _, c in fake.calls)
    s.stop()
    db.close()


def test_language_switch_drops_ai_screens(session, sched, sent, loop):
    loop.run()
    pick(session, sched, sent, "I need")
    loop.run()
    pick(session, sched, sent, "Water")
    assert last_screen(sent).screen == "suggestions"
    session.handle(Settings(pointing_mode="auto", scan_ms=1000, lang="es"))
    screen = last_screen(sent)
    assert screen.screen == "menu" and screen.path == ["Necesito"]
    assert screen.tiles[-1].label == "Otro..."
