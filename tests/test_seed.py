"""scripts/seed_demo.py: the simulated week loads, and --focus-hour makes the María text the top
Suggested phrase at that hour."""

import json
import random
from datetime import datetime

import pytest

from core.db import Db
from core.menu import load_menu
from tests.test_scripts import load

MARIA_ES = "Mija, estoy bien, llámame a las seis."


@pytest.fixture(scope="module")
def seed():
    return load("seed_demo")


@pytest.fixture(scope="module")
def menu():
    return load_menu()


@pytest.mark.parametrize("hour", [datetime.now().hour, 0, 15])
def test_focus_hour_makes_the_maria_text_the_top_suggestion(seed, menu, tmp_path, capsys, hour):
    path = tmp_path / "demo.db"
    assert seed.main(["--reset", "--load", "--yes", "--db", str(path), "--focus-hour", str(hour)]) == 0
    out = capsys.readouterr().out
    assert f"1. {MARIA_ES}" in out
    assert f"One-clench shortcut: {MARIA_ES!r}" in out
    db = Db(path)
    at = datetime.now().replace(hour=hour, minute=30)
    top, shortcut = seed.preview(db, menu, "es", at)
    assert top[0] == MARIA_ES and shortcut == MARIA_ES
    db.close()


def test_the_week_looks_like_the_core_wrote_it(seed, menu, tmp_path, capsys):
    path = tmp_path / "demo.db"
    seed.main(["--load", "--db", str(path), "--focus-hour", "12"])
    db = Db(path)
    events = db.events()
    confirmed = [e for e in events if e["confirmed"]]
    assert len(confirmed) >= 35
    now = datetime.now().timestamp()
    assert all(now - 8 * 86400 < e["t"] <= now for e in events)  # the last 7 days, nothing in the future
    maria = [e for e in confirmed if e["node_id"] == "people.maria.text"]
    assert all(e["text"] == MARIA_ES and e["contact"] == "maria" and e["action"] == "send_message" for e in maria)
    assert {datetime.fromtimestamp(e["t"]).hour for e in maria} == {12}
    # The picks down the path come first, as on the board.
    first = maria[0]["id"]
    trail = [(e["node_id"], json.loads(e["path"])) for e in events if first - 3 <= e["id"] < first]
    assert trail == [("people", ["Personas"]), ("people.maria", ["Personas", "María"]), ("people.maria.text", ["Personas", "María", "Mensaje"])]
    # Nothing else was said within an hour of the focus hour.
    others = [e for e in confirmed if e["node_id"] != "people.maria.text"]
    assert all(abs(datetime.fromtimestamp(e["t"]).hour - 12) > 1 for e in others)
    phrases = {p["text"]: p["uses"] for p in db.phrases()}
    assert phrases[MARIA_ES] == len(maria)
    db.close()


def test_reset_asks_first(seed, tmp_path, monkeypatch, capsys):
    path = tmp_path / "demo.db"
    seed.main(["--load", "--db", str(path)])
    monkeypatch.setattr("builtins.input", lambda prompt: "n")
    assert seed.main(["--reset", "--db", str(path)]) == 1
    assert "Nothing deleted" in capsys.readouterr().out
    db = Db(path)
    assert db.events()
    db.close()
    monkeypatch.setattr("builtins.input", lambda prompt: "y")
    assert seed.main(["--reset", "--db", str(path)]) == 0
    db = Db(path)
    assert db.events() == [] and db.phrases() == []
    db.close()


def test_no_flags_only_prints_help(seed, tmp_path, capsys):
    assert seed.main(["--db", str(tmp_path / "x.db")]) == 0
    assert "--load" in capsys.readouterr().out
    assert not (tmp_path / "x.db").exists()


def test_patterns_must_match_the_menu(seed, menu, tmp_path):
    data = json.loads(seed.SEED_PATH.read_text(encoding="utf-8"))
    lang, days, patterns = seed.load_patterns(menu)
    assert lang == "es" and days == 7 and sum(p.focus for p in patterns) == 1
    data["patterns"][0]["contact"] = "carlos"  # the seed may not invent who a message goes to
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="does not match the menu"):
        seed.load_patterns(menu, bad)


def test_plan_moves_other_habits_away_from_the_focus_hour(seed, menu):
    _, days, patterns = seed.load_patterns(menu)
    week = seed.plan(patterns, days, 16, random.Random(1))
    for p in week:
        if p.focus:
            assert p.hours == (16, 16) and p.days_per_week == days
        else:
            assert all(min(abs(h - 16) % 24, 24 - abs(h - 16) % 24) > 1 for h in p.hours)
