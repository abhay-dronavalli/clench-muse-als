from core.computer.keyboard import Keyboard, ROWS


def test_row_column_letters_enye_space_delete_and_done():
    keyboard = Keyboard([], "en")
    assert "".join("".join(row) for row in ROWS[:5]) == "abcdefghijklmnñopqrstuvwxyz"
    assert len(keyboard.items()) == 6
    keyboard.pick("row:0")
    assert keyboard.items() == [("key:" + c, c) for c in "abcdef"]
    keyboard.pick("key:c")
    assert keyboard.row is None and keyboard.draft == "c"
    keyboard.pick("row:2")
    keyboard.pick("key:ñ")
    keyboard.pick("row:5")
    assert keyboard.items() == [("key:space", "Space"), ("key:delete", "Delete"), ("key:done", "Done")]
    keyboard.pick("key:space")
    keyboard.pick("row:5")
    keyboard.pick("key:delete")
    assert keyboard.draft == "cñ"
    keyboard.pick("row:5")
    assert keyboard.pick("key:done") == "cñ"


def test_word_completions_replace_only_current_word_and_stay_bounded():
    keyboard = Keyboard(["Celia Cruz", "César", "centro Miami", "Celia Cruz", "Celine Dion"])
    assert keyboard.completions() == []
    keyboard.draft = "música ce"
    assert keyboard.completions() == ["Celia", "centro", "Celine"]
    assert keyboard.items()[0] == ("complete:0", "Celia")
    keyboard.pick("complete:0")
    assert keyboard.draft == "música Celia " and keyboard.row is None
    keyboard.draft = "a" * 40
    keyboard.pick("key:b")
    keyboard.pick("key:space")
    assert len(keyboard.draft) == 40
    keyboard.pick("key:delete")
    assert len(keyboard.draft) == 39
    keyboard.draft = "x " * 19 + "ce"
    assert keyboard.completions() == []


def test_delete_empty_and_done_empty_are_safe():
    keyboard = Keyboard([])
    keyboard.pick("key:delete")
    assert keyboard.pick("key:done") == ""
    keyboard.pick("row:5")
    assert [label for _, label in keyboard.items()] == ["Espacio", "Borrar", "Listo"]
