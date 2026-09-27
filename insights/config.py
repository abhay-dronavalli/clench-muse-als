"""Settings for the insights service, read from the environment (and the repo's .env).

The database URL is a secret: it is read here and passed to psycopg, never logged or printed.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent

# The daily continuous aggregates bucket by this zone. It is fixed in schema.sql (a continuous
# aggregate's definition cannot take a parameter), so change both together.
LOCAL_TZ = "America/New_York"


@dataclass(frozen=True)
class Config:
    database_url: str | None
    core_ws_url: str
    port: int
    decline_drop: float
    decline_window_days: int
    decline_min_days: int


def load(env: dict[str, str] | None = None) -> Config:
    if env is None:
        load_dotenv(REPO_ROOT / ".env", override=False)
        env = dict(os.environ)
    core_port = env.get("CORE_PORT") or "8000"
    return Config(
        database_url=env.get("TIGER_DATABASE_URL") or None,
        core_ws_url=env.get("INSIGHTS_CORE_WS") or f"ws://127.0.0.1:{core_port}/ws/console",
        port=int(env.get("INSIGHTS_PORT") or 8300),
        decline_drop=float(env.get("INSIGHTS_DECLINE_DROP") or 0.15),
        decline_window_days=int(env.get("INSIGHTS_DECLINE_WINDOW_DAYS") or 14),
        decline_min_days=int(env.get("INSIGHTS_DECLINE_MIN_DAYS") or 7),
    )
