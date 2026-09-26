"""METRICS: clenches and scan steps per confirmed message, and the Day 1 comparison."""

import asyncio
from datetime import datetime

import pytest

from core.clock import ManualScheduler
from core.contracts import Clench, DoubleBlink, Metrics, Screen, Settings
from core.db import Db
from core.hub import Hub
from core.menu import load_menu
from core.metrics import day1_cost
from core.profile import load_profile
from core.rank import Ranker
from core.rank.score import DAY_S
from core.session import CLENCH_DEBOUNCE_S, SPEAK_TIMEOUT_S, Session
from core.suggest.fake import FakeProvider
from core.suggest.service import Suggester

NOW = datetime(2026, 9, 26, 18, 30).timestamp()
SCAN_S = 1.0
MARIA = "Honey, I'm okay, call me at six."


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


def seed_maria(db):
    for d in range(7):
        for extra in (0, 600):
            t = NOW - d * DAY_S - extra
            db.log_event(node_id="people.maria.text", path=[], action="send_message", lang="en", confirmed=True, text=MARIA, contact="maria", t=t)


LOOPS: list = []


@pytest.fixture(autouse=True)
def close_leftover_jobs():
    yield
    for loop in LOOPS:
        for job in loop.jobs:
            job.close()
    LOOPS.clear()


class Loop:
    def __init__(self):
        self.jobs = []
        LOOPS.append(self)

    def spawn(self, coro):
        self.jobs.append(coro)

    def run(self):
        while self.jobs:
            jobs, self.jobs = self.jobs, []
            for job in jobs:
                asyncio.run(job)


def make(menu, profile, db, *, learning, ai=False):
    sent: list = []
    loop = Loop()
    suggester = Suggester(FakeProvider(), patient_name="Luis", spawn=loop.spawn, local_hour=lambda: 18, history=db) if ai else None
    s = Session(
        menu, sent.append, ManualScheduler(start=100.0), profile=profile, spawn=asyncio.run, lang="en", db=db,
        learning=learning, ranker=Ranker(db, clock=lambda: NOW), suggester=suggester,
    )
    s.start()
    return s, sent, loop


def labels(sent):
    return [t.label for t in next(m for m in reversed(sent) if isinstance(m, Screen)).tiles]


def pick(s, sent, label):
    index = labels(sent).index(label)
    s._scheduler.advance(CLENCH_DEBOUNCE_S + 0.05 + index * SCAN_S)
    s.handle(Clench(t=0.0, strength=1.0))


def confirm(s):
    s._scheduler.advance(CLENCH_DEBOUNCE_S + 0.05)
    s.handle(Clench(t=0.0, strength=1.0))


def metrics(sent) -> list[Metrics]:
    return [m for m in sent if isinstance(m, Metrics)]


def test_day1_mode_without_ai(menu, profile, db):
    s, sent, _ = make(menu, profile, db, learning=False)
    for tile in ["People", "Maria", "Text"]:
        pick(s, sent, tile)
    confirm(s)
    # People is tile 2 (2 steps), Maria tile 0, Text tile 1: 3 steps; 3 picks + the confirm.
    assert metrics(sent) == [Metrics(text=MARIA, selections=4, scan_steps=3, day1_selections=4, day1_scan_steps=3)]


def test_day1_mode_with_ai(menu, profile, db):
    s, sent, loop = make(menu, profile, db, learning=False, ai=True)
    for tile in ["People", "Maria"]:
        pick(s, sent, tile)
    loop.run()
    pick(s, sent, "Text")
    pick(s, sent, MARIA)  # after the AI's 3 sentences: 3 more steps
    confirm(s)
    assert metrics(sent) == [Metrics(text=MARIA, selections=5, scan_steps=6, day1_selections=5, day1_scan_steps=6)]


def test_after_a_week_the_shortcut_makes_it_two_clenches(menu, profile, db):
    seed_maria(db)
    s, sent, loop = make(menu, profile, db, learning=True, ai=True)
    pick(s, sent, "Suggested")  # tile 0: no waiting
    confirm(s)  # the shortcut opened the confirm screen at once
    assert metrics(sent) == [Metrics(text=MARIA, selections=2, scan_steps=0, day1_selections=5, day1_scan_steps=6)]


def test_mistakes_and_waiting_count(menu, profile, db):
    s, sent, _ = make(menu, profile, db, learning=False)
    pick(s, sent, "I need")  # wrong branch
    s.handle(DoubleBlink(t=0.0))
    s._scheduler.advance(6 * SCAN_S)  # a full lap missed
    pick(s, sent, "People")
    for tile in ["Maria", "Text"]:
        pick(s, sent, tile)
    confirm(s)
    (m,) = metrics(sent)
    assert m.selections == 5 and m.scan_steps == 1 + 6 + 2 + 0 + 1
    assert (m.day1_selections, m.day1_scan_steps) == (4, 3)


def test_counts_start_again_from_home(menu, profile, db):
    s, sent, _ = make(menu, profile, db, learning=False)
    for tile in ["People", "Maria", "Text"]:
        pick(s, sent, tile)
    confirm(s)
    s._scheduler.advance(SPEAK_TIMEOUT_S)  # speaking times out, back home
    for tile in ["I need", "Water"]:
        pick(s, sent, tile)
    confirm(s)
    assert [(m.selections, m.scan_steps) for m in metrics(sent)] == [(4, 3), (3, 1)]


def test_day1_cost_paths(menu):
    cost = lambda **kw: day1_cost(menu, lang="en", **kw)  # noqa: E731
    back = cost(event_id="need.pain.back.a_lot", item_id="need.pain.back.a_lot", text="My back hurts a lot. Can you help me turn over?", ai_on=False)
    assert (back.selections, back.scan_steps) == (5, 1 + 3 + 1 + 1)
    arms = cost(event_id="need.pain.arms.a_lot", item_id="need.pain.arms.a_lot", text="My arms hurt a lot. Can you move them for me?", ai_on=False)
    assert (arms.selections, arms.scan_steps) == (6, 1 + 3 + 5 + 0 + 1)  # through "Other..." (tile 5)
    ai_sentence = cost(event_id="ai:people.maria.text", item_id="ai:people.maria.text.s2", text="Mija, todo bien.", ai_on=True)
    assert (ai_sentence.selections, ai_sentence.scan_steps) == (5, 2 + 0 + 1 + 1)
    assert cost(event_id="ai:suggested", item_id="ai:suggested.s1", text="Good evening.", ai_on=True) is None
    assert cost(event_id="ai:need.pain.brazos", item_id="ai:need.pain.brazos", text="x", ai_on=True) is None


def test_metrics_go_to_consoles_and_the_dev_panel_only():
    hub = Hub()
    class FakeClient:
        def __init__(self, role):
            self.role = role
            self.queue = asyncio.Queue()

    clients = {role: FakeClient(role) for role in ("board", "console", "input")}
    for c in clients.values():
        hub.add(c)
    hub.broadcast(Metrics(text="x", selections=2, scan_steps=0, day1_selections=5, day1_scan_steps=6))
    assert {r: c.queue.qsize() for r, c in clients.items()} == {"board": 0, "console": 1, "input": 1}
    hub.broadcast(Settings(pointing_mode="scan", scan_ms=1000))
    assert clients["board"].queue.qsize() == 1 and clients["input"].queue.qsize() == 2
