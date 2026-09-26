"""The one-clench Suggested shortcut and Day 1 mode (learning off)."""

import asyncio
import json
from datetime import datetime

import httpx
import pytest

from core.clock import ManualScheduler
from core.contracts import Clench, Confirm, DoubleBlink, Screen, Settings, Speak
from core.db import Db
from core.menu import load_menu
from core.profile import load_profile
from core.rank import Ranker
from core.rank.jev import JevRanker, TypeSafeAccess
from core.rank.score import DAY_S
from core.session import CLENCH_DEBOUNCE_S, Session, SessionState
from core.suggest.fake import FakeProvider
from core.suggest.service import Suggester

NOW = datetime(2026, 9, 26, 18, 30).timestamp()
SCAN_S = 1.0
MARIA = "Honey, I'm okay, call me at six."
WATER = "I'd like some water, please."
DAYS = [NOW - d * DAY_S for d in range(7)]
YAML_HOME = ["suggested", "need", "people", "feel", "room", "other"]


@pytest.fixture(scope="module")
def menu():
    return load_menu()


@pytest.fixture(scope="module")
def profile(menu):
    return load_profile(menu.contacts)


@pytest.fixture
def db():
    d = Db(":memory:")
    d.sync_profile("Luis", "en", [])
    yield d
    d.close()


def confirm_many(db, node_id, text, times, *, action="speak", contact=None):
    for t in times:
        db.log_event(node_id=node_id, path=[], action=action, lang="en", confirmed=True, text=text, contact=contact, t=t)
        db.use_phrase(text, "en", t=t)


def maria_every_evening(db, per_day=2):
    confirm_many(db, "people.maria.text", MARIA, DAYS * per_day, action="send_message", contact="maria")


class Loop:
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


@pytest.fixture
def loop():
    lp = Loop()
    yield lp
    lp.drop()


def make(menu, profile, db, loop, *, learning=True, ai=False, jev=None):
    sent: list = []
    suggester = None
    if ai:
        suggester = Suggester(FakeProvider(), patient_name="Luis", spawn=loop.spawn, local_hour=lambda: 18, history=db)
    s = Session(
        menu, sent.append, ManualScheduler(start=100.0), profile=profile, spawn=asyncio.run, lang="en", db=db,
        learning=learning, ranker=Ranker(db, clock=lambda: NOW), suggester=suggester, jev=jev,
    )
    s.start()
    return s, sent


def last_screen(sent) -> Screen:
    return next(m for m in reversed(sent) if isinstance(m, Screen))


def labels(sent) -> list[str]:
    return [t.label for t in last_screen(sent).tiles]


def pick(session, sent, label):
    index = labels(sent).index(label)
    session._scheduler.advance(CLENCH_DEBOUNCE_S + 0.05 + index * SCAN_S)
    assert session.highlight == index
    session.handle(Clench(t=0.0, strength=1.0))


def confirm(session):
    session._scheduler.advance(CLENCH_DEBOUNCE_S + 0.05)
    session.handle(Clench(t=0.0, strength=1.0))


# --- the shortcut ----------------------------------------------------------------------------


def test_confident_history_goes_straight_to_confirm(menu, profile, db, loop):
    maria_every_evening(db)
    s, sent = make(menu, profile, db, loop)
    pick(s, sent, "Suggested")
    assert sent[-1] == Confirm(text=MARIA, action="send_message")
    assert s.state is SessionState.CONFIRMING
    assert not any(isinstance(m, Speak) and m.kind == "phrase" for m in sent)  # D5: nothing said yet
    confirm(s)  # the confirm clench is still required
    assert [m.text for m in sent if isinstance(m, Speak) and m.kind == "phrase"] == [MARIA]


def test_double_blink_on_the_shortcut_opens_the_suggested_list(menu, profile, db, loop):
    maria_every_evening(db)
    s, sent = make(menu, profile, db, loop)
    pick(s, sent, "Suggested")
    s.handle(DoubleBlink(t=0.0))
    screen = last_screen(sent)
    assert s.state is SessionState.SCANNING and screen.path == ["Suggested"]  # not home
    assert screen.tiles[0].label == MARIA and len(screen.tiles) == 6
    rejected = [r for r in db.events() if r["rejected"]]
    assert [r["text"] for r in rejected] == [MARIA]  # the wrong guess is recorded
    s.handle(DoubleBlink(t=0.0))
    assert last_screen(sent).path == []  # and one more goes home


def test_double_blink_on_a_normal_confirm_still_goes_back(menu, profile, db, loop):
    maria_every_evening(db)
    s, sent = make(menu, profile, db, loop)
    for tile in ["I need", "Water"]:
        pick(s, sent, tile)
    assert isinstance(sent[-1], Confirm)
    s.handle(DoubleBlink(t=0.0))
    assert last_screen(sent).path == ["I need"]


def test_no_shortcut_below_the_threshold(menu, profile, db, loop):
    # María in the evening, but water just as often at this hour: not a confident guess.
    maria_every_evening(db, per_day=1)
    confirm_many(db, "need.water", WATER, DAYS)
    s, sent = make(menu, profile, db, loop)
    pick(s, sent, "Suggested")
    assert s.state is SessionState.SCANNING and last_screen(sent).path == ["Suggested"]


def test_no_shortcut_with_too_little_history(menu, profile, db, loop):
    confirm_many(db, "people.maria.text", MARIA, [NOW - 600], action="send_message", contact="maria")
    s, sent = make(menu, profile, db, loop)
    pick(s, sent, "Suggested")
    assert last_screen(sent).path == ["Suggested"]  # one use is not a habit


def test_no_shortcut_in_day1_mode(menu, profile, db, loop):
    maria_every_evening(db)
    s, sent = make(menu, profile, db, loop, learning=False)
    pick(s, sent, "Suggested")
    assert s.state is SessionState.SCANNING
    assert labels(sent) == ["I'm hungry", "Water, please", "Turn me over", "Thank you, I love you", "How are you?", "Other..."]


def test_a_recent_cancel_turns_the_shortcut_off(menu, profile, db, loop):
    maria_every_evening(db)
    s, sent = make(menu, profile, db, loop)
    pick(s, sent, "Suggested")
    s.handle(DoubleBlink(t=0.0))  # cancelled the guess
    s.handle(DoubleBlink(t=0.0))
    pick(s, sent, "Suggested")
    assert s.state is SessionState.SCANNING  # the list, not the same guess again


def jev_for(choice, confidence):
    def handler(request):
        criteria = json.loads(request.content)["questions"]["next"]["criteria"]
        probs = {k: (0.9 if k == choice else 0.01) for k in criteria}
        answer = {"type": "choice", "choice": choice, "probabilities": probs, "confidence": confidence}
        return httpx.Response(200, json={"answers": {"next": answer}})

    return handler


def test_jev_confidence_decides_when_jev_is_on(menu, profile, db, loop):
    maria_every_evening(db)
    jev = JevRanker(TypeSafeAccess("k"), spawn=loop.spawn, transport=httpx.MockTransport(jev_for("people.maria.text", 0.9)))
    s, sent = make(menu, profile, db, loop, jev=jev)
    loop.run()  # Jev answered about the Suggested phrases while home was showing
    pick(s, sent, "Suggested")
    assert sent[-1] == Confirm(text=MARIA, action="send_message")


def with_jev(menu, profile, db, loop, choice, confidence):
    jev = JevRanker(TypeSafeAccess("k"), spawn=loop.spawn, transport=httpx.MockTransport(jev_for(choice, confidence)))
    s, sent = make(menu, profile, db, loop, jev=jev)
    loop.run()  # Jev answered about the Suggested phrases while home was showing
    pick(s, sent, "Suggested")
    return s, sent


def test_confident_jev_is_enough_even_with_little_history(menu, profile, db, loop):
    confirm_many(db, "people.maria.text", MARIA, [NOW - 600], action="send_message", contact="maria")
    s, sent = with_jev(menu, profile, db, loop, "people.maria.text", 0.85)
    assert sent[-1] == Confirm(text=MARIA, action="send_message")


def test_jev_agreeing_with_a_confident_history_is_enough(menu, profile, db, loop):
    maria_every_evening(db)
    s, sent = with_jev(menu, profile, db, loop, "people.maria.text", 0.5)  # Jev's usual calibrated 0.5
    assert sent[-1] == Confirm(text=MARIA, action="send_message")


def test_jev_disagreeing_means_no_shortcut(menu, profile, db, loop):
    maria_every_evening(db)  # the history alone would be confident
    s, sent = with_jev(menu, profile, db, loop, "suggested.water", 0.5)
    assert s.state is SessionState.SCANNING and last_screen(sent).path == ["Suggested"]


def test_unsure_jev_and_little_history_means_no_shortcut(menu, profile, db, loop):
    confirm_many(db, "people.maria.text", MARIA, [NOW - 600], action="send_message", contact="maria")
    s, sent = with_jev(menu, profile, db, loop, "people.maria.text", 0.6)
    assert s.state is SessionState.SCANNING


# --- Day 1 mode from SETTINGS ------------------------------------------------------------------


def test_learning_setting_switches_day1_mode_live(menu, profile, db, loop):
    maria_every_evening(db, per_day=3)
    confirm_many(db, "room.tv.on", "Please turn on the TV.", DAYS * 3, action="room_control")
    s, sent = make(menu, profile, db, loop)
    learned = [t.id for t in last_screen(sent).tiles]
    assert learned != YAML_HOME and learned[0] == "suggested" and learned[-1] == "other"

    s.handle(Settings(pointing_mode="auto", scan_ms=1000, learning=False))
    assert Settings(pointing_mode="auto", scan_ms=1000, lang="en", speak_picks=True, learning=False) in sent
    assert [t.id for t in last_screen(sent).tiles] == YAML_HOME  # back home, menu.yaml order
    assert not s.suggester.use_history

    s.handle(Settings(pointing_mode="auto", scan_ms=1000, learning=True))
    assert [t.id for t in last_screen(sent).tiles] == learned
    assert s.settings().learning is True


def test_day1_mode_asks_the_ai_without_history_or_suggested_sentences(menu, profile, db, loop):
    maria_every_evening(db)
    s, sent = make(menu, profile, db, loop, learning=False, ai=True)
    fake = s.suggester.provider
    loop.run()
    (method, ctx), = fake.calls
    assert method == "more_options"  # home: only its "Other...", no "right now" sentences
    assert ctx.top_phrases == () and ctx.recent_messages == ()
    pick(s, sent, "Suggested")
    assert all(t.kind in ("leaf", "other") for t in last_screen(sent).tiles)  # the fixed list


def test_learning_default_comes_from_the_profile(menu, profile, db, loop):
    s, _ = make(menu, profile.model_copy(update={"learning": False}), db, loop, learning=None)
    assert s.learning is False and s.settings().learning is False
