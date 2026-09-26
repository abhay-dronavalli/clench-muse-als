from pathlib import Path

import pytest
import yaml

from core.menu import CONTACTS_PATH, MAX_TILES, MenuError, MenuNode, load_menu


def all_nodes(node: MenuNode):
    yield node
    for child in node.children or []:
        yield from all_nodes(child)


@pytest.fixture(scope="module")
def menu():
    return load_menu()


def find(node: MenuNode, *ids: str) -> MenuNode:
    for i in ids:
        node = next(c for c in node.children if c.id == i)
    return node


def test_home_level(menu):
    assert [c.id for c in menu.root.children] == ["suggested", "need", "people", "feel", "say", "room"]


def test_every_level_has_at_most_six_items(menu):
    for node in all_nodes(menu.root):
        if not node.is_leaf:
            assert 1 <= len(node.children) <= MAX_TILES, node.id


def test_both_languages_on_every_node(menu):
    for node in all_nodes(menu.root):
        assert node.label_en.strip() and node.label_es.strip(), node.id
        if node.is_leaf:
            assert node.phrase("en").strip() and node.phrase("es").strip(), node.id
            assert node.action in {"speak", "send_message", "place_call", "room_control"}


def test_prd_pain_path(menu):
    leaf = find(menu.root, "need", "pain", "back", "a_lot")
    assert leaf.phrase("en") == "My back hurts a lot. Can you help me turn over?"
    assert leaf.action == "speak"


def test_people_built_from_contacts(menu):
    people = find(menu.root, "people")
    assert [c.id for c in people.children] == ["maria", "carlos", "nurse"]
    for person in people.children:
        assert [(c.id, c.action) for c in person.children] == [
            ("call", "place_call"),
            ("text", "send_message"),
            ("say", "speak"),
        ]
        assert all(c.contact == person.id for c in person.children)
    text = find(menu.root, "people", "maria", "text")
    assert text.phrase("es") == "Mija, estoy bien, llámame a las seis."
    assert menu.contacts["maria"].relation == "daughter"


def leaf(i: int | str = 0, **overrides) -> dict:
    node = {"id": f"n{i}", "label_en": "A", "label_es": "B", "phrase_en": "a", "phrase_es": "b", "action": "speak"}
    node.update(overrides)
    return node


@pytest.mark.parametrize(
    "home",
    [
        [leaf(i) for i in range(MAX_TILES + 1)],  # too many tiles
        [{"id": "x", "label_en": "A", "label_es": "B", "children": [leaf(i) for i in range(7)]}],
        [leaf(label_es="")],  # empty label
        [{"id": "x", "label_en": "A", "phrase_en": "a", "phrase_es": "b", "action": "speak"}],  # missing label
        [leaf(phrase_es=None)],  # leaf without phrase
        [leaf(action=None)],  # leaf without action
        [leaf(action="launch_rocket")],
        [{"id": "x", "label_en": "A", "label_es": "B", "children": [leaf()], "phrase_en": "a"}],
        [leaf(1), leaf(1)],  # duplicate ids
        [leaf(id="Bad Id")],
        [{"id": "x", "label_en": "A", "label_es": "B", "children": []}],
    ],
)
def test_bad_tree_fails_loudly(tmp_path: Path, home):
    path = tmp_path / "menu.yaml"
    path.write_text(yaml.safe_dump({"home": home}), encoding="utf-8")
    with pytest.raises(MenuError):
        load_menu(path, CONTACTS_PATH)


def test_unreadable_yaml_fails_loudly(tmp_path: Path):
    path = tmp_path / "menu.yaml"
    path.write_text("home: [unclosed", encoding="utf-8")
    with pytest.raises(MenuError):
        load_menu(path, CONTACTS_PATH)
