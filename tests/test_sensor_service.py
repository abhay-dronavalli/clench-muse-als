"""The console's Muse Sensor Service supervisor (core/sensor_service.py)."""

import json
import sys
import textwrap
import time

import pytest
from fastapi.testclient import TestClient

from core.clock import AsyncioScheduler
from core.contracts import Settings
from core.main import create_app
from core.sensor_service import SensorService, available_profiles


def make_client(tmp_path):
    # A very slow scan so the highlight never moves during the test.
    return TestClient(create_app(db_path=tmp_path / "test.db", scheduler=AsyncioScheduler(),
                                 scan_ms=600_000, lang="en"))


@pytest.fixture
def profiles(tmp_path):
    for name in ("taher", "maria"):
        (tmp_path / f"calibration.{name}.json").write_text(json.dumps({"board": "MUSE_2_BOARD"}))
    return tmp_path


def test_available_profiles_lists_calibration_files(profiles):
    assert sorted(available_profiles(profiles)) == ["maria", "taher"]


def test_available_profiles_ignores_other_files(tmp_path):
    (tmp_path / "notes.json").write_text("{}")
    (tmp_path / "calibration.bad name.json").write_text("{}")
    assert available_profiles(tmp_path) == []


def test_stop_is_safe_when_nothing_is_running():
    service = SensorService()
    status = service.stop()
    assert status["running"] is False and status["pid"] is None


def test_start_rejects_an_unknown_source():
    with pytest.raises(ValueError, match="source must be"):
        SensorService().start("ws://127.0.0.1:8000/ws/sensor", "taher", "telepathy")


@pytest.mark.parametrize("name", ["../secrets", "taher; rm -rf /", "", "a" * 33])
def test_start_rejects_a_profile_name_that_is_not_a_plain_name(name):
    """The name reaches a command line, so it must not carry a path or an extra argument."""
    with pytest.raises(ValueError, match="profile must contain"):
        SensorService().start("ws://127.0.0.1:8000/ws/sensor", name, "muse")


def test_start_rejects_a_missing_calibration_profile(profiles, monkeypatch):
    monkeypatch.setattr("core.sensor_service.PROFILE_DIR", profiles)
    with pytest.raises(ValueError, match="no calibration profile named 'luis'"):
        SensorService().start("ws://127.0.0.1:8000/ws/sensor", "luis", "muse")


def fake_sensor_package(tmp_path, body):
    """A stand-in `sensor.main` so the tests never touch BrainFlow or Bluetooth."""
    package = tmp_path / "sensor"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "__main__.py").write_text(textwrap.dedent(body))
    (package / "main.py").write_text(textwrap.dedent(body))
    return tmp_path


def test_start_runs_the_subprocess_and_keeps_its_output(tmp_path):
    cwd = fake_sensor_package(tmp_path, """
        import sys, time
        print('args ' + ' '.join(sys.argv[1:]), flush=True)
        time.sleep(30)
    """)
    service = SensorService(python=sys.executable, cwd=cwd)
    status = service.start("ws://127.0.0.1:8002/ws/sensor", "demo", "demo")
    try:
        assert status["running"] is True and status["profile"] == "demo"
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not any("args" in line for line in service.status()["log"]):
            time.sleep(0.05)
        logged = "\n".join(service.status()["log"])
        # The subprocess must dial back the port the Core is really bound to.
        assert "--url ws://127.0.0.1:8002/ws/sensor" in logged
    finally:
        service.stop()
    assert service.running() is False


def test_start_replaces_a_running_service_so_the_headband_has_one_owner(tmp_path):
    """Two BrainFlow clients cannot share the headband, so starting again must not stack."""
    cwd = fake_sensor_package(tmp_path, "import time; time.sleep(30)")
    service = SensorService(python=sys.executable, cwd=cwd)
    first = service.start("ws://127.0.0.1:8002/ws/sensor", "demo", "demo")
    try:
        second = service.start("ws://127.0.0.1:8002/ws/sensor", "demo", "demo")
        assert first["pid"] != second["pid"]
        assert service.running() is True
    finally:
        service.stop()


def test_exit_code_is_reported_when_the_service_dies_on_its_own(tmp_path):
    """No headband means the subprocess exits; the console has to be able to say so."""
    cwd = fake_sensor_package(tmp_path, """
        import sys
        print('BOARD_NOT_READY_ERROR:7', flush=True)
        sys.exit(3)
    """)
    service = SensorService(python=sys.executable, cwd=cwd)
    service.start("ws://127.0.0.1:8002/ws/sensor", "demo", "demo")
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and service.running():
        time.sleep(0.05)
    status = service.status()
    assert status["running"] is False and status["exit_code"] == 3
    assert any("BOARD_NOT_READY_ERROR" in line for line in status["log"])


def test_api_reports_status_and_rejects_a_bad_profile(tmp_path):
    with make_client(tmp_path) as client:
        status = client.get("/api/sensor").json()
        assert status["running"] is False
        bad = client.post("/api/sensor/start", json={"profile": "../etc", "source": "muse"})
        assert bad.status_code == 400
        assert "profile must contain" in bad.json()["detail"]


def test_api_stop_pauses_muse_input(tmp_path):
    """Losing the headband must never leave the board accepting stale clenches."""
    with make_client(tmp_path) as client:
        session = client.app.state.session
        session.handle(Settings(**dict(session.settings().model_dump(), muse_enabled=True)))
        assert session.muse_enabled is True
        assert client.post("/api/sensor/stop").status_code == 200
        assert session.muse_enabled is False
