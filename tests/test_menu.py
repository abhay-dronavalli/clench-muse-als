from pathlib import Path

import pytest
import yaml

from core.menu import CONTACTS_PATH, MAX_ITEMS, MenuError, MenuNode, load_menu


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


def all_nodes_and_more(node: MenuNode):
    yield node
    for child in (node.children or []) + (node.more or []):
        yield from all_nodes_and_more(child)


def test_home_level(menu):
    assert [c.id for c in menu.root.children] == ["suggested", "need", "people", "feel", "room"]
    assert menu.root.children[0].ai_now
    # The old "Say something" phrases live on in Suggested and in the home "Other..." fallback.
    assert [c.id for c in menu.root.more] == ["yes", "no", "good_morning", "wait"]
    assert {"thank_you", "how_are_you"} <= {c.id for c in menu.root.children[0].children}


def test_every_level_has_at_most_five_items(menu):
    assert MAX_ITEMS == 5  # the session adds "Other..." as the sixth tile (PRD D8)
    for node in all_nodes_and_more(menu.root):
        if not node.is_leaf:
            assert 1 <= len(node.children) <= MAX_ITEMS, node.id
            assert node.more is None or 1 <= len(node.more) <= MAX_ITEMS, node.id


def test_moved_items_are_in_more(menu):
    assert [c.id for c in find(menu.root, "need").more] == ["temperature"]
    assert [c.id for c in find(menu.root, "need", "pain").more] == ["arms"]
    assert [c.id for c in find(menu.root, "feel").more] == ["happy"]


def test_ai_options_inherit_action_and_contact_from_the_path(menu):
    assert find(menu.root, "people", "maria").inherited() == ("speak", "maria")
    assert find(menu.root, "room").inherited() == ("room_control", None)
    assert find(menu.root, "people").inherited() == ("speak", None)
    assert menu.root.inherited() == ("speak", None)


def test_both_languages_on_every_node(menu):
    for node in all_nodes_and_more(menu.root):
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
        [leaf(i) for i in range(MAX_ITEMS + 1)],  # too many items on home
        [{"id": "x", "label_en": "A", "label_es": "B", "children": [leaf(i) for i in range(6)]}],  # 6 > 5
        [{"id": "x", "label_en": "A", "label_es": "B", "children": [leaf()], "more": [leaf(i) for i in range(1, 7)]}],
        [{"id": "x", "label_en": "A", "label_es": "B", "children": [leaf(1)], "more": [leaf(1)]}],  # same id twice
        [{"id": "x", "label_en": "A", "label_es": "B", "children": [leaf()], "more": []}],
        [leaf(more=[leaf(1)])],  # a leaf cannot have `more`
        [leaf(ai_now=True)],
        [leaf(id="other")],  # reserved for the session's own tile
        [{"id": "spell", "label_en": "A", "label_es": "B", "children": [leaf()]}],
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
