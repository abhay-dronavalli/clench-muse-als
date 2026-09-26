"""Menu tree loading and validation (PRD section 8, D1, D6, D8).

The tree is hand-written in data/menu.yaml; the "People" branch is built from data/contacts.yaml.
Loading fails loudly (MenuError) on a bad tree so a broken menu never reaches the patient.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from core.contracts import Lang

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
MENU_PATH = DATA_DIR / "menu.yaml"
CONTACTS_PATH = DATA_DIR / "contacts.yaml"

MAX_TILES = 6  # PRD D8
MenuAction = Literal["speak", "send_text", "place_call", "room_control"]
NodeId = Annotated[str, Field(pattern=r"^[a-z0-9_]+$")]
Text = Annotated[str, Field(min_length=1)]


class MenuError(ValueError):
    """The menu or contacts file is invalid."""


class MenuNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: NodeId
    label_en: Text
    label_es: Text
    children: list[MenuNode] | None = None
    from_contacts: bool = False
    phrase_en: Text | None = None
    phrase_es: Text | None = None
    action: MenuAction | None = None
    contact: str | None = None  # contact id, for People leaves

    @model_validator(mode="after")
    def _branch_or_leaf(self) -> MenuNode:
        leaf_fields = (self.phrase_en, self.phrase_es, self.action)
        if self.children is not None or self.from_contacts:
            if any(f is not None for f in leaf_fields):
                raise ValueError(f"node '{self.id}' has children, so it cannot have a phrase or action")
            if self.children is not None:
                if not 1 <= len(self.children) <= MAX_TILES:
                    raise ValueError(
                        f"node '{self.id}' has {len(self.children)} children; allowed 1 to {MAX_TILES}"
                    )
                ids = [c.id for c in self.children]
                if len(ids) != len(set(ids)):
                    raise ValueError(f"node '{self.id}' has duplicate child ids: {ids}")
        elif any(f is None for f in leaf_fields):
            raise ValueError(f"leaf '{self.id}' needs phrase_en, phrase_es and action")
        return self

    @property
    def is_leaf(self) -> bool:
        return self.children is None

    def label(self, lang: Lang) -> str:
        return self.label_es if lang == "es" else self.label_en

    def phrase(self, lang: Lang) -> str:
        assert self.phrase_en is not None and self.phrase_es is not None
        return self.phrase_es if lang == "es" else self.phrase_en


class Phrase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    en: Text
    es: Text


class ContactPhrases(BaseModel):
    model_config = ConfigDict(extra="forbid")
    call: Phrase
    text: Phrase
    say: Phrase


class Contact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: NodeId
    label_en: Text
    label_es: Text
    relation: str
    language: Lang
    phrases: ContactPhrases


class _MenuFile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    home: list[MenuNode] = Field(min_length=1, max_length=MAX_TILES)


class _ContactsFile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    contacts: list[Contact] = Field(min_length=1, max_length=MAX_TILES)


class Menu(BaseModel):
    root: MenuNode
    contacts: dict[str, Contact]


# Call / Text / Say out loud under each contact.
_CONTACT_ACTIONS: list[tuple[str, str, str, MenuAction]] = [
    ("call", "Call", "Llamar", "place_call"),
    ("text", "Text", "Mensaje", "send_text"),
    ("say", "Say out loud", "Decir en voz alta", "speak"),
]


def _contact_branch(c: Contact) -> MenuNode:
    leaves = []
    for key, label_en, label_es, action in _CONTACT_ACTIONS:
        phrase: Phrase = getattr(c.phrases, key)
        leaves.append(
            MenuNode(
                id=key,
                label_en=label_en,
                label_es=label_es,
                phrase_en=phrase.en,
                phrase_es=phrase.es,
                action=action,
                contact=c.id,
            )
        )
    return MenuNode(id=c.id, label_en=c.label_en, label_es=c.label_es, children=leaves)


def _expand_contacts(node: MenuNode, contacts: list[Contact]) -> MenuNode:
    if node.from_contacts:
        return MenuNode(
            id=node.id,
            label_en=node.label_en,
            label_es=node.label_es,
            children=[_contact_branch(c) for c in contacts],
        )
    if node.children is None:
        return node
    return node.model_copy(update={"children": [_expand_contacts(c, contacts) for c in node.children]})


def _read_yaml(path: Path) -> object:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as e:
        raise MenuError(f"cannot read {path}: {e}") from e


def load_menu(menu_path: Path = MENU_PATH, contacts_path: Path = CONTACTS_PATH) -> Menu:
    """Load and validate the menu tree and contacts. Raises MenuError on any problem."""
    try:
        contacts = _ContactsFile.model_validate(_read_yaml(contacts_path)).contacts
        home = _MenuFile.model_validate(_read_yaml(menu_path)).home
        root = MenuNode(id="home", label_en="Home", label_es="Inicio", children=home)
        root = _expand_contacts(root, contacts)
        # Re-validate the expanded tree so generated nodes get the same checks.
        root = MenuNode.model_validate(root.model_dump())
    except ValidationError as e:
        raise MenuError(f"invalid menu ({menu_path.name} / {contacts_path.name}):\n{e}") from e
    return Menu(root=root, contacts={c.id: c for c in contacts})
