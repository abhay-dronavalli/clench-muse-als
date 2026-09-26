"""The real-service scripts in scripts/ must do nothing real unless run with --send."""

import importlib.util
from pathlib import Path
from types import ModuleType

import httpx
import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"script_{name}", SCRIPTS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def no_network(monkeypatch):
    """Any HTTP request (Telegram, Twilio, ElevenLabs, Gemini) fails the test."""

    def refuse(*args, **kwargs):
        raise AssertionError("a script made a network request without --send")

    monkeypatch.setattr(httpx.AsyncClient, "send", refuse)
    monkeypatch.setattr(httpx.Client, "send", refuse)


@pytest.mark.parametrize(
    ("name", "argv"),
    [
        ("test_telegram", []),
        ("test_telegram", ["carlos"]),
        ("test_call", []),
        ("test_call", ["carlos", "en"]),
        ("test_voice", []),
        ("test_gemini", []),
    ],
)
def test_scripts_do_nothing_without_send(name, argv, no_network, monkeypatch, capsys):
    module = load(name)
    # Belt and braces: the pieces that would reach a service or write the database must not run.
    for attr in ("TelegramMessageAction", "TwilioCallAction", "Db", "build_tts", "build_provider"):
        if hasattr(module, attr):
            monkeypatch.setattr(module, attr, lambda *a, **k: pytest.fail(f"{name} used {attr} without --send"))
    assert module.main(argv) == 0
    out = capsys.readouterr().out
    assert "--send" in out
    assert "OK" not in out
