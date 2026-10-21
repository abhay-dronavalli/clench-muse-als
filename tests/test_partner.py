import time

import pytest
from fastapi.testclient import TestClient

from core.main import create_app
from core.contracts import LongClench, Settings
from core.session import SessionState


READER = {"Authorization": "Bearer reader-test-token"}
CAREGIVER = {"Authorization": "Bearer caregiver-test-token"}
ENV = {"CLENCH_PARTNER_TOKEN": "reader-test-token", "CLENCH_CAREGIVER_TOKEN": "caregiver-test-token"}


@pytest.fixture
def client():
    with TestClient(create_app(env=ENV, lang="en", scan_ms=600_000)) as client:
        yield client


def propose(client, lang="es"):
    response = client.post("/api/partner/proposals", headers=READER, json={"lang": lang})
    assert response.status_code == 201
    return response.json()


def test_disabled_and_distinct_credentials():
    with TestClient(create_app()) as client:
        assert client.get("/api/partner/status", headers=READER).status_code == 404
    with pytest.raises(ValueError, match="different"):
        create_app(env={"CLENCH_PARTNER_TOKEN": "same", "CLENCH_CAREGIVER_TOKEN": "same"})


def test_status_tracks_board_connections_and_restricts_data(client):
    assert client.get("/api/partner/status").status_code == 401
    assert client.get("/api/partner/status", headers=CAREGIVER).status_code == 401
    assert client.get("/api/partner/status", headers=READER).json()["board_connections"] == 0
    with client.websocket_connect("/ws/board") as board:
        board.receive_json()
        assert client.get("/api/partner/status", headers=READER).json()["board_connections"] == 1
    settings = client.get("/api/partner/board", headers=READER).json()
    assert set(settings) == {"lang", "pointing_mode", "scan_ms", "supported_languages"}


def test_proposal_review_apply_and_replay(client):
    with client.websocket_connect("/ws/board") as board:
        board.receive_json()
        proposal = propose(client)
        assert client.app.state.session.lang == "en"
        url = f'/api/partner/proposals/{proposal["id"]}/approve'
        assert client.post(url, headers=READER).status_code == 401
        assert client.get("/api/partner/proposals", headers=CAREGIVER).json() == [proposal]
        response = client.post(url, headers=CAREGIVER)
        assert response.status_code == 200
        assert response.json()["status"] == "applied"
        assert client.app.state.session.lang == "es"
        # The normal SETTINGS broadcast updates the actual board.
        assert board.receive_json()["lang"] == "es"
        assert client.post(url, headers=CAREGIVER).status_code == 404


@pytest.mark.parametrize("body", [{"lang": "fr"}, {"lang": "es", "action": "speak"}, {}])
def test_invalid_proposal(client, body):
    assert client.post("/api/partner/proposals", headers=READER, json=body).status_code == 422


def test_stale_and_expired_proposals(client, monkeypatch):
    proposal = propose(client)
    session = client.app.state.session
    session.handle(Settings(**{**session.settings().model_dump(), "lang": "es"}))
    url = f'/api/partner/proposals/{proposal["id"]}/approve'
    assert client.post(url, headers=CAREGIVER).status_code == 409
    monkeypatch.setattr("core.partner.time.time", lambda: proposal["expires_at"] + 1)
    assert client.post(url, headers=CAREGIVER).status_code == 404


@pytest.mark.parametrize("state", [SessionState.CONFIRMING, SessionState.SPEAKING,
    SessionState.LOADING, SessionState.COMPUTER, SessionState.ACTING])
def test_busy_session_rejects_approval(client, state):
    proposal = propose(client)
    client.app.state.session.state = state
    assert client.post(f'/api/partner/proposals/{proposal["id"]}/approve', headers=CAREGIVER).status_code == 409
    assert client.app.state.session.lang == "en"


def test_help_remains_independent(client):
    proposal = propose(client)
    session = client.app.state.session
    client.portal.call(session.handle, LongClench(t=time.time(), duration=3))
    assert session.state is SessionState.HELP_COUNTDOWN
    assert client.post(f'/api/partner/proposals/{proposal["id"]}/approve', headers=CAREGIVER).status_code == 409
    assert session.state is SessionState.HELP_COUNTDOWN


def test_reject_and_capacity(client):
    proposal = propose(client)
    url = f'/api/partner/proposals/{proposal["id"]}'
    assert client.delete(url, headers=READER).status_code == 401
    assert client.delete(url, headers=CAREGIVER).status_code == 200
    assert client.post(url + "/approve", headers=CAREGIVER).status_code == 404
    for _ in range(100):
        propose(client)
    assert client.post("/api/partner/proposals", headers=READER, json={"lang": "es"}).status_code == 429
