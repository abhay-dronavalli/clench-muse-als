import asyncio
import json
from urllib.parse import parse_qs

import httpx
import pytest

from core.actions import build_registry
from core.clock import ManualScheduler
from core.contracts import (
    ActionResult,
    AudioDone,
    Clench,
    Confirm,
    DoubleBlink,
    LongClench,
    PlayAudio,
    Point,
    Reset,
    Screen,
    Settings,
    Speak,
)
from core.menu import load_menu
from core.pointer import ScanPointer
from core.profile import load_profile
from core.voice import Voice
from core.session import CLENCH_DEBOUNCE_S, HELP_COUNTDOWN_S, SPEAK_TIMEOUT_S, Session, SessionState, voice_lines

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


@pytest.fixture(scope="module")
def profile(menu):
    return load_profile(menu.contacts)


def run_now(coro) -> None:
    """Spawn stand-in: run a background action to completion right away."""
    asyncio.run(coro)


@pytest.fixture
def session(menu, profile, sched, sent):
    s = Session(menu, sent.append, sched, profile=profile, spawn=run_now, lang="en", scan_ms=int(SCAN_S * 1000))
    s.start()
    return s


def last_screen(sent) -> Screen:
    return next(m for m in reversed(sent) if isinstance(m, Screen))


def utterances(sent, kind: str) -> list[Speak | PlayAudio]:
    return [m for m in sent if isinstance(m, (Speak, PlayAudio)) and m.kind == kind]


def said(sent, kind: str) -> list[tuple[str, str]]:
    """(text, lang) of every utterance of `kind` said on the board."""
    return [(m.text, m.lang) for m in utterances(sent, kind)]


def spoken(sent) -> list[tuple[str, str]]:
    """Confirmed phrases only (no echoes, no system lines)."""
    return said(sent, "phrase")


def done(sent) -> AudioDone:
    """The board's AUDIO_DONE for the last phrase."""
    return AudioDone(id=utterances(sent, "phrase")[-1].id)


def results(sent) -> list[ActionResult]:
    return [m for m in sent if isinstance(m, ActionResult)]


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
    assert [t.id for t in screen.tiles] == ["suggested", "need", "people", "feel", "computer", "other"]
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
    assert [t.id for t in screen.tiles] == ["need.pain.back.a_little", "need.pain.back.a_lot", "need.pain.back.other"]
    assert [t.kind for t in screen.tiles] == ["leaf", "leaf", "other"]
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
    assert spoken(sent) == [("My back hurts a lot. Can you help me turn over?", "en")]

    session.handle(done(sent))
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
    session.handle(blink())  # help countdown cancelled
    session.handle(AudioDone(id="stray"))
    sched.advance(60)
    assert spoken(sent) == []
    assert results(sent) == []


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
    session.handle(AudioDone(id="late"))
    assert session.state is SessionState.SCANNING
    assert last_screen(sent).path == []


def long_clench() -> LongClench:
    return LongClench(t=0.0, duration=1.6)


def countdowns(sent) -> list[int | None]:
    return [m.countdown for m in sent if isinstance(m, Screen) and m.screen == "help_countdown"]


def test_help_countdown_fires_call_and_message(session, sched, sent):
    pick(session, sched, sent, "need")
    session.handle(long_clench())
    assert session.state is SessionState.HELP_COUNTDOWN
    assert sent[-1] == Screen(screen="help_countdown", seq=session.seq, tiles=[], highlight=None, lang="en", path=[], countdown=5)
    sched.advance(4.0)
    assert countdowns(sent) == [5, 4, 3, 2, 1]
    assert results(sent) == [] and spoken(sent) == []
    assert [m for m in sent if isinstance(m, Screen) and m.screen == "menu"][-1].path == ["I need"]  # frozen

    sched.advance(1.0)  # 0: fire (the countdown is the confirmation, no CONFIRM screen)
    assert not any(isinstance(m, Confirm) for m in sent)
    assert results(sent) == [
        ActionResult(action="place_call", ok=True, detail="dry run", contact="Maria"),
        ActionResult(action="send_message", ok=True, detail="dry run", contact="Maria"),
    ]
    assert spoken(sent) == []
    assert said(sent, "system") == [("Calling for help. Double blink to cancel.", "en"), ("Calling Maria", "en")]
    # A system line never holds the session: straight home.
    assert session.state is SessionState.SCANNING
    assert last_screen(sent).path == []
    assert last_screen(sent).screen == "menu"
    n = len(sent)
    session.handle(AudioDone(id=utterances(sent, "system")[-1].id))  # ignored
    assert len(sent) == n


def test_help_alert_sends_real_requests_in_spanish(menu, profile, sched, sent):
    requests: list[httpx.Request] = []

    def service(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(201, json={"ok": True, "sid": "CA1"})

    env = {
        "TELEGRAM_BOT_TOKEN": "1:x",
        "TELEGRAM_CHAT_ID_MARIA": "42",
        "TWILIO_ACCOUNT_SID": "AC1",
        "TWILIO_AUTH_TOKEN": "t",
        "TWILIO_FROM_NUMBER": "+13055550100",
        "CONTACT_MARIA_PHONE": "+13055550123",
    }
    actions = build_registry(Voice(sent.append), env, dry_run=False, transport=httpx.MockTransport(service))
    s = Session(menu, sent.append, sched, profile=profile, actions=actions, spawn=run_now)  # profile lang: es
    s.start()
    s.handle(long_clench())
    sched.advance(HELP_COUNTDOWN_S)
    call, message = requests
    assert call.url.host == "api.twilio.com"
    say = '<Say language="es-MX">Luis necesita ayuda ahora</Say>'
    assert parse_qs(call.content.decode())["Twiml"] == [f'<Response>{say}<Pause length="1"/>{say}</Response>']
    assert message.url.host == "api.telegram.org"
    assert json.loads(message.content) == {"chat_id": "42", "text": "Luis necesita ayuda ahora"}
    assert said(sent, "system") == [
        ("Pidiendo ayuda. Parpadea dos veces para cancelar.", "es"),
        ("Llamando a María", "es"),
    ]
    assert [(r.action, r.ok, r.contact) for r in results(sent)] == [
        ("place_call", True, "María"),
        ("send_message", True, "María"),
    ]


def test_double_blink_cancels_help_back_to_scanning(session, sched, sent):
    pick(session, sched, sent, "need")
    sched.advance(2 * SCAN_S)
    assert session.highlight == 2
    session.handle(long_clench())
    sched.advance(2.5)
    session.handle(blink())
    assert session.state is SessionState.SCANNING
    screen = last_screen(sent)
    assert (screen.screen, screen.path, screen.highlight) == ("menu", ["I need"], 2)  # where it was
    sched.advance(30)
    assert results(sent) == [] and spoken(sent) == []
    assert said(sent, "system") == [("Calling for help. Double blink to cancel.", "en")]  # no "Calling Maria"
    assert countdowns(sent) == [5, 4, 3]


def test_double_blink_cancels_help_back_to_confirm(session, sched, sent):
    pick(session, sched, sent, "suggested")
    pick(session, sched, sent, "hungry")
    session.handle(long_clench())
    sched.advance(1.0)
    session.handle(blink())
    assert session.state is SessionState.CONFIRMING
    assert sent[-1] == Confirm(text="I'm hungry. What's for lunch?", action="speak")
    session.handle(clench())  # the confirm screen works as before
    assert spoken(sent) == [("I'm hungry. What's for lunch?", "en")]


def test_help_ignores_clench_and_repeat_long_clench(session, sched, sent):
    session.handle(long_clench())
    sched.advance(1.5)
    session.handle(clench())
    session.handle(long_clench())
    assert session.state is SessionState.HELP_COUNTDOWN
    sched.advance(0.5)
    assert countdowns(sent) == [5, 4, 3]  # not restarted


def test_long_clench_while_speaking_is_ignored(session, sched, sent):
    pick(session, sched, sent, "suggested")
    pick(session, sched, sent, "water")
    sched.advance(CLENCH_DEBOUNCE_S + 0.05)
    session.handle(clench())
    session.handle(long_clench())
    assert session.state is SessionState.SPEAKING
    sched.advance(HELP_COUNTDOWN_S + 1)
    assert countdowns(sent) == []


def test_help_countdown_view_and_language_switch(session, sched, sent):
    session.handle(long_clench())
    sched.advance(2.0)
    assert session.current_view() == Screen(
        screen="help_countdown", seq=session.seq, tiles=[], highlight=None, lang="en", path=[], countdown=3
    )
    session.handle(Settings(pointing_mode="auto", scan_ms=1000, lang="es"))
    assert sent[-1] == Screen(
        screen="help_countdown", seq=session.seq, tiles=[], highlight=None, lang="es", path=[], countdown=3
    )


def test_people_text_in_spanish(session, sched, sent):
    session.handle(Settings(pointing_mode="auto", scan_ms=1000, lang="es"))
    assert last_screen(sent).tiles[1].label == "Necesito"
    for tile in ["people", "maria", "text"]:
        pick(session, sched, sent, tile)
    assert sent[-1] == Confirm(text="Mija, estoy bien, llámame a las seis.", action="send_message")
    sched.advance(CLENCH_DEBOUNCE_S + 0.05)
    session.handle(clench())
    # Spoken in the room and sent at the same time; with no keys the default registry dry-runs.
    assert spoken(sent) == [("Mija, estoy bien, llámame a las seis.", "es")]
    assert results(sent) == [ActionResult(action="send_message", ok=True, detail="dry run", contact="María")]


def test_speak_leaf_has_no_action_result(session, sched, sent):
    pick(session, sched, sent, "suggested")
    pick(session, sched, sent, "water")
    sched.advance(CLENCH_DEBOUNCE_S + 0.05)
    session.handle(clench())
    assert len(spoken(sent)) == 1
    assert results(sent) == []


def test_confirmed_call_really_sends(menu, profile, sched, sent):
    requests: list[httpx.Request] = []

    def twilio(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(201, json={"sid": "CA9"})

    env = {
        "TWILIO_ACCOUNT_SID": "AC1",
        "TWILIO_AUTH_TOKEN": "t",
        "TWILIO_FROM_NUMBER": "+13055550100",
        "CONTACT_CARLOS_PHONE": "+13055550199",
    }
    actions = build_registry(Voice(sent.append), env, dry_run=False, transport=httpx.MockTransport(twilio))
    s = Session(menu, sent.append, sched, profile=profile, actions=actions, spawn=run_now, lang="en")
    s.start()
    for tile in ["people", "carlos", "call"]:
        pick(s, sched, sent, tile)
    assert requests == []  # nothing before the confirming clench (D5)
    sched.advance(CLENCH_DEBOUNCE_S + 0.05)
    s.handle(clench())
    assert len(requests) == 1
    twiml = parse_qs(requests[0].content.decode())["Twiml"][0]
    assert "Message from Luis: Carlos, I'd like to see you. Please come by today." in twiml
    assert results(sent) == [ActionResult(action="place_call", ok=True, detail="call queued (CA9)", contact="Carlos")]
    assert s.state is SessionState.SPEAKING  # the room hears it too


def test_failed_send_reports_error(menu, profile, sched, sent):
    s = Session(
        menu,
        sent.append,
        sched,
        profile=profile,
        actions=build_registry(Voice(sent.append), {}, dry_run=False),
        spawn=run_now,
        lang="en",
    )
    s.start()
    for tile in ["people", "maria", "text"]:
        pick(s, sched, sent, tile)
    sched.advance(CLENCH_DEBOUNCE_S + 0.05)
    s.handle(clench())
    (result,) = results(sent)
    assert (result.ok, result.contact) == (False, "Maria")
    assert result.detail == "Telegram not configured: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID_MARIA missing"
    assert len(spoken(sent)) == 1  # still said aloud


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


def test_speaking_ends_only_on_the_phrase_id(session, sched, sent):
    pick(session, sched, sent, "suggested")
    pick(session, sched, sent, "water")
    sched.advance(CLENCH_DEBOUNCE_S + 0.05)
    session.handle(clench())
    (phrase,) = [m for m in sent if isinstance(m, Speak) and m.kind == "phrase"]
    assert session.speaking_id == phrase.id
    session.handle(AudioDone(id="an-older-one"))
    assert session.state is SessionState.SPEAKING
    session.handle(AudioDone(id=phrase.id))
    assert session.state is SessionState.SCANNING
    assert session.speaking_id is None
    assert last_screen(sent).path == []


def test_speak_picks_setting(session):
    assert session.speak_picks is True  # data/profile.yaml
    session.handle(Settings(pointing_mode="auto", scan_ms=1000, speak_picks=False))
    assert session.speak_picks is False
    session.handle(Settings(pointing_mode="auto", scan_ms=1000))  # omitted: kept
    assert session.speak_picks is False


def test_each_pick_is_echoed_before_the_next_level(session, sched, sent):
    for tile in ["need", "pain", "back", "a_lot"]:
        pick(session, sched, sent, tile)
    assert said(sent, "echo") == [("I need", "en"), ("Pain", "en"), ("Back", "en"), ("A lot", "en")]
    # Each echo goes out right before the view it opens: the next level, or the confirm screen.
    after = [sent[sent.index(echo) + 1] for echo in utterances(sent, "echo")]
    assert [m.path[-1] for m in after[:3]] == ["I need", "Pain", "Back"]
    assert isinstance(after[3], Confirm)
    assert spoken(sent) == []  # the sentence itself still waits for the confirm (D5)


def test_scanning_does_not_wait_for_the_echo(session, sched, sent):
    pick(session, sched, sent, "need")
    assert session.state is SessionState.SCANNING
    sched.advance(SCAN_S)
    assert last_screen(sent).highlight == 1  # the highlight moves on at once


def test_no_echo_on_double_blink_or_confirm_clench(session, sched, sent):
    pick(session, sched, sent, "suggested")
    pick(session, sched, sent, "water")
    session.handle(blink())  # back from the confirm screen
    session.handle(blink())  # up to home
    assert said(sent, "echo") == [("Suggested", "en"), ("Water, please", "en")]
    pick(session, sched, sent, "suggested")
    pick(session, sched, sent, "water")
    sched.advance(CLENCH_DEBOUNCE_S + 0.05)
    session.handle(clench())  # confirm
    assert len(said(sent, "echo")) == 4
    assert spoken(sent) == [("I'd like some water, please.", "en")]


def test_echo_audio_done_does_not_end_speaking(session, sched, sent):
    pick(session, sched, sent, "suggested")
    pick(session, sched, sent, "water")
    sched.advance(CLENCH_DEBOUNCE_S + 0.05)
    session.handle(clench())
    for echo in utterances(sent, "echo"):
        session.handle(AudioDone(id=echo.id))
    assert session.state is SessionState.SPEAKING
    session.handle(done(sent))
    assert session.state is SessionState.SCANNING


def test_speak_picks_off_means_no_echo(session, sched, sent):
    session.handle(Settings(pointing_mode="auto", scan_ms=1000, speak_picks=False))
    for tile in ["need", "pain", "back", "a_lot"]:
        pick(session, sched, sent, tile)
    assert said(sent, "echo") == []
    session.handle(Settings(pointing_mode="auto", scan_ms=1000, speak_picks=True))
    session.handle(blink())
    pick(session, sched, sent, "a_little")
    assert said(sent, "echo") == [("A little", "en")]


def test_echo_in_spanish(menu, profile, sched, sent):
    s = Session(menu, sent.append, sched, profile=profile, spawn=run_now, lang="es", speak_picks=True)
    s.start()
    pick(s, sched, sent, "need")
    assert said(sent, "echo") == [("Necesito", "es")]


def test_voice_lines_cover_labels_system_lines_and_phrases(menu, profile):
    lines = voice_lines(menu, profile)
    assert len(lines) == len(set(lines))
    for line in [
        ("Necesito", "es"),
        ("I need", "en"),
        ("A lot", "en"),
        ("My back hurts a lot. Can you help me turn over?", "en"),
        ("Mija, estoy bien, llámame a las seis.", "es"),
        ("Pidiendo ayuda. Parpadea dos veces para cancelar.", "es"),
        ("Calling for help. Double blink to cancel.", "en"),
        ("Llamando a María", "es"),
        ("Calling Maria", "en"),
    ]:
        assert line in lines
    assert lines.index(("A lot", "en")) < lines.index(("Calling Maria", "en"))  # labels first


# --- start state and RESET ---------------------------------------------------------------------


def test_every_new_screen_starts_on_tile_0_with_a_full_scan_step(session, sched, sent):
    sched.advance(2 * SCAN_S + 0.4)  # mid-way through the third tile's step
    assert session.highlight == 2
    session.handle(clench())  # People
    assert last_screen(sent).path == ["People"]
    assert session.highlight == 0
    sched.advance(SCAN_S - 0.01)
    assert session.highlight == 0  # the timer restarted from 0 at the new screen
    sched.advance(0.02)
    assert session.highlight == 1
    session.handle(blink())  # back up: a new screen again
    assert session.highlight == 0
    sched.advance(SCAN_S - 0.01)
    assert session.highlight == 0


def test_reset_goes_home_on_the_first_tile(session, sched, sent):
    pick(session, sched, sent, "need")
    pick(session, sched, sent, "pain")
    sched.advance(2 * SCAN_S + 0.3)
    assert session.highlight == 2
    seq = session.seq
    session.handle(Reset())
    screen = last_screen(sent)
    assert screen.path == [] and screen.highlight == 0
    assert screen.tiles[0].id == "suggested"
    assert screen.seq > seq
    assert len(session._stack) == 1
    sched.advance(SCAN_S - 0.01)
    assert session.highlight == 0  # scan timer restarted from 0
    sched.advance(0.02)
    assert session.highlight == 1


def test_reset_at_home_mid_scan_starts_over(session, sched, sent):
    sched.advance(3 * SCAN_S + 0.5)
    assert session.highlight == 3
    seq = session.seq
    session.handle(Reset())
    assert last_screen(sent).highlight == 0
    assert last_screen(sent).seq == seq + 1  # same tiles, a new seq: an old POINT cannot land on it


def test_reset_leaves_the_confirm_screen_without_saying_anything(session, sched, sent):
    for tile in ["need", "pain", "back", "a_lot"]:
        pick(session, sched, sent, tile)
    assert session.state is SessionState.CONFIRMING
    session.handle(Reset())
    assert session.state is SessionState.SCANNING
    assert last_screen(sent).path == []
    sched.advance(CLENCH_DEBOUNCE_S + 0.05)
    session.handle(clench())  # picks Suggested, confirms nothing
    assert spoken(sent) == []


def test_reset_while_speaking_goes_home(session, sched, sent):
    for tile in ["need", "pain", "back", "a_lot"]:
        pick(session, sched, sent, tile)
    sched.advance(CLENCH_DEBOUNCE_S + 0.05)
    session.handle(clench())
    assert session.state is SessionState.SPEAKING
    session.handle(Reset())
    assert session.state is SessionState.SCANNING
    assert session.speaking_id is None
    sched.advance(SPEAK_TIMEOUT_S + 1)  # the speaking timeout was cancelled: no second trip home
    assert session.state is SessionState.SCANNING


def test_reset_never_cancels_a_help_countdown(session, sched, sent):
    session.handle(LongClench(t=0.0, duration=2.5))
    session.handle(Reset())
    assert session.state is SessionState.HELP_COUNTDOWN
    sched.advance(HELP_COUNTDOWN_S)
    assert [r.action for r in results(sent)] == ["place_call", "send_message"]


def test_reset_puts_the_head_on_tile_0_too(menu, profile, sched, sent):
    s = Session(menu, sent.append, sched, profile=profile, spawn=run_now, lang="en", pointing_mode="webcam")
    s.start()
    s.handle(Point(source="webcam", tile=4, seq=s.seq, t=0.0))
    assert s.highlight == 4
    s.handle(Reset())
    assert last_screen(sent).highlight == 0
