from fastapi.testclient import TestClient

from core.clock import AsyncioScheduler
from core.main import create_app
from core.suggest.fake import FakeProvider


class FakeClock:
    """Real timers, but a debounce clock the test moves by hand."""

    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def make_client(clock: FakeClock) -> TestClient:
    # A very slow scan so the highlight never moves during the test.
    return TestClient(create_app(scheduler=AsyncioScheduler(clock=clock), scan_ms=600_000, lang="en"))


def test_board_gets_confirm_then_speak():
    clock = FakeClock()
    with make_client(clock) as client:
        with client.websocket_connect("/ws/board") as board, client.websocket_connect("/ws/input") as inp:
            assert board.receive_json()["type"] == "SETTINGS"
            board.send_json({"type": "READY"})
            home = board.receive_json()
            assert home["type"] == "SCREEN"
            assert home["tiles"][0]["id"] == "suggested"
            assert home["highlight"] == 0

            def clench():
                clock.t += 1.0  # past the 300 ms debounce
                inp.send_json({"type": "CLENCH", "t": clock.t, "strength": 1.0})

            clench()  # Suggested
            echo = board.receive_json()  # the picked tile is said first (speak picks)
            assert echo == {"type": "SPEAK", "id": echo["id"], "kind": "echo", "text": "Suggested", "lang": "en"}
            level = board.receive_json()
            assert level["type"] == "SCREEN"
            assert level["path"] == ["Suggested"]

            clench()  # I'm hungry
            assert board.receive_json()["kind"] == "echo"
            board.send_json({"type": "AUDIO_DONE", "id": echo["id"]})  # echoes change nothing
            confirm = board.receive_json()
            assert confirm == {
                "type": "CONFIRM",
                "text": "I'm hungry. What's for lunch?",
                "action": "speak",
            }

            clench()  # confirm
            speak = board.receive_json()
            assert speak == {
                "type": "SPEAK",
                "id": speak["id"],
                "kind": "phrase",
                "text": "I'm hungry. What's for lunch?",
                "lang": "en",
            }

            board.send_json({"type": "AUDIO_DONE", "id": "someone-else"})  # not the phrase: ignored
            board.send_json({"type": "AUDIO_DONE", "id": speak["id"]})
            back_home = board.receive_json()
            assert back_home["type"] == "SCREEN"
            assert back_home["path"] == []


def test_reset_is_accepted_from_the_board_and_the_dev_panel():
    clock = FakeClock()
    with make_client(clock) as client:
        with client.websocket_connect("/ws/board") as board, client.websocket_connect("/ws/input") as inp:
            assert board.receive_json()["type"] == "SETTINGS"
            clock.t += 1.0
            inp.send_json({"type": "CLENCH", "t": clock.t, "strength": 1.0})  # Suggested
            assert board.receive_json()["kind"] == "echo"
            assert board.receive_json()["path"] == ["Suggested"]
            board.send_json({"type": "RESET"})  # "Click to start"
            home = board.receive_json()
            assert (home["type"], home["path"], home["highlight"]) == ("SCREEN", [], 0)
            clock.t += 1.0
            inp.send_json({"type": "CLENCH", "t": clock.t, "strength": 1.0})
            assert board.receive_json()["kind"] == "echo"
            assert board.receive_json()["path"] == ["Suggested"]
            inp.send_json({"type": "RESET"})  # the dev panel's "Reset to Home"
            assert board.receive_json()["path"] == []


def test_invalid_messages_do_not_close_the_connection():
    clock = FakeClock()
    with make_client(clock) as client:
        with client.websocket_connect("/ws/board") as board, client.websocket_connect("/ws/input") as inp:
            inp.send_text("not json")
            inp.send_json([1, 2, 3])
            inp.send_json({"type": "CLENCH", "strength": 5})
            inp.send_json({"type": "SCREEN", "screen": "menu", "tiles": [], "highlight": None, "lang": "en", "path": []})
            inp.send_bytes(b"\x00")
            board.send_json({"type": "CLENCH", "t": 1.0, "strength": 1.0})  # wrong route
            # Both sockets still work.
            assert board.receive_json()["type"] == "SETTINGS"
            board.send_json({"type": "READY"})
            assert board.receive_json()["path"] == []
            clock.t += 1.0
            inp.send_json({"type": "CLENCH", "t": clock.t, "strength": 1.0})
            assert board.receive_json()["kind"] == "echo"
            assert board.receive_json()["path"] == ["Suggested"]


def test_console_mirrors_board_and_sends_settings():
    clock = FakeClock()
    with make_client(clock) as client:
        with client.websocket_connect("/ws/console") as console:
            assert console.receive_json()["type"] == "SETTINGS"
            console.send_json({"type": "SETTINGS", "pointing_mode": "scan", "scan_ms": 600_000, "lang": "es"})
            assert console.receive_json()["lang"] == "es"  # the new settings first, then the view
            screen = console.receive_json()
            assert screen["type"] == "SCREEN"
            assert screen["lang"] == "es"
            assert screen["tiles"][1]["label"] == "Necesito"
        health = client.get("/health").json()
        assert health["lang"] == "es"
        assert health["state"] == "SCANNING"


def test_settings_are_announced_on_connect_and_after_every_change():
    clock = FakeClock()
    with make_client(clock) as client:
        with (
            client.websocket_connect("/ws/board") as board,
            client.websocket_connect("/ws/console") as console,
            client.websocket_connect("/ws/input") as inp,
        ):
            current = {
                "type": "SETTINGS", "pointing_mode": "auto", "scan_ms": 600_000, "lang": "en", "speak_picks": True,
                "learning": True, "long_clench_ms": 2500,
            }
            for ws in (board, console, inp):
                assert ws.receive_json() == current
            inp.send_json({"type": "SETTINGS", "pointing_mode": "scan", "scan_ms": 700, "speak_picks": False})
            changed = {**current, "pointing_mode": "scan", "scan_ms": 700, "speak_picks": False}
            for ws in (board, console, inp):  # the sender too, so every dev panel shows the truth
                assert ws.receive_json() == changed


def test_no_key_runs_with_fixed_phrases():
    clock = FakeClock()
    with make_client(clock) as client:
        health = client.get("/health").json()
        assert health["ai"] == "off (GEMINI_API_KEY missing): fixed phrases only"
        assert health["ai_calls"] == 0
        with client.websocket_connect("/ws/board") as board:
            board.receive_json()  # SETTINGS
            board.send_json({"type": "READY"})
            home = board.receive_json()
            assert [t["kind"] for t in home["tiles"]] == ["branch"] * 5 + ["other"]
            assert home["loading"] is False


def test_fake_provider_suggests_in_the_app():
    clock = FakeClock()
    app = create_app(scheduler=AsyncioScheduler(clock=clock), scan_ms=600_000, lang="en", provider=FakeProvider())
    with TestClient(app) as client:
        assert client.get("/health").json()["ai"] == "fake (fake)"
        with client.websocket_connect("/ws/board") as board, client.websocket_connect("/ws/input") as inp:
            board.receive_json()  # SETTINGS
            board.send_json({"type": "READY"})
            board.receive_json()  # home
            clock.t += 1.0
            inp.send_json({"type": "CLENCH", "t": clock.t, "strength": 1.0})  # Suggested
            assert board.receive_json()["kind"] == "echo"
            screen = board.receive_json()
            while screen.get("loading"):  # the prefetch may still be on its way
                screen = board.receive_json()
            assert screen["path"] == ["Suggested"]
            assert [t["kind"] for t in screen["tiles"]][:3] == ["suggestion"] * 3
            assert screen["tiles"][-1] == {"id": "suggested.other", "label": "Other...", "kind": "other"}
        assert client.get("/health").json()["ai_calls"] > 0


def test_serves_cached_audio_only(tmp_path):
    name = "a" * 64 + ".mp3"
    (tmp_path / name).write_bytes(b"ID3fake")
    (tmp_path / "notes.txt").write_text("secret")
    app = create_app(scheduler=AsyncioScheduler(), scan_ms=600_000, audio_dir=tmp_path)
    with TestClient(app) as client:
        resp = client.get(f"/audio/{name}")
        assert resp.status_code == 200
        assert resp.content == b"ID3fake"
        assert resp.headers["content-type"] == "audio/mpeg"
        assert client.get("/audio/" + "b" * 64 + ".mp3").status_code == 404  # not cached
        assert client.get("/audio/notes.txt").status_code == 404  # only <sha256>.mp3 names
        assert client.get("/audio/..%2F..%2Fpyproject.toml").status_code == 404
        health = client.get("/health").json()
        assert health["voice"] == "browser speech (ELEVENLABS_API_KEY, ELEVENLABS_VOICE_ID missing)"
        assert health["speak_picks"] is True
        assert health["learning"] is True and health["jev"] == "off"
