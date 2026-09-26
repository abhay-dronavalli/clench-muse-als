"""The patient profile (PRD A7): name, starting language and who the help alert reaches.

Loaded from data/profile.yaml at startup. Loading fails loudly (ProfileError) on a bad file.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from core.contracts import Lang
from core.menu import DATA_DIR, Contact

PROFILE_PATH = DATA_DIR / "profile.yaml"


class ProfileError(ValueError):
    """The profile file is invalid."""


class Profile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Annotated[str, Field(min_length=1)]
    lang: Lang = "en"
    help_contact: str  # contact id in data/contacts.yaml


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
