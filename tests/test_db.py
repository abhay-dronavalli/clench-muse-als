import asyncio
import json
from datetime import datetime
from pathlib import Path

import pytest

from core.clock import ManualScheduler
from core.contracts import AudioDone, Clench, DoubleBlink
from core.db import Db
from core.menu import load_menu
from core.profile import ProfileError, load_profile
from core.session import CLENCH_DEBOUNCE_S, Session

T0 = datetime(2026, 9, 26, 14, 30).timestamp()  # 14:30 local time


@pytest.fixture(scope="module")
def menu():
    return load_menu()


@pytest.fixture(scope="module")
def profile(menu):
    return load_profile(menu.contacts)


class WallClock:
    def __init__(self, t: float) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def wall():
    return WallClock(T0)


@pytest.fixture
def db(wall):
    d = Db(":memory:", clock=wall)
    yield d
    d.close()


@pytest.fixture
def sched():
    return ManualScheduler(start=100.0)


@pytest.fixture
def session(menu, profile, sched, db):
    s = Session(menu, lambda m: None, sched, profile=profile, db=db, spawn=asyncio.run, lang="en", scan_ms=1000)
    s.start()
    return s


def walk(session: Session, sched: ManualScheduler, *tiles: str) -> None:
    """Pick each tile in turn: wait for the scan to reach it, then clench."""
    for tile in tiles:
        ids = [c.id for c in session.level.children]
        sched.advance(CLENCH_DEBOUNCE_S + 0.05 + ids.index(tile) * 1.0)
        session.handle(Clench(t=0.0, strength=1.0))


def confirm(session: Session, sched: ManualScheduler) -> None:
    sched.advance(CLENCH_DEBOUNCE_S + 0.05)
    session.handle(Clench(t=0.0, strength=1.0))


def test_profile_loads(profile):
    assert (profile.name, profile.lang, profile.help_contact) == ("Luis", "es", "maria")


def test_profile_help_contact_must_exist(menu, tmp_path: Path):
    path = tmp_path / "profile.yaml"
    path.write_text("name: Luis\nlang: es\nhelp_contact: nobody\n", encoding="utf-8")
    with pytest.raises(ProfileError, match="nobody"):
        load_profile(menu.contacts, path)


def test_sync_profile_is_idempotent(db, menu):
    for _ in range(2):
        db.sync_profile("Luis", "es", menu.contacts.values())
    rows = db._conn.execute("SELECT id, phone_env, telegram_chat_env FROM contacts ORDER BY id").fetchall()
    assert [tuple(r) for r in rows] == [
        ("carlos", "CONTACT_CARLOS_PHONE", "TELEGRAM_CHAT_ID_CARLOS"),
        ("maria", "CONTACT_MARIA_PHONE", "TELEGRAM_CHAT_ID_MARIA"),
        ("nurse", "CONTACT_NURSE_PHONE", "TELEGRAM_CHAT_ID_NURSE"),
    ]
    assert [tuple(r) for r in db._conn.execute("SELECT name, language FROM profile")] == [("Luis", "es")]


def test_pick_confirm_and_cancel_are_logged(session, sched, db, wall):
    db.sync_profile("Luis", "es", [])
    walk(session, sched, "need", "pain", "back", "a_lot")
    session.handle(DoubleBlink(t=0.0))  # cancel on the confirm screen
    walk(session, sched, "a_lot")
    confirm(session, sched)

    rows = [dict(r) for r in db.events()]
    summary = [(r["node_id"], r["action"], r["confirmed"], r["rejected"]) for r in rows]
    assert summary == [
        ("need", None, 0, 0),
        ("need.pain", None, 0, 0),
        ("need.pain.back", None, 0, 0),
        ("need.pain.back.a_lot", "speak", 0, 0),  # pick of the leaf opens the confirm screen
        ("need.pain.back.a_lot", "speak", 0, 1),  # cancelled
        ("need.pain.back.a_lot", "speak", 0, 0),
        ("need.pain.back.a_lot", "speak", 1, 0),  # confirmed
    ]
    last = rows[-1]
    assert json.loads(last["path"]) == ["I need", "Pain", "Back", "A lot"]
    assert last["lang"] == "en"
    assert last["t"] == T0
    assert last["state_level"] is None
    assert last["text"] == "My back hurts a lot. Can you help me turn over?"
    assert rows[4]["text"] == last["text"]  # the cancelled sentence is kept too
    assert all(r["text"] is None for r in rows[:4])


def test_phrase_use_count_and_hour_histogram(session, sched, db, wall):
    db.sync_profile("Luis", "es", [])
    for _ in range(2):
        walk(session, sched, "suggested", "water")
        confirm(session, sched)
        session.handle(AudioDone())
    wall.t = T0 + 3 * 3600  # 17:30
    walk(session, sched, "suggested", "water")
    confirm(session, sched)

    (row,) = db.phrases()
    assert (row["text"], row["lang"], row["uses"]) == ("I'd like some water, please.", "en", 3)
    assert row["last_used"] == T0 + 3 * 3600
    hist = json.loads(row["hour_histogram_json"])
    assert len(hist) == 24 and hist[14] == 2 and hist[17] == 1 and sum(hist) == 3


def test_contact_leaf_records_contact(session, sched, db):
    db.sync_profile("Luis", "es", [])
    walk(session, sched, "people", "maria", "text")
    confirm(session, sched)
    last = dict(db.events()[-1])
    assert (last["node_id"], last["action"], last["contact"], last["confirmed"]) == (
        "people.maria.text",
        "send_message",
        "maria",
        1,
    )


def test_database_file_persists(tmp_path: Path, wall):
    path = tmp_path / "clench.db"
    d = Db(path, clock=wall)
    d.sync_profile("Luis", "es", [])
    d.log_event(node_id="need", path=["I need"], action=None, lang="en")
    d.use_phrase("Hola", "es")
    d.close()
    d = Db(path, clock=wall)  # reopening never recreates or wipes tables
    assert len(d.events()) == 1
    d.use_phrase("Hola", "es")
    assert d.phrases()[0]["uses"] == 2
    d.close()
