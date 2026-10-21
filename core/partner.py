"""Opt-in partner API. AI credentials can read and propose, never approve."""

from __future__ import annotations

import secrets
import time
from collections.abc import Mapping

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict

from core.contracts import Lang, Settings
from core.session import SessionState


class LanguageProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lang: Lang


def build_partner_router(env: Mapping[str, str]) -> APIRouter:
    reader = env.get("CLENCH_PARTNER_TOKEN", "")
    approver = env.get("CLENCH_CAREGIVER_TOKEN", "")
    if reader and approver and secrets.compare_digest(reader, approver):
        raise ValueError("Partner and caregiver tokens must be different")
    proposals: dict[str, dict] = {}

    def authorize(token: str, authorization: str | None) -> None:
        if not token:
            raise HTTPException(404, "Partner capability is disabled")
        if not authorization or not secrets.compare_digest(
            authorization.encode(), f"Bearer {token}".encode()
        ):
            raise HTTPException(401, "Invalid credentials", headers={"WWW-Authenticate": "Bearer"})

    def read_access(authorization: str | None = Header(default=None)) -> None:
        authorize(reader, authorization)

    def approve_access(authorization: str | None = Header(default=None)) -> None:
        authorize(approver, authorization)

    router = APIRouter(prefix="/api/partner", tags=["partner"])

    def prune() -> None:
        now = time.time()
        for key in list(proposals):
            if proposals[key]["expires_at"] <= now:
                del proposals[key]

    @router.get("/status", dependencies=[Depends(read_access)])
    async def status(request: Request) -> dict:
        state = request.app.state
        return {
            "core_online": True,
            "board_connections": state.hub.count("board"),
            "session_state": state.session.state.value,
            "scope": "One shared patient session; connections are not identified devices",
        }

    @router.get("/board", dependencies=[Depends(read_access)])
    async def board(request: Request) -> dict:
        session = request.app.state.session
        return {
            "lang": session.lang,
            "pointing_mode": session.pointing_mode,
            "scan_ms": session.scan_ms,
            "supported_languages": ["en", "es"],
        }

    @router.post("/proposals", dependencies=[Depends(read_access)], status_code=201)
    async def propose(body: LanguageProposal, request: Request) -> dict:
        prune()
        if len(proposals) >= 100:
            raise HTTPException(429, "Too many pending proposals; wait for expiry")
        session = request.app.state.session
        proposal = {
            "id": secrets.token_urlsafe(24),
            "before": {"lang": session.lang},
            "after": {"lang": body.lang},
            "expires_at": time.time() + 600,
            "status": "pending_caregiver_review",
            "effect": "Switch existing bilingual labels and phrases for this session; no custom tiles",
        }
        proposals[proposal["id"]] = proposal
        return proposal

    @router.get("/proposals", dependencies=[Depends(approve_access)])
    async def review() -> list[dict]:
        prune()
        return list(proposals.values())

    @router.post("/proposals/{proposal_id}/approve", dependencies=[Depends(approve_access)])
    async def approve(proposal_id: str, request: Request) -> dict:
        prune()
        proposal = proposals.get(proposal_id)
        if proposal is None:
            raise HTTPException(404, "Proposal expired or not found")
        session = request.app.state.session
        if session.lang != proposal["before"]["lang"]:
            raise HTTPException(409, "Board language changed; create and review a fresh proposal")
        if (session.state is not SessionState.SCANNING or session.onboarding
                or session.trip or session._back is not None):
            raise HTTPException(409, "Wait until the communication board is scanning")
        # No await between validation and application: help/input cannot interleave.
        settings = session.settings().model_dump()
        settings["lang"] = proposal["after"]["lang"]
        session.handle(Settings(**settings))
        del proposals[proposal_id]
        return {**proposal, "status": "applied", "persistent": False}

    @router.delete("/proposals/{proposal_id}", dependencies=[Depends(approve_access)])
    async def reject(proposal_id: str) -> dict:
        prune()
        if proposals.pop(proposal_id, None) is None:
            raise HTTPException(404, "Proposal expired or not found")
        return {"id": proposal_id, "status": "rejected"}

    return router
