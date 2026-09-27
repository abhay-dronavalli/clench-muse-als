"""The /api/geo routes and scripts/geo_trip.py (no network without --send)."""

import importlib.util
import sys
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.geo import api, trip
from core.geo.model import Trip

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def no_network(monkeypatch):
    """Real HTTP fails the test. (TestClient has its own in-process transport, so it still works.)"""

    def refuse(*args, **kwargs):
        raise AssertionError("made a network request")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", refuse)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", refuse)


@pytest.fixture
def clock(monkeypatch):
    """A monotonic clock for the live gate: each reading is 10 s after the last, so the gate never
    trips unless a test pins the time."""
    now = [0.0]

    def tick():
        now[0] += 10
        return now[0]

    monkeypatch.setattr(api.time, "monotonic", tick)


def app(env=None) -> TestClient:
    a = FastAPI()
    a.include_router(api.build_geo_router(env or {}))
    return TestClient(a)


def test_committed_demo_trip_is_valid_and_served_offline(no_network):
    saved = trip.load_trip()
    assert saved is not None, "data/geo/demo_trip.json is missing: run scripts/geo_trip.py --send"
    got = app().get("/api/geo/trip")
    assert got.status_code == 200
    body = Trip.model_validate(got.json())
    assert body.dropoff and body.dropoff.candidates and body.dropoff.request
    assert body.ride and body.ride.tiles
    assert any("OpenStreetMap" in a for a in body.attribution)
    assert "without live traffic" in body.duration_note


def test_committed_result_holds_no_google_content():
    text = (ROOT / "data" / "geo" / "demo_trip.json").read_text(encoding="utf-8").lower()
    for word in ("google", "streetview", "street view", "place_id", "pano"):
        assert word not in text


def test_missing_demo_trip_is_a_clear_404(monkeypatch, tmp_path):
    monkeypatch.setattr(api, "load_trip", lambda: None)
    got = app().get("/api/geo/trip")
    assert got.status_code == 404 and "geo_trip.py --send" in got.json()["detail"]


def test_live_routes_without_a_google_key_are_503(no_network, clock):
    candidate = trip.load_trip().dropoff.candidates[0].id
    client = app({})
    assert client.get("/api/geo/live/place").status_code == 503
    assert client.get(f"/api/geo/live/candidate/{candidate}").status_code == 503


def test_unknown_candidate_is_404_before_any_google_call(no_network, clock):
    assert app({"GOOGLE_MAPS_API_KEY": "x"}).get("/api/geo/live/candidate/nope").status_code == 404


def test_live_calls_are_rate_limited(monkeypatch, no_network):
    monkeypatch.setattr(api.time, "monotonic", lambda: 100.0)
    client = app({})
    assert client.get("/api/geo/live/place").status_code == 503  # passed the gate, then no key
    assert client.get("/api/geo/live/place").status_code == 429


def test_refresh_with_every_service_down_falls_back_to_the_saved_result(monkeypatch, tmp_path):
    def down(self, request):
        raise httpx.ConnectError("offline", request=request)

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", down)
    monkeypatch.setattr(api, "CACHE_DIR", tmp_path)  # nothing cached, and every request fails
    monkeypatch.setattr("core.geo.http.time.sleep", lambda s: None)
    got = app().get("/api/geo/trip?refresh=true")
    assert got.status_code == 200
    body = got.json()
    assert any("Showing the saved result" in n for n in body["notes"])
    assert any("not computed" in n for n in body["notes"])


# --- scripts/geo_trip.py ------------------------------------------------------------------------


def load_script():
    spec = importlib.util.spec_from_file_location("script_geo_trip", ROOT / "scripts" / "geo_trip.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_script_without_send_makes_no_request_and_writes_nothing(monkeypatch, tmp_path, capsys, no_network):
    script = load_script()
    monkeypatch.setattr(script, "CACHE_DIR", tmp_path / "empty-cache")
    monkeypatch.setattr(script, "save_trip", lambda *a, **k: pytest.fail("wrote the demo trip without --send"))
    assert script.main([]) == 0
    out = capsys.readouterr().out
    assert "--send" in out
    assert "not computed" in out  # an empty cache means no layers, stated plainly


def test_script_rebuilds_the_committed_numbers_from_cache(monkeypatch, capsys, no_network):
    """With the local cache present (a developer machine), the offline rebuild reproduces the
    committed candidates and routes exactly. Skipped where the cache is absent (CI)."""
    from core.geo.config import CACHE_DIR

    if not any(CACHE_DIR.glob("*/*.json")):
        pytest.skip("no local open-data cache")
    script = load_script()
    monkeypatch.setattr(script, "save_trip", lambda *a, **k: pytest.fail("wrote without --send"))
    assert script.main([]) == 0
    saved = trip.load_trip()
    out = capsys.readouterr().out
    if "not computed" in out:
        pytest.skip("the local cache does not cover the demo trip")
    for c in saved.dropoff.candidates:
        assert f"#{c.rank} {c.score:5.1f}" in out
    for t in saved.ride.tiles:
        assert f"TILE {t.label}: {t.detail}" in out
