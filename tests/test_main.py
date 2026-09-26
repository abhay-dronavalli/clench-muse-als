from fastapi.testclient import TestClient

from core.clock import AsyncioScheduler
from core.main import create_app


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
            board.send_json({"type": "READY"})
            home = board.receive_json()
            assert home["type"] == "SCREEN"
            assert home["tiles"][0]["id"] == "suggested"
            assert home["highlight"] == 0

            def clench():
                clock.t += 1.0  # past the 300 ms debounce
                inp.send_json({"type": "CLENCH", "t": clock.t, "strength": 1.0})

            clench()  # Suggested
            level = board.receive_json()
            assert level["type"] == "SCREEN"
            assert level["path"] == ["Suggested"]

            clench()  # I'm hungry
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
            board.send_json({"type": "READY"})
            assert board.receive_json()["path"] == []
            clock.t += 1.0
            inp.send_json({"type": "CLENCH", "t": clock.t, "strength": 1.0})
            assert board.receive_json()["path"] == ["Suggested"]


def test_console_mirrors_board_and_sends_settings():
    clock = FakeClock()
    with make_client(clock) as client:
        with client.websocket_connect("/ws/console") as console:
            console.send_json({"type": "SETTINGS", "pointing_mode": "scan", "scan_ms": 600_000, "lang": "es"})
            screen = console.receive_json()
            assert screen["type"] == "SCREEN"
            assert screen["lang"] == "es"
            assert screen["tiles"][1]["label"] == "Necesito"
        health = client.get("/health").json()
        assert health["lang"] == "es"
        assert health["state"] == "SCANNING"


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
