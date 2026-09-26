"""Environment settings. Real values live in .env (git-ignored); .env.example lists every key."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"

_FALSE = {"false", "0", "no", "off"}


def load_env(path: Path = ENV_PATH) -> dict[str, str]:
    """.env values, overridden by the real environment. Does not modify os.environ."""
    from_file = {k: v for k, v in dotenv_values(path).items() if v is not None}
    return {**from_file, **os.environ}


def dry_run_enabled(env: Mapping[str, str]) -> bool:
    """ACTIONS_DRY_RUN is on unless it is explicitly false / 0 / no / off. Safe by default."""
    return env.get("ACTIONS_DRY_RUN", "true").strip().lower() not in _FALSE
