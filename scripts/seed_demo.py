"""Simulated week of use for the "it learns him" demo (PRD section 9, "Showing it in the demo").

    uv run python scripts/seed_demo.py --reset            # Day 1: no history (asks first; --yes skips)
    uv run python scripts/seed_demo.py --load             # 7 days of Luis's habits, ending now
    uv run python scripts/seed_demo.py --reset --load --yes --focus-hour 15
    uv run python scripts/seed_demo.py --load --lang es   # only the Spanish history

--load reads data/seed_demo_week.json (patterns: phrase in English and Spanish, menu path, action,
contact, typical hours, days per week) and writes believable events for the last 7 days into
data/clench.db, the same rows the core writes: the picks down the menu path, the confirmed send, and
the phrase count. By default (--lang both) every simulated use is written once in each language, so
the English and the Spanish board both show the learned sentences; the ranking scales every part of
the score across the candidates, so a week written twice ranks exactly like a week written once. The
pattern marked "focus" (the text to María) is tied strongly to --focus-hour (default: the current
hour): sent twice at that hour every day, with nothing else said within an hour of it, so it is the
top Suggested phrase whenever the demo runs. Prints what was loaded and, for each language written,
the top 3 Suggested phrases at the focus hour.

Only the local database is touched; nothing goes on the network. The running core picks the new
history up at its next screen (no restart). We say plainly in the demo that this week is simulated.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # run from anywhere

from core.clock import ManualScheduler  # noqa: E402
from core.contracts import Lang  # noqa: E402
from core.db import DB_PATH, Db  # noqa: E402
from core.menu import DATA_DIR, Menu, load_menu  # noqa: E402
from core.profile import load_profile  # noqa: E402
from core.rank import Ranker  # noqa: E402
from core.session import Session  # noqa: E402

SEED_PATH = DATA_DIR / "seed_demo_week.json"
FOCUS_PER_DAY = 2  # the focus text is sent this many times at the focus hour, every day
LANGS: tuple[Lang, ...] = ("es", "en")


@dataclass(frozen=True)
class Pattern:
    name: str
    path: str
    phrases: tuple[tuple[Lang, str], ...]  # (lang, phrase) for every language
    action: str
    contact: str | None
    hours: tuple[int, ...]
    days_per_week: int
    focus: bool = False

    def phrase(self, lang: Lang) -> str:
        return dict(self.phrases)[lang]


def load_patterns(menu: Menu, path: Path = SEED_PATH) -> tuple[int, list[Pattern]]:
    """(days, patterns) from the JSON, each checked against the menu in both languages so the seed
    can never teach a phrase, action or contact the menu does not have."""
    data = json.loads(path.read_text(encoding="utf-8"))
    days = int(data.get("days", 7))
    patterns = []
    for p in data["patterns"]:
        pat = Pattern(
            name=p["name"],
            path=p["path"],
            phrases=tuple((lang, p[f"phrase_{lang}"]) for lang in LANGS),
            action=p["action"],
            contact=p.get("contact"),
            hours=tuple(int(h) for h in p["hours"]),
            days_per_week=int(p["days_per_week"]),
            focus=bool(p.get("focus", False)),
        )
        node = menu.find(pat.path)
        if node is None or not node.is_leaf:
            raise ValueError(f"{path.name}: {pat.name!r}: {pat.path} is not a leaf of the menu")
        for lang in LANGS:
            if node.phrase(lang) != pat.phrase(lang) or node.action != pat.action or node.contact != pat.contact:
                raise ValueError(
                    f"{path.name}: {pat.name!r} does not match the menu at {pat.path} ({lang}): "
                    f"{node.phrase(lang)!r} / {node.action} / {node.contact}"
                )
        if not 0 < pat.days_per_week <= days or not all(0 <= h < 24 for h in pat.hours):
            raise ValueError(f"{path.name}: {pat.name!r}: bad hours or days_per_week")
        patterns.append(pat)
    return days, patterns


def plan(patterns: list[Pattern], days: int, focus_hour: int, rng: random.Random) -> list[Pattern]:
    """The patterns with the focus hour applied: the focus one moves to that hour (every day,
    FOCUS_PER_DAY times), and any other use within an hour of it moves two hours away."""
    out = []
    for p in patterns:
        if p.focus:
            out.append(Pattern(**{**p.__dict__, "hours": (focus_hour,) * FOCUS_PER_DAY, "days_per_week": days}))
            continue
        hours = []
        for h in p.hours:
            d = (h - focus_hour) % 24
            if d in (0, 1, 23):  # too close: two hours further away from the focus hour
                h = (focus_hour + 3) % 24 if d == 1 else (focus_hour - 2 - (d == 23)) % 24
            hours.append(h)
        out.append(Pattern(**{**p.__dict__, "hours": tuple(hours)}))
    return out


def write_week(
    db: Db, menu: Menu, langs: tuple[Lang, ...], days: int, patterns: list[Pattern], now: datetime, rng: random.Random
) -> dict[str, int]:
    """Write the events, each use once in every language of `langs`; returns uses per pattern name."""
    counts: dict[str, int] = {}
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    for p in patterns:
        n = 0
        for day in sorted(rng.sample(range(days), p.days_per_week)):
            date = today - timedelta(days=day)
            used_minutes: set[int] = set()
            for h in p.hours:
                minute = rng.randrange(2, 58)
                while minute in used_minutes:
                    minute = rng.randrange(2, 58)
                used_minutes.add(minute)
                when = date.replace(hour=h, minute=minute, second=rng.randrange(60))
                if when >= now:
                    continue  # later today: has not happened yet
                start = when.timestamp() - rng.uniform(0, 3)  # the same week whatever the languages
                for lang in langs:
                    _log_use(db, menu, lang, p, when.timestamp(), start)
                n += 1
        counts[p.name] = n
    return counts


def _log_use(db: Db, menu: Menu, lang: Lang, p: Pattern, t: float, start: float) -> None:
    """The rows the core writes for one use: a pick per menu level, then the confirmed send."""
    nodes = menu.chain(p.path)
    parts = p.path.split(".")
    labels = [n.label(lang) for n in nodes]
    start -= 2.5 * len(nodes)
    for i, node in enumerate(nodes):
        db.log_event(
            node_id=".".join(parts[: i + 1]),
            path=labels[: i + 1],
            action=node.action,
            lang=lang,
            t=start + 2.5 * i,
        )
    text = p.phrase(lang)
    db.log_event(node_id=p.path, path=labels, action=p.action, lang=lang, confirmed=True, text=text, contact=p.contact, t=t)
    db.use_phrase(text, lang, t=t)


def preview(db: Db, menu: Menu, lang: Lang, at: datetime) -> tuple[list[str], str | None]:
    """The home Suggested phrases as the core would rank them at `at`, and the shortcut phrase."""
    profile = load_profile(menu.contacts).model_copy(update={"learning": True})
    session = Session(
        menu, lambda m: None, ManualScheduler(), profile=profile, db=db, lang=lang,
        ranker=Ranker(db, weights=profile.ranking.weights, hysteresis=profile.ranking.hysteresis, clock=at.timestamp),
    )
    return session.suggested_preview()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Reset or load the simulated demo week in the local database.")
    parser.add_argument("--reset", action="store_true", help="delete every event and phrase (Day 1)")
    parser.add_argument("--load", action="store_true", help="write 7 days of use ending now from the seed JSON")
    parser.add_argument("--yes", action="store_true", help="do not ask before --reset")
    parser.add_argument("--focus-hour", type=int, choices=range(24), metavar="H", help="hour (0-23) the María text is tied to (default: now)")
    parser.add_argument(
        "--lang", choices=("both", *LANGS), default="both", help="write the history in both languages (default) or one"
    )
    parser.add_argument("--db", type=Path, default=DB_PATH, help=f"database file (default {DB_PATH})")
    parser.add_argument("--seed-file", type=Path, default=SEED_PATH, help="patterns JSON")
    parser.add_argument("--random-seed", type=int, default=7, help="same seed, same week (default 7)")
    args = parser.parse_args(argv)
    if not args.reset and not args.load:
        parser.print_help()
        return 0

    menu = load_menu()
    db = Db(args.db)
    try:
        if args.reset:
            n_events = len(db.events())
            n_phrases = len(db.phrases())
            if not args.yes:
                answer = input(f"Delete {n_events} events and {n_phrases} phrases from {args.db}? [y/N] ")
                if answer.strip().lower() not in ("y", "yes"):
                    print("Nothing deleted.")
                    return 1
            events, phrases = db.clear_history()
            print(f"Reset: deleted {events} events and {phrases} phrases. The board is on Day 1 now.")
        if args.load:
            profile = load_profile(menu.contacts)
            db.sync_profile(profile.name, profile.lang, menu.contacts.values())
            days, patterns = load_patterns(menu, args.seed_file)
            langs = LANGS if args.lang == "both" else (args.lang,)
            now = datetime.now()
            focus = now.hour if args.focus_hour is None else args.focus_hour
            rng = random.Random(args.random_seed)
            week = plan(patterns, days, focus, rng)
            counts = write_week(db, menu, langs, days, week, now, rng)
            print(f"Loaded a simulated week for {profile.name} ({' and '.join(langs)}), {days} days ending now:")
            for p in week:
                hours = ", ".join(f"{h:02d}:xx" for h in sorted(set(p.hours)))
                tag = "  <- focus hour" if p.focus else ""
                print(f"  {counts[p.name]:3d} x {p.name:28s} {p.path:22s} at {hours}{tag}")
            each = " in each language" if len(langs) > 1 else ""
            print(f"  {sum(counts.values())} confirmed messages in all{each}.")
            at = now if focus == now.hour else now.replace(hour=focus, minute=30)
            for lang in langs:
                top, shortcut = preview(db, menu, lang, at)
                print(f"Top 3 Suggested at {at:%H:%M} ({lang}):")
                for i, text in enumerate(top[:3], 1):
                    print(f"  {i}. {text}")
                print(f"One-clench shortcut: {shortcut!r}" if shortcut else "One-clench shortcut: off (not confident enough)")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
