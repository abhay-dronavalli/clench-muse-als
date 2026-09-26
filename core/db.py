"""Local SQLite store (PRD A7, D14): everything the person says stays on this laptop.

Only the tables needed so far: profile, contacts, events, phrases, audio_cache. The learning chunk
reads `events` (what was picked, when, in which language) and `phrases` (what was said, how often,
at what hour). `audio_cache` records each cloud TTS file saved in data/audio_cache/.

One profile per database for now (id 1). Timestamps are Unix seconds; hours are local time.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from collections.abc import Callable, Iterable, Sequence
from datetime import datetime
from pathlib import Path

from core.contracts import BodyStateLevel, Lang
from core.menu import DATA_DIR, Contact

log = logging.getLogger("clench.db")

DB_PATH = DATA_DIR / "clench.db"
PROFILE_ID = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS profile (
    id              INTEGER PRIMARY KEY,
    name            TEXT NOT NULL,
    language        TEXT NOT NULL,
    pointing_mode   TEXT NOT NULL DEFAULT 'auto',
    scan_speed_ms   INTEGER NOT NULL DEFAULT 1000,
    thresholds_json TEXT,
    head_range_json TEXT,
    created_at      REAL NOT NULL
);

-- Phone numbers and chat ids stay in .env; only the names of those variables are stored.
CREATE TABLE IF NOT EXISTS contacts (
    id                TEXT NOT NULL,
    profile_id        INTEGER NOT NULL REFERENCES profile(id),
    name              TEXT NOT NULL,
    relation          TEXT NOT NULL,
    language          TEXT NOT NULL,
    phone_env         TEXT,
    telegram_chat_env TEXT,
    PRIMARY KEY (profile_id, id)
);

-- One row per CLENCH pick, confirmed send, cancelled confirm and help alert.
--   pick:       confirmed=0, rejected=0 (action is NULL for a branch)
--   confirmed:  confirmed=1, with text and contact
--   cancelled:  rejected=1 (double blink on the confirm screen or during the help countdown)
CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id  INTEGER NOT NULL REFERENCES profile(id),
    t           REAL NOT NULL,
    node_id     TEXT NOT NULL,              -- dotted menu id, e.g. need.pain.back.a_lot; 'help' for the alert
    path        TEXT NOT NULL,              -- JSON list of breadcrumb labels down to the node, in `lang`
    action      TEXT,
    confirmed   INTEGER NOT NULL DEFAULT 0,
    rejected    INTEGER NOT NULL DEFAULT 0,
    lang        TEXT NOT NULL,
    state_level TEXT,                       -- body state at the time; NULL until the state chunk
    text        TEXT,
    contact     TEXT
);
CREATE INDEX IF NOT EXISTS events_by_time ON events (profile_id, t);
CREATE INDEX IF NOT EXISTS events_by_node ON events (profile_id, node_id);

-- Every confirmed sentence, with how often and at which local hours it was used.
CREATE TABLE IF NOT EXISTS phrases (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id          INTEGER NOT NULL REFERENCES profile(id),
    text                TEXT NOT NULL,
    lang                TEXT NOT NULL,
    uses                INTEGER NOT NULL DEFAULT 0,
    last_used           REAL,
    hour_histogram_json TEXT NOT NULL,      -- JSON list of 24 counts, index = local hour 0-23
    UNIQUE (profile_id, text, lang)
);

-- One row per cached TTS file. hash = sha256(text|lang|voice|model), also the file name.
CREATE TABLE IF NOT EXISTS audio_cache (
    hash       TEXT PRIMARY KEY,
    text       TEXT NOT NULL,
    lang       TEXT NOT NULL,
    voice      TEXT NOT NULL,              -- ElevenLabs voice id
    file_path  TEXT NOT NULL,              -- data/audio_cache/<hash>.mp3
    created_at REAL NOT NULL
);
"""


class Db:
    def __init__(self, path: str | Path = DB_PATH, *, clock: Callable[[], float] = time.time) -> None:
        """Open (or create) the database. `path` may be ":memory:" for tests."""
        self._clock = clock
        # The core is single-threaded asyncio, but the test client runs the app in another thread.
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        with self._conn:
            self._conn.executescript(SCHEMA)
        log.info("database ready: %s", path)

    def close(self) -> None:
        self._conn.close()

    # --- profile and contacts ------------------------------------------------------

    def sync_profile(self, name: str, lang: Lang, contacts: Iterable[Contact]) -> None:
        """Mirror data/profile.yaml and data/contacts.yaml into the database at startup."""
        with self._conn:
            self._conn.execute(
                """INSERT INTO profile (id, name, language, created_at) VALUES (?, ?, ?, ?)
                   ON CONFLICT (id) DO UPDATE SET name = excluded.name, language = excluded.language""",
                (PROFILE_ID, name, lang, self._clock()),
            )
            for c in contacts:
                self._conn.execute(
                    """INSERT INTO contacts (id, profile_id, name, relation, language, phone_env, telegram_chat_env)
                       VALUES (?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT (profile_id, id) DO UPDATE SET
                         name = excluded.name, relation = excluded.relation, language = excluded.language,
                         phone_env = excluded.phone_env, telegram_chat_env = excluded.telegram_chat_env""",
                    (c.id, PROFILE_ID, c.label_en, c.relation, c.language, c.phone_env, c.telegram_chat_env),
                )

    # --- events and phrases --------------------------------------------------------

    def log_event(
        self,
        *,
        node_id: str,
        path: Sequence[str],
        action: str | None,
        lang: Lang,
        confirmed: bool = False,
        rejected: bool = False,
        state_level: BodyStateLevel | None = None,
        text: str | None = None,
        contact: str | None = None,
    ) -> int:
        """Append one events row and return its id."""
        with self._conn:
            cur = self._conn.execute(
                """INSERT INTO events (profile_id, t, node_id, path, action, confirmed, rejected, lang,
                                       state_level, text, contact)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    PROFILE_ID,
                    self._clock(),
                    node_id,
                    json.dumps(list(path), ensure_ascii=False),
                    action,
                    int(confirmed),
                    int(rejected),
                    lang,
                    state_level,
                    text,
                    contact,
                ),
            )
        assert cur.lastrowid is not None
        return cur.lastrowid

    def use_phrase(self, text: str, lang: Lang) -> None:
        """Count one confirmed use of `text`: uses + 1, last_used = now, this hour's bucket + 1."""
        now = self._clock()
        hour = datetime.fromtimestamp(now).hour
        with self._conn:
            row = self._conn.execute(
                "SELECT id, hour_histogram_json FROM phrases WHERE profile_id = ? AND text = ? AND lang = ?",
                (PROFILE_ID, text, lang),
            ).fetchone()
            hist = json.loads(row["hour_histogram_json"]) if row else [0] * 24
            hist[hour] += 1
            if row is None:
                self._conn.execute(
                    """INSERT INTO phrases (profile_id, text, lang, uses, last_used, hour_histogram_json)
                       VALUES (?, ?, ?, 1, ?, ?)""",
                    (PROFILE_ID, text, lang, now, json.dumps(hist)),
                )
            else:
                self._conn.execute(
                    "UPDATE phrases SET uses = uses + 1, last_used = ?, hour_histogram_json = ? WHERE id = ?",
                    (now, json.dumps(hist), row["id"]),
                )

    def add_audio(self, hash: str, *, text: str, lang: Lang, voice: str, file_path: str) -> None:
        """Record a cached TTS file (replaces the row if the file was made again)."""
        with self._conn:
            self._conn.execute(
                """INSERT OR REPLACE INTO audio_cache (hash, text, lang, voice, file_path, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (hash, text, lang, voice, file_path, self._clock()),
            )

    # --- what the AI is told (PRD D14: a short summary, never the full history) ----

    def top_phrases(self, lang: Lang, limit: int = 20) -> list[str]:
        """The most used confirmed sentences in `lang`, most used first."""
        rows = self._conn.execute(
            """SELECT text FROM phrases WHERE profile_id = ? AND lang = ?
               ORDER BY uses DESC, last_used DESC LIMIT ?""",
            (PROFILE_ID, lang, limit),
        ).fetchall()
        return [r["text"] for r in rows]

    def recent_messages(self, lang: Lang, limit: int = 5) -> list[str]:
        """The last confirmed sentences in `lang` (spoken, sent or called), newest first. The help
        alert is left out: its text is a fixed line, not the patient's words."""
        rows = self._conn.execute(
            """SELECT text FROM events
               WHERE profile_id = ? AND lang = ? AND confirmed = 1 AND text IS NOT NULL AND node_id != 'help'
               ORDER BY t DESC, id DESC LIMIT ?""",
            (PROFILE_ID, lang, limit),
        ).fetchall()
        return [r["text"] for r in rows]

    # --- reading (tests, console history later) ------------------------------------

    def events(self) -> list[sqlite3.Row]:
        return self._conn.execute("SELECT * FROM events WHERE profile_id = ? ORDER BY id", (PROFILE_ID,)).fetchall()

    def phrases(self) -> list[sqlite3.Row]:
        return self._conn.execute("SELECT * FROM phrases WHERE profile_id = ? ORDER BY id", (PROFILE_ID,)).fetchall()

    def audio_cache(self) -> list[sqlite3.Row]:
        return self._conn.execute("SELECT * FROM audio_cache ORDER BY created_at, hash").fetchall()
