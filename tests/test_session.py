import pytest

from core.clock import ManualScheduler
from core.contracts import (
    AudioDone,
    Clench,
    Confirm,
    DoubleBlink,
    LongClench,
    Screen,
    Settings,
    Speak,
)
from core.menu import load_menu
from core.pointer import ScanPointer
from core.session import CLENCH_DEBOUNCE_S, SPEAK_TIMEOUT_S, Session, SessionState

SCAN_S = 1.0


@pytest.fixture(scope="module")
def menu():
    return load_menu()


@pytest.fixture
def sched():
    return ManualScheduler(start=100.0)


@pytest.fixture
def sent():
    return []


@pytest.fixture
def session(menu, sched, sent):
    s = Session(menu, sent.append, sched, scan_ms=int(SCAN_S * 1000))
    s.start()
    return s


def last_screen(sent) -> Screen:
    return next(m for m in reversed(sent) if isinstance(m, Screen))


def spoken(sent) -> list[Speak]:
    return [m for m in sent if isinstance(m, Speak)]


def clench() -> Clench:
    return Clench(t=0.0, strength=1.0)


def blink() -> DoubleBlink:
    return DoubleBlink(t=0.0)


def pick(session: Session, sched: ManualScheduler, sent, tile: str) -> None:
    """Wait for the scan to reach `tile` (last part of the dotted id) on this level, then clench."""
    ids = [t.id.split(".")[-1] for t in last_screen(sent).tiles]
    index = ids.index(tile)
    # Past the debounce window, then `index` scan steps (the timer restarted when the level opened).
    sched.advance(CLENCH_DEBOUNCE_S + 0.05 + index * SCAN_S)
    assert session.highlight == index
    session.handle(clench())


def test_starts_scanning_home(session, sent):
    screen = last_screen(sent)
    assert session.state is SessionState.SCANNING
    assert [t.id for t in screen.tiles] == ["suggested", "need", "people", "feel", "say", "room"]
    assert screen.highlight == 0
    assert screen.path == []


def test_scan_moves_and_wraps(session, sched, sent):
    sched.advance(SCAN_S)
    assert last_screen(sent).highlight == 1
    sched.advance(5 * SCAN_S)
    assert last_screen(sent).highlight == 0  # 6 tiles: wrapped around


def test_walk_pain_back_a_lot_then_speak(session, sched, sent):
    for tile in ["need", "pain", "back"]:
        pick(session, sched, sent, tile)
    screen = last_screen(sent)
    assert screen.path == ["I need", "Pain", "Back"]
    assert [t.id for t in screen.tiles] == ["need.pain.back.a_little", "need.pain.back.a_lot"]
    assert screen.highlight == 0

    pick(session, sched, sent, "a_lot")
    assert session.state is SessionState.CONFIRMING
    assert sent[-1] == Confirm(text="My back hurts a lot. Can you help me turn over?", action="speak")
    assert spoken(sent) == []  # nothing spoken before the confirming clench (D5)

    # The highlight is frozen while confirming.
    n = len(sent)
    sched.advance(5 * SCAN_S)
    assert len(sent) == n

    sched.advance(CLENCH_DEBOUNCE_S + 0.05)
    session.handle(clench())
    assert session.state is SessionState.SPEAKING
    assert spoken(sent) == [Speak(text="My back hurts a lot. Can you help me turn over?", lang="en")]

    session.handle(AudioDone())
    assert session.state is SessionState.SCANNING
    assert last_screen(sent).path == []
    assert last_screen(sent).tiles[0].id == "suggested"


def test_double_blink_on_confirm_cancels(session, sched, sent):
    for tile in ["need", "pain", "back", "a_lot"]:
        pick(session, sched, sent, tile)
    assert session.state is SessionState.CONFIRMING
    session.handle(blink())
    assert session.state is SessionState.SCANNING
    screen = last_screen(sent)
    assert screen.path == ["I need", "Pain", "Back"]  # back on the level the leaf was on
    sched.advance(SPEAK_TIMEOUT_S * 2)
    assert spoken(sent) == []


def test_double_blink_goes_up_one_level(session, sched, sent):
    pick(session, sched, sent, "need")
    pick(session, sched, sent, "pain")
    session.handle(blink())
    assert last_screen(sent).path == ["I need"]
    session.handle(blink())
    assert last_screen(sent).path == []
    n = len(sent)
    session.handle(blink())  # no-op at home
    assert len(sent) == n
    assert session.state is SessionState.SCANNING


def test_clench_debounce(session, sched, sent):
    sched.advance(0.5)
    session.handle(clench())  # picks "suggested"
    assert last_screen(sent).path == ["Suggested"]
    sched.advance(CLENCH_DEBOUNCE_S - 0.1)
    session.handle(clench())  # too soon: ignored
    assert session.state is SessionState.SCANNING
    assert last_screen(sent).path == ["Suggested"]
    sched.advance(0.2)
    session.handle(clench())  # now accepted: picks "I'm hungry"
    assert session.state is SessionState.CONFIRMING


def test_nothing_spoken_without_confirm(session, sched, sent):
    pick(session, sched, sent, "suggested")
    pick(session, sched, sent, "hungry")
    session.handle(blink())
    session.handle(LongClench(t=0.0, duration=1.6))
    session.handle(AudioDone())
    sched.advance(60)
    assert spoken(sent) == []


def test_speaking_times_out_to_home(session, sched, sent):
    pick(session, sched, sent, "suggested")
    pick(session, sched, sent, "water")
    sched.advance(CLENCH_DEBOUNCE_S + 0.05)
    session.handle(clench())
    assert session.state is SessionState.SPEAKING
    session.handle(clench())  # clenches while speaking are ignored
    sched.advance(CLENCH_DEBOUNCE_S + 0.05)
    session.handle(clench())
    assert len(spoken(sent)) == 1
    sched.advance(SPEAK_TIMEOUT_S)
    assert session.state is SessionState.SCANNING
    assert last_screen(sent).path == []


def test_late_audio_done_is_ignored(session, sched, sent):
    session.handle(AudioDone())
    assert session.state is SessionState.SCANNING
    assert last_screen(sent).path == []


def test_long_clench_is_ignored(session, sent):
    n = len(sent)
    session.handle(LongClench(t=0.0, duration=1.6))
    assert len(sent) == n
    assert session.state is SessionState.SCANNING


def test_people_text_in_spanish(session, sched, sent):
    session.handle(Settings(pointing_mode="auto", scan_ms=1000, lang="es"))
    assert last_screen(sent).tiles[1].label == "Necesito"
    for tile in ["people", "maria", "text"]:
        pick(session, sched, sent, tile)
    assert sent[-1] == Confirm(text="Mija, estoy bien, llámame a las seis.", action="send_message")
    sched.advance(CLENCH_DEBOUNCE_S + 0.05)
    session.handle(clench())
    assert spoken(sent) == [Speak(text="Mija, estoy bien, llámame a las seis.", lang="es")]


def test_language_switch_while_confirming_resends_confirm(session, sched, sent):
    pick(session, sched, sent, "suggested")
    pick(session, sched, sent, "hungry")
    session.handle(Settings(pointing_mode="auto", scan_ms=1000, lang="es"))
    assert sent[-1] == Confirm(text="Tengo hambre. ¿Qué hay de almuerzo?", action="speak")


def test_scan_speed_setting(session, sched, sent):
    session.handle(Settings(pointing_mode="scan", scan_ms=400))
    sched.advance(0.4)
    assert last_screen(sent).highlight == 1
    assert session.scan_ms == 400


def test_scan_pointer_alone():
    sched = ManualScheduler()
    moves = []
    p = ScanPointer(sched, moves.append, scan_ms=500)
    p.on_tiles_changed(3)
    p.start()
    sched.advance(1.5)
    assert moves == [1, 2, 0]
    p.stop()
    sched.advance(5)
    assert moves == [1, 2, 0]
    p.on_tiles_changed(1)
    p.start()
    sched.advance(5)
    assert moves == [1, 2, 0]  # a single tile never moves
