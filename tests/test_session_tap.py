"""TAP: a touch on the board picks a tile or confirms the "Say this?" card, like a CLENCH would."""

import pytest
from pydantic import ValidationError

from core.contracts import BackPrompt, Confirm, DoubleBlink, LongClench, Tap, parse_message
from core.session import BACK_CONFIRM_S, CLENCH_DEBOUNCE_S, SessionState
from tests.test_session import (  # noqa: F401 (fixtures)
    SCAN_S,
    last_screen,
    menu,
    profile,
    sched,
    sent,
    session,
    spoken,
)


def tap(sent, tile_id: str) -> Tap:
    """A TAP on the tile whose id ends in `tile_id`, on the current screen (wherever the highlight is)."""
    screen = last_screen(sent)
    index = [t.id.split(".")[-1] for t in screen.tiles].index(tile_id)
    return Tap(tile=index, seq=screen.seq, t=0.0)


def card() -> Tap:
    return Tap(tile=None, seq=None, t=0.0)


def settle(sched) -> None:
    sched.advance(CLENCH_DEBOUNCE_S + 0.05)


def test_a_tap_picks_that_tile_not_the_highlighted_one(session, sched, sent):
    settle(sched)
    assert session.highlight == 0
    session.handle(tap(sent, "people"))  # tile 3 of Home while the scan is on tile 0
    assert last_screen(sent).path == ["People"]


def test_taps_walk_to_the_confirm_screen_and_the_card_confirms(session, sched, sent):
    for tile in ["need", "pain", "back", "a_lot"]:
        settle(sched)
        session.handle(tap(sent, tile))
    assert session.state is SessionState.CONFIRMING
    assert sent[-1] == Confirm(text="My back hurts a lot. Can you help me turn over?", action="speak")
    assert spoken(sent) == []  # a tap on a tile never says the sentence: the confirm step still does
    settle(sched)
    session.handle(card())
    assert session.state is SessionState.SPEAKING
    assert spoken(sent) == [("My back hurts a lot. Can you help me turn over?", "en")]


def test_a_tap_for_an_older_screen_is_ignored(session, sched, sent):
    settle(sched)
    stale = tap(sent, "people")
    session.handle(tap(sent, "need"))
    assert last_screen(sent).path == ["I need"]
    settle(sched)
    session.handle(stale)  # Home's seq: the board has moved on
    assert last_screen(sent).path == ["I need"]


def test_a_tile_tap_on_the_confirm_screen_is_ignored(session, sched, sent):
    for tile in ["suggested", "hungry"]:
        settle(sched)
        session.handle(tap(sent, tile))
    assert session.state is SessionState.CONFIRMING
    settle(sched)
    session.handle(Tap(tile=0, seq=last_screen(sent).seq, t=0.0))
    assert session.state is SessionState.CONFIRMING
    assert spoken(sent) == []


def test_the_card_does_nothing_while_scanning(session, sched, sent):
    settle(sched)
    session.handle(card())
    assert session.state is SessionState.SCANNING
    assert last_screen(sent).path == []


def test_taps_share_the_clench_debounce(session, sched, sent):
    settle(sched)
    session.handle(tap(sent, "need"))
    session.handle(tap(sent, "pain"))  # within 300 ms: ignored
    assert last_screen(sent).path == ["I need"]


def test_a_tap_never_answers_the_go_back_prompt(session, sched, sent):
    settle(sched)
    session.handle(tap(sent, "need"))
    session.handle(DoubleBlink(t=0.0))
    assert isinstance(sent[-1], BackPrompt) and sent[-1].open
    settle(sched)
    session.handle(tap(sent, "pain"))
    assert last_screen(sent).path == ["I need"]  # neither went back nor picked
    sched.advance(BACK_CONFIRM_S)
    assert last_screen(sent).path == ["I need"]


def test_a_tap_never_cancels_or_confirms_help(session, sched, sent):
    session.handle(LongClench(t=0.0, duration=2.5))
    assert session.state is SessionState.HELP_COUNTDOWN
    session.handle(card())
    session.handle(Tap(tile=0, seq=last_screen(sent).seq, t=0.0))
    assert session.state is SessionState.HELP_COUNTDOWN


def test_tap_parses_and_rejects_half_a_card():
    assert parse_message({"type": "TAP", "tile": 2, "seq": 5, "t": 1.0}) == Tap(tile=2, seq=5, t=1.0)
    assert parse_message({"type": "TAP", "tile": None, "seq": None, "t": 1.0}) == card().model_copy(update={"t": 1.0})
    with pytest.raises(ValidationError):
        parse_message({"type": "TAP", "tile": 2, "seq": None, "t": 1.0})
    with pytest.raises(ValidationError):
        parse_message({"type": "TAP", "tile": -1, "seq": 0, "t": 1.0})


def test_scan_keeps_running_after_a_tap_elsewhere(session, sched, sent):
    settle(sched)
    session.handle(tap(sent, "people"))
    before = last_screen(sent).highlight
    sched.advance(SCAN_S)
    assert last_screen(sent).highlight != before
