"""Internal endpoints for our own web app (/trip and /trip/live).

  GET /api/geo/trip                     the committed demo result (data/geo/demo_trip.json);
                                        ?refresh=true recomputes from open data (cached responses
                                        first; a failed service falls back to its cache with a note;
                                        with nothing usable, the committed result plus a note)
  GET /api/geo/live/place               Google Places accessibility for the destination (live)
  GET /api/geo/live/candidate/{id}      Street View + Gemini for one drop-off candidate (live)

The two live routes spend Google credit and never store anything (docs/decisions.md #25). They
are rate-limited per process so a reloading page cannot run up a bill.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Mapping
from typing import Any

from fastapi import APIRouter, HTTPException

from core.geo.config import CACHE_DIR, GeoConfig, load_geo_config
from core.geo.google import GoogleError, GoogleLive
from core.geo.http import DiskCache, OpenDataClient
from core.geo.live import candidate_evidence, place_evidence
from core.geo.model import Candidate, Trip
from core.geo.trip import build_trip, load_trip
from core.geo.vision import RampClassifier, VisionError

LIVE_MIN_GAP_S = 2.0


class _Gate:
    """At most one live Google call every LIVE_MIN_GAP_S seconds."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._last = -LIVE_MIN_GAP_S

    def check(self) -> None:
        with self._lock:
            now = time.monotonic()
            if now - self._last < LIVE_MIN_GAP_S:
                raise HTTPException(status_code=429, detail="Live Google checks are limited to one every 2 seconds.")
            self._last = now


def build_geo_router(env: Mapping[str, str], cfg: GeoConfig | None = None) -> APIRouter:
    cfg = cfg or load_geo_config()
    router = APIRouter(prefix="/api/geo")
    gate = _Gate()

    def committed() -> Trip:
        trip = load_trip()
        if trip is None:
            raise HTTPException(status_code=404, detail="No demo trip yet: run uv run python scripts/geo_trip.py --send")
        return trip

    @router.get("/trip")
    def trip(refresh: bool = False) -> Trip:
        if not refresh:
            return committed()
        http = OpenDataClient(cfg.http, DiskCache(CACHE_DIR), live=True, refresh=True)
        try:
            fresh = build_trip(cfg, http)
        finally:
            http.close()
        if fresh.dropoff is None and fresh.ride is None:
            old = committed()
            old.notes = [*fresh.notes, f"Showing the saved result from {old.generated_at}."]
            return old
        return fresh

    def google() -> GoogleLive:
        try:
            return GoogleLive(env.get("GOOGLE_MAPS_API_KEY", ""))
        except GoogleError as e:
            raise HTTPException(status_code=503, detail=str(e)) from None

    def candidate(candidate_id: str) -> Candidate:
        trip = committed()
        found = next((c for c in (trip.dropoff.candidates if trip.dropoff else []) if c.id == candidate_id), None)
        if found is None:
            raise HTTPException(status_code=404, detail=f"No drop-off candidate {candidate_id}")
        return found

    @router.get("/live/place")
    def live_place() -> dict[str, Any]:
        gate.check()
        g = google()
        try:
            return place_evidence(g, cfg.demo_trip.destination_query)
        finally:
            g.close()

    @router.get("/live/candidate/{candidate_id}")
    def live_candidate(candidate_id: str) -> dict[str, Any]:
        c = candidate(candidate_id)
        gate.check()
        g = google()
        try:
            vision = RampClassifier(env.get("GEMINI_API_KEY", ""), env.get("GEMINI_MODEL") or None)
        except VisionError:
            vision = None
        try:
            return {"candidate": c.model_dump(), **candidate_evidence(g, vision, c)}
        finally:
            g.close()

    return router
