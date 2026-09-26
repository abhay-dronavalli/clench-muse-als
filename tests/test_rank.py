"""Ranking (PRD section 9 layer 2): score math, the stability rule, and how the session orders screens."""

import asyncio
from datetime import datetime

import pytest

from core.clock import ManualScheduler
from core.contracts import Clench, Confirm, Screen
from core.db import Db
from core.menu import load_menu
from core.profile import load_profile
from core.rank import Entry, Ranker, Weights
from core.rank.score import (
    DAY_S,
    Candidate,
    Evidence,
    Use,
    beats,
    decay,
    full_order,
    hour_match,
    hour_weight,
    recency_count,
    score_all,
    stable_order,
    state_fit,
)
from core.session import CLENCH_DEBOUNCE_S, Session

NOW = datetime(2026, 9, 26, 18, 30).timestamp()  # a Saturday, 18:30 local time
HOUR = 18
SCAN_S = 1.0


def hours_ago(h: float) -> float:
    return NOW - h * 3600


def uses(*ages_days: float, hour: int = HOUR) -> list[Use]:
    return [Use(NOW - a * DAY_S, hour) for a in ages_days]


# --- score math ---------------------------------------------------------------------------


def test_recency_decay_halves_every_three_days():
    assert decay(0) == 1.0
    assert decay(3 * DAY_S) == pytest.approx(0.5)
    assert decay(6 * DAY_S) == pytest.approx(0.25)
    assert decay(-5) == 1.0  # a clock step backwards never counts more than now
    assert recency_count(uses(0, 3, 6), NOW) == pytest.approx(1.75)


def test_hour_smoothing_counts_neighbours_at_half_weight():
    assert [hour_weight(h, 18) for h in (16, 17, 18, 19, 20)] == [0, 0.5, 1.0, 0.5, 0]
    assert hour_weight(23, 0) == 0.5 and hour_weight(0, 23) == 0.5  # midnight wraps
    at = uses(0, hour=18) + uses(0, hour=17) + uses(0, hour=12)
    assert hour_match(at, NOW, 18) == pytest.approx(1.5)


def test_weights_are_normalized():
    w = Weights().normalized()  # 0.4, 0.2, 0.1, 0.3, 0.3 from the PRD
    assert w.use + w.time + w.state + w.ai + w.reject == pytest.approx(1.0)
    assert w.use == pytest.approx(0.4 / 1.3) and w.reject == pytest.approx(0.3 / 1.3)
    doubled = Weights(use=0.8, time=0.4, state=0.2, ai=0.6, reject=0.6).normalized()
    assert doubled == w  # only the ratios matter
    assert Weights(use=0, time=0, state=0, ai=0, reject=0).normalized().use == 0


def score(cands, **kw):
    return score_all(cands, now=NOW, hour=HOUR, weights=Weights(), **kw)


def test_each_part_is_scaled_to_the_best_candidate():
    s = score([Candidate("a", Evidence(uses(0, 0))), Candidate("b", Evidence(uses(0))), Candidate("c", Evidence())])
    assert (s["a"].use, s["b"].use, s["c"].use) == (1.0, 0.5, 0.0)
    assert s["a"].time == 1.0 and s["c"].score == 0.0
    assert s["a"].score > s["b"].score > s["c"].score


def test_time_of_day_bonus():
    morning = Candidate("breakfast", Evidence(uses(0, 1, 2, hour=8)))
    evening = Candidate("maria", Evidence(uses(0, 1, 2, hour=18)))
    s = score([morning, evening])
    assert s["breakfast"].use == s["maria"].use  # used as often...
    assert s["maria"].score > s["breakfast"].score  # ...but it is 18:30


def test_rejection_penalty_lasts_a_day():
    fresh = Candidate("x", Evidence(uses(0), rejections=[hours_ago(1)]))
    twice = Candidate("y", Evidence(uses(0), rejections=[hours_ago(1), hours_ago(2)]))
    old = Candidate("z", Evidence(uses(0), rejections=[hours_ago(25)]))
    s = score([fresh, twice, old])
    assert s["x"].reject == 0.5 and s["y"].reject == 0.75 and s["z"].reject == 0.0
    assert s["z"].score > s["x"].score > s["y"].score


def test_state_hook_and_ai_prior():
    assert state_fit("elevated", True) == 1.0
    assert state_fit("elevated", False) == state_fit("calm", True) == state_fit(None, True) == 0.0
    pain = Candidate("pain", Evidence(), urgent=True)
    tv = Candidate("tv", Evidence())
    assert score([pain, tv])["pain"].score == 0.0  # no state yet: nothing moves
    assert score([pain, tv], state_level="elevated")["pain"].score > 0
    s = score([Candidate("p", Evidence(), ai_prior=0.9), Candidate("q", Evidence(), ai_prior=0.1)])
    assert s["p"].ai == 0.9 and s["p"].score > s["q"].score


# --- ordering ------------------------------------------------------------------------------


def test_stability_rule_keeps_the_order_under_small_changes():
    ids = ["water", "food", "bathroom", "pain", "move"]
    small = {"water": 0.30, "food": 0.35, "bathroom": 0.10, "pain": 0.12, "move": 0.0}
    assert stable_order(ids, small) == ids  # food is ahead, but not by 1.5x
    assert stable_order(ids, {}) == ids
    tiny = {"move": 0.01}  # beats zeros by less than the minimum gap
    assert stable_order(ids, tiny) == ids


def test_stability_rule_moves_items_on_large_changes():
    ids = ["water", "food", "bathroom", "pain", "move"]
    big = {"water": 0.2, "food": 0.1, "bathroom": 0.0, "pain": 0.9, "move": 0.0}
    assert stable_order(ids, big) == ["pain", "water", "food", "bathroom", "move"]
    # It only climbs past the ones it clearly beats.
    mid = {"water": 0.5, "food": 0.1, "bathroom": 0.0, "pain": 0.6, "move": 0.0}
    assert stable_order(ids, mid) == ["water", "pain", "food", "bathroom", "move"]
    assert beats(0.3, 0.2) and not beats(0.29, 0.2)
    assert beats(0.0, -0.3)  # a rejected item sinks below untouched ones


def test_pinned_items_never_move():
    ids = ["suggested", "need", "people", "feel", "room"]
    assert stable_order(ids, {"people": 1.0}, pinned=1) == ["suggested", "people", "need", "feel", "room"]
    assert stable_order(ids, {"people": 1.0}, pinned=1)[0] == "suggested"


def test_full_order_sorts_by_score_and_keeps_ties():
    assert full_order(["a", "b", "c", "d"], {"c": 0.5, "b": 0.5, "d": 0.9}) == ["d", "b", "c", "a"]


# --- the session ----------------------------------------------------------------------------


@pytest.fixture(scope="module")
def menu():
    return load_menu()


@pytest.fixture(scope="module")
def profile(menu):
    return load_profile(menu.contacts)


@pytest.fixture
def db(tmp_path):
    d = Db(tmp_path / "t.db")
    d.sync_profile("Luis", "en", [])
    yield d
    d.close()


def confirm_many(db, node_id, text, times, *, action="speak", contact=None, lang="en"):
    for t in times:
        db.log_event(node_id=node_id, path=[], action=action, lang=lang, confirmed=True, text=text, contact=contact, t=t)
        db.use_phrase(text, lang, t=t)


def make(menu, profile, db, *, learning=True, lang="en"):
    sent: list = []
    ranker = Ranker(db, clock=lambda: NOW)
    s = Session(
        menu, sent.append, ManualScheduler(start=100.0), profile=profile, spawn=asyncio.run, lang=lang, db=db,
        learning=learning, ranker=ranker,
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


MARIA = "Honey, I'm okay, call me at six."
DAYS = [NOW - d * DAY_S for d in range(7)]


def test_home_keeps_suggested_first_and_other_last_with_history(menu, profile, db):
    confirm_many(db, "people.maria.text", MARIA, DAYS * 3, action="send_message", contact="maria")
    confirm_many(db, "room.tv.on", "Please turn on the TV.", DAYS * 3, action="room_control")
    s, sent = make(menu, profile, db)
    tiles = last_screen(sent).tiles
    assert tiles[0].id == "suggested" and tiles[-1].kind == "other"
    assert [t.id for t in tiles] == ["suggested", "people", "room", "need", "feel", "other"]


def test_small_history_does_not_reorder_the_menu(menu, profile, db):
    confirm_many(db, "people.maria.text", MARIA, [NOW - 3600])
    confirm_many(db, "need.water", "I'd like some water, please.", [NOW - 3660])
    s, sent = make(menu, profile, db)
    # People (one use) does not clearly beat I need (one use): menu.yaml order stays.
    assert [t.id for t in last_screen(sent).tiles] == ["suggested", "need", "people", "feel", "room", "other"]


def test_day1_mode_ignores_history(menu, profile, db):
    confirm_many(db, "people.maria.text", MARIA, DAYS * 3, action="send_message", contact="maria")
    s, sent = make(menu, profile, db, learning=False)
    assert [t.id for t in last_screen(sent).tiles] == ["suggested", "need", "people", "feel", "room", "other"]
    pick(s, sent, "Suggested")
    assert labels(sent) == ["I'm hungry", "Water, please", "Turn me over", "Thank you, I love you", "How are you?", "Other..."]


def test_suggested_offers_the_most_used_sentences_with_their_action(menu, profile, db):
    confirm_many(db, "people.maria.text", MARIA, DAYS * 2, action="send_message", contact="maria")
    confirm_many(db, "need.water", "I'd like some water, please.", DAYS[:3])
    s, sent = make(menu, profile, db)
    pick(s, sent, "Suggested")
    screen = last_screen(sent)
    assert screen.tiles[0].label == MARIA and screen.tiles[0].kind == "suggestion"
    assert screen.tiles[0].id == "people.maria.text"  # the leaf's own phrase keeps the leaf's id
    assert len(screen.tiles) == 6 and screen.tiles[-1].kind == "other"
    # The fixed "Water, please" tile says the same as the history sentence: shown once.
    assert sum("water" in t.label.lower() for t in screen.tiles) == 1
    pick(s, sent, MARIA)
    assert sent[-1] == Confirm(text=MARIA, action="send_message")  # the leaf's action, not the text's


def test_suggestions_screen_puts_the_usual_sentence_first(menu, profile, db):
    from core.suggest.fake import FakeProvider
    from core.suggest.service import Suggester

    confirm_many(db, "people.maria.text", MARIA, DAYS, action="send_message", contact="maria")
    sent: list = []
    jobs: list = []
    suggester = Suggester(FakeProvider(), patient_name="Luis", spawn=jobs.append, local_hour=lambda: HOUR, history=db)
    s = Session(
        menu, sent.append, ManualScheduler(start=100.0), profile=profile, spawn=asyncio.run, lang="en", db=db,
        suggester=suggester, ranker=Ranker(db, clock=lambda: NOW),
    )
    s.start()
    for tile in ["People", "Maria"]:
        pick(s, sent, tile)
    while jobs:
        asyncio.run(jobs.pop())
    pick(s, sent, "Text")
    screen = last_screen(sent)
    assert screen.screen == "suggestions"
    assert screen.tiles[0].label == MARIA  # full reorder: the fixed phrase he always sends is first
    assert [t.kind for t in screen.tiles] == ["suggestion"] * 4 + ["other"]
    for job in jobs:  # the prefetch for this screen's "Other..."
        job.close()


def test_ranker_rereads_history_after_new_events(db):
    r = Ranker(db, clock=lambda: NOW)
    entries = [Entry("a", path="need.water"), Entry("b", path="need.food")]
    assert r.score(entries, "en")["a"].score == 0
    confirm_many(db, "need.water", "I'd like some water, please.", [NOW - 60])
    assert r.score(entries, "en")["a"].score > 0
