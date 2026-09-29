"""Media on the board (Computer > YouTube / Spotify): MEDIA and the player screen's control tiles."""

import pytest
from pydantic import ValidationError

from core.contracts import Clench, Confirm, DoubleBlink, LongClench, Media, Reset, Settings, Tile, parse_message
from core.session import HELP_COUNTDOWN_S, SessionState
from tests.test_session import (  # noqa: F401 (fixtures)
    last_screen,
    menu,
    profile,
    sched,
    sent,
    session,
    spoken,
)
from tests.test_session_tap import settle, tap


def media(sent) -> list[Media]:
    return [m for m in sent if isinstance(m, Media)]


def ends(screen) -> list[str]:
    return [t.id.split(".")[-1] for t in screen.tiles]


def walk(session, sched, sent, *tiles: str) -> None:
    for tile in tiles:
        settle(sched)
        session.handle(tap(sent, tile))


def test_computer_offers_youtube_spotify_and_the_web_browser(session, sched, sent):
    walk(session, sched, sent, "computer")
    assert ends(last_screen(sent)) == ["youtube", "spotify", "browser", "other"]


def test_video_tiles_show_their_picture(session, sched, sent):
    walk(session, sched, sent, "computer", "youtube")
    lofi = last_screen(sent).tiles[0]
    assert lofi.id == "computer.youtube.lofi"
    assert lofi.image == "https://i.ytimg.com/vi/jfKfPfyJRdk/hqdefault.jpg"


def test_a_video_plays_on_the_player_screen_with_no_confirm_and_nothing_said(session, sched, sent):
    walk(session, sched, sent, "computer", "youtube", "lofi")
    assert media(sent) == [Media(action="play", provider="youtube", id="jfKfPfyJRdk", title="Lofi radio")]
    screen = last_screen(sent)
    assert screen.screen == "player"
    assert ends(screen) == ["pause", "restart", "volume_down", "volume_up", "back"]  # no "Other..."
    assert screen.corner is None  # the Car mode button is Home's only
    assert not any(isinstance(m, Confirm) for m in sent)
    assert spoken(sent) == []
    assert session.state is SessionState.SCANNING


def test_spotify_has_no_volume_tiles(session, sched, sent):
    walk(session, sched, sent, "computer", "spotify", "top_hits")
    assert media(sent)[-1].provider == "spotify"
    assert ends(last_screen(sent)) == ["pause", "restart", "back"]


def test_pause_turns_into_play_and_each_control_sends_media(session, sched, sent):
    walk(session, sched, sent, "computer", "youtube", "lofi", "pause")
    assert media(sent)[-1] == Media(action="pause")
    assert ends(last_screen(sent))[0] == "resume"
    assert last_screen(sent).tiles[0].label == "Play"
    walk(session, sched, sent, "resume", "restart", "volume_up")
    assert [m.action for m in media(sent)[-3:]] == ["resume", "restart", "volume_up"]
    assert ends(last_screen(sent))[0] == "pause"


def test_the_back_tile_stops_it_and_goes_up_one_level(session, sched, sent):
    walk(session, sched, sent, "computer", "youtube", "lofi", "back")
    assert media(sent)[-1] == Media(action="stop")
    screen = last_screen(sent)
    assert screen.screen == "menu" and screen.path == ["Computer", "YouTube"]
    assert session.media is None


def test_a_confirmed_double_blink_stops_it(session, sched, sent):
    walk(session, sched, sent, "computer", "youtube", "lofi")
    settle(sched)
    session.handle(DoubleBlink(t=0.0))
    settle(sched)
    session.handle(Clench(t=0.0, strength=1.0))  # the "Go back?" prompt is open: a clench goes back
    assert media(sent)[-1] == Media(action="stop")
    assert last_screen(sent).path == ["Computer", "YouTube"]


def test_reset_stops_it(session, sched, sent):
    walk(session, sched, sent, "computer", "youtube", "lofi")
    session.handle(Reset())
    assert media(sent)[-1] == Media(action="stop")
    assert last_screen(sent).path == []


def test_car_mode_stops_it(session, sched, sent):
    walk(session, sched, sent, "computer", "youtube", "lofi")
    session.handle(Settings(pointing_mode="scan", scan_ms=1000, trip=True))
    assert media(sent)[-1] == Media(action="stop")
    assert session.media is None
    assert last_screen(sent).screen == "trip"


def test_help_pauses_it_and_a_cancel_comes_back_to_the_player(session, sched, sent):
    walk(session, sched, sent, "computer", "youtube", "lofi")
    session.handle(LongClench(t=0.0, duration=2.6))
    assert session.state is SessionState.HELP_COUNTDOWN
    assert media(sent)[-1] == Media(action="pause")  # so the help line is heard
    session.handle(DoubleBlink(t=0.0))
    screen = last_screen(sent)
    assert screen.screen == "player" and ends(screen)[0] == "resume"


def test_help_still_fires_from_the_player_and_stops_it(session, sched, sent):
    walk(session, sched, sent, "computer", "youtube", "lofi")
    session.handle(LongClench(t=0.0, duration=2.6))
    sched.advance(HELP_COUNTDOWN_S + 0.1)
    assert session.state is not SessionState.HELP_COUNTDOWN
    assert media(sent)[-1] == Media(action="stop")
    assert last_screen(sent).path == []


def test_app_and_media_levels_ask_the_ai_for_nothing(menu):
    computer = next(c for c in menu.root.children if c.id == "computer")
    assert computer.fixed_only
    assert all(c.fixed_only for c in computer.children if c.id in ("youtube", "spotify"))
    assert not next(c for c in menu.root.children if c.id == "need").fixed_only


def test_media_play_needs_a_provider_and_an_id():
    with pytest.raises(ValidationError):
        parse_message({"type": "MEDIA", "action": "play", "provider": "youtube"})
    with pytest.raises(ValidationError):
        parse_message({"type": "MEDIA", "action": "pause", "provider": "youtube", "id": "jfKfPfyJRdk"})
    with pytest.raises(ValidationError):
        parse_message({"type": "MEDIA", "action": "play", "provider": "youtube", "id": "bad id!"})


def test_a_tile_without_a_picture_has_no_image_key():
    assert "image" not in Tile(id="need", label="I need", kind="branch").model_dump()
    with pytest.raises(ValidationError):
        Tile(id="x", label="x", kind="branch", image="http://insecure.example/x.jpg")
