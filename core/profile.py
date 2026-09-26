"""The patient profile (PRD A7): name, starting language, who the help alert reaches, the clench
look-back for head pointing, and how the ranking learns (PRD section 9: weights, the stability margin, learning on or Day 1 mode).

Loaded from data/profile.yaml at startup. Loading fails loudly (ProfileError) on a bad file.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from core.contracts import Lang
from core.menu import DATA_DIR, Contact
from core.rank.score import DEFAULT_HYSTERESIS, Weights

PROFILE_PATH = DATA_DIR / "profile.yaml"


class ProfileError(ValueError):
    """The profile file is invalid."""


class Ranking(BaseModel):
    """PRD section 9 score weights (normalized when used) and the menu stability margin."""

    model_config = ConfigDict(extra="forbid")

    weights: Weights = Field(default_factory=Weights)
    # A menu item moves above the one before it only when its score is this many times higher.
    hysteresis: float = Field(default=DEFAULT_HYSTERESIS, ge=1.0)


class Profile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Annotated[str, Field(min_length=1)]
    lang: Lang = "en"
    help_contact: str  # contact id in data/contacts.yaml
    speak_picks: bool = True  # say each picked tile aloud as it is picked (switchable live)
    learning: bool = True  # false = Day 1 mode: yaml order, fixed Suggested list (switchable live)
    # When the highlight follows the head, a CLENCH picks the tile highlighted this long before it
    # arrived: clenching can move the head (PRD 3a "freeze on clench"). 0 = the tile at the clench.
    clench_lookback_ms: int = Field(default=250, ge=0, le=1000)
    # How long a clench must be held to count as a LONG_CLENCH (help alert). The Sensor Service and
    # the dev panel's hold-Space both use it (announced in SETTINGS). The 5 s countdown follows.
    long_clench_ms: int = Field(default=2500, ge=1000, le=5000)
    ranking: Ranking = Field(default_factory=Ranking)


def load_profile(contacts: dict[str, Contact], path: Path = PROFILE_PATH) -> Profile:
    """Load and validate the profile. `help_contact` must be one of `contacts`."""
    try:
        profile = Profile.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    except (OSError, yaml.YAMLError, ValidationError) as e:
        raise ProfileError(f"invalid profile {path}: {e}") from e
    if profile.help_contact not in contacts:
        raise ProfileError(
            f"{path.name}: help_contact '{profile.help_contact}' is not in contacts.yaml ({', '.join(contacts)})"
        )
    return profile
