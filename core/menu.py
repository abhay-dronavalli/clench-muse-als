"""Menu tree loading and validation (PRD section 8, D1, D6, D8).

The tree is hand-written in data/menu.yaml; the "People" branch is built from data/contacts.yaml.
Loading fails loudly (MenuError) on a bad tree so a broken menu never reaches the patient.

Each level holds at most 5 items; the session adds "Other..." as a sixth tile on every level
(decisions.md #5). A branch may list `more`: fixed extra options that "Other..." shows when the AI
has nothing (no key, offline, slow). The home "Suggested" branch has `ai_now: true`: the AI's
guesses for right now go first, its fixed children are the fallback.
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

MAX_TILES = 6  # PRD D8: tiles on one screen
MAX_ITEMS = 5  # items per menu level; the session adds "Other..." as the sixth tile
RESERVED_IDS = frozenset({"other"})  # tile id the session uses for its own "Other..." tile
MenuAction = Literal["speak", "send_message", "place_call", "room_control"]
NodeId = Annotated[str, Field(pattern=r"^[a-z0-9_]+$")]
ActionContact = tuple[MenuAction, str | None]
Text = Annotated[str, Field(min_length=1)]
EnvName = Annotated[str, Field(pattern=r"^[A-Z][A-Z0-9_]*$")]


class MenuError(ValueError):
    """The menu or contacts file is invalid."""


class MenuNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: NodeId
    label_en: Text
    label_es: Text
    children: list[MenuNode] | None = None
    # Fixed extra options that "Other..." shows when the AI has nothing (same shape as children).
    more: list[MenuNode] | None = None
    from_contacts: bool = False
    ai_now: bool = False  # the AI's guesses for right now go first (home "Suggested")
    phrase_en: Text | None = None
    phrase_es: Text | None = None
    action: MenuAction | None = None
    contact: str | None = None  # contact id, for People leaves
    # Pain, bathroom, help: moves up when the body state is elevated (PRD D10, only reorders).
    urgent: bool = False
    # A switch tile: picking it hands the gestures to the desktop agent (docs/desktop-control.md).
    # Nothing is said or sent, so there is no confirm step; it has no phrase, action or children.
    switch: Literal["desktop"] | None = None

    @model_validator(mode="after")
    def _branch_or_leaf(self) -> MenuNode:
        if self.id in RESERVED_IDS:
            raise ValueError(f"node id '{self.id}' is reserved for the session's own tiles")
        leaf_fields = (self.phrase_en, self.phrase_es, self.action)
        if self.switch is not None:
            if self.children is not None or self.from_contacts or self.more is not None or self.ai_now                     or any(f is not None for f in leaf_fields):
                raise ValueError(f"switch '{self.id}' cannot have children, a phrase, an action, `more` or `ai_now`")
        elif self.children is not None or self.from_contacts:
            if any(f is not None for f in leaf_fields):
                raise ValueError(f"node '{self.id}' has children, so it cannot have a phrase or action")
            if self.children is not None:
                if not 1 <= len(self.children) <= MAX_ITEMS:
                    raise ValueError(f"node '{self.id}' has {len(self.children)} children; allowed 1 to {MAX_ITEMS}")
                if self.more is not None and not 1 <= len(self.more) <= MAX_ITEMS:
                    raise ValueError(f"node '{self.id}' has {len(self.more)} `more` items; allowed 1 to {MAX_ITEMS}")
                ids = [c.id for c in self.children + (self.more or [])]
                if len(ids) != len(set(ids)):
                    raise ValueError(f"node '{self.id}' has duplicate child ids: {ids}")
            elif self.more is not None:
                raise ValueError(f"node '{self.id}' is built from contacts, so it cannot have `more`")
        else:
            if any(f is None for f in leaf_fields):
                raise ValueError(f"leaf '{self.id}' needs phrase_en, phrase_es and action")
            if self.more is not None or self.ai_now:
                raise ValueError(f"leaf '{self.id}' cannot have `more` or `ai_now`")
        return self

    @property
    def is_leaf(self) -> bool:
        """An option that leads to a sentence (a switch tile is neither a leaf nor a branch)."""
        return self.children is None and self.switch is None

    def label(self, lang: Lang) -> str:
        return self.label_es if lang == "es" else self.label_en

    def phrase(self, lang: Lang) -> str:
        assert self.phrase_en is not None and self.phrase_es is not None
        return self.phrase_es if lang == "es" else self.phrase_en

    def leaves(self) -> list[MenuNode]:
        """Every leaf under this node (children and `more`), or itself for a leaf."""
        if self.is_leaf:
            return [self]
        if self.switch is not None:
            return []
        return [leaf for c in (self.children or []) + (self.more or []) for leaf in c.leaves()]

    @property
    def has_urgent(self) -> bool:
        """This node or anything below it is urgent."""
        return self.urgent or any(c.has_urgent for c in (self.children or []) + (self.more or []))

    def child(self, node_id: str) -> MenuNode | None:
        """The child (or `more` item) with this id."""
        return next((c for c in (self.children or []) + (self.more or []) if c.id == node_id), None)

    def inherited(self) -> ActionContact:
        """Action and contact for an option the AI adds to this level. They always come from the
        path, never from the AI: the action every leaf below shares (else speak), and the contact
        every leaf below shares (else none). People > Maria gives (speak, maria); Room gives
        room_control; People gives (speak, None)."""
        leaves = self.leaves()
        actions = {leaf.action for leaf in leaves}
        contacts = {leaf.contact for leaf in leaves}
        action = actions.pop() if len(actions) == 1 else "speak"
        assert action is not None
        return action, contacts.pop() if len(contacts) == 1 else None


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
    # Names of the .env variables holding the phone number / Telegram chat id (values never in git).
    phone_env: EnvName | None = None
    telegram_chat_env: EnvName | None = None
    phrases: ContactPhrases
    urgent: bool = False  # the People > <contact> branch moves up when the body state is elevated

    def label(self, lang: Lang) -> str:
        return self.label_es if lang == "es" else self.label_en


class _MenuFile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    home: list[MenuNode] = Field(min_length=1, max_length=MAX_ITEMS)
    more: list[MenuNode] | None = None  # fixed extra options for the home "Other..."


class _ContactsFile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    contacts: list[Contact] = Field(min_length=1, max_length=MAX_ITEMS)


class Menu(BaseModel):
    root: MenuNode
    contacts: dict[str, Contact]

    def chain(self, path: str) -> list[MenuNode]:
        """The nodes along dotted `path` from home down, as far as the path exists in the menu."""
        nodes: list[MenuNode] = []
        node = self.root
        for part in path.split(".") if path else []:
            found = node.child(part)
            if found is None:
                break
            nodes.append(found)
            node = found
        return nodes

    def find(self, path: str) -> MenuNode | None:
        """The node at dotted `path`, or None when the path is not (all) in the menu."""
        nodes = self.chain(path)
        return nodes[-1] if nodes and len(nodes) == len(path.split(".")) else None


# Call / Text / Say out loud under each contact.
_CONTACT_ACTIONS: list[tuple[str, str, str, MenuAction]] = [
    ("call", "Call", "Llamar", "place_call"),
    ("text", "Text", "Mensaje", "send_message"),
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
    return MenuNode(id=c.id, label_en=c.label_en, label_es=c.label_es, children=leaves, urgent=c.urgent)


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
    more = [_expand_contacts(c, contacts) for c in node.more] if node.more else node.more
    return node.model_copy(update={"children": [_expand_contacts(c, contacts) for c in node.children], "more": more})


def _read_yaml(path: Path) -> object:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as e:
        raise MenuError(f"cannot read {path}: {e}") from e


def load_menu(menu_path: Path = MENU_PATH, contacts_path: Path = CONTACTS_PATH) -> Menu:
    """Load and validate the menu tree and contacts. Raises MenuError on any problem."""
    try:
        contacts = _ContactsFile.model_validate(_read_yaml(contacts_path)).contacts
        menu_file = _MenuFile.model_validate(_read_yaml(menu_path))
        root = MenuNode(id="home", label_en="Home", label_es="Inicio", children=menu_file.home, more=menu_file.more)
        root = _expand_contacts(root, contacts)
        # Re-validate the expanded tree so generated nodes get the same checks.
        root = MenuNode.model_validate(root.model_dump())
    except ValidationError as e:
        raise MenuError(f"invalid menu ({menu_path.name} / {contacts_path.name}):\n{e}") from e
    return Menu(root=root, contacts={c.id: c for c in contacts})
