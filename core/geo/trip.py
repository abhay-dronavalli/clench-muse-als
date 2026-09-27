"""Builds the demo trip result from open data, and loads the committed copy.

  build_trip(cfg, http)  geocode both ends (Nominatim), then Layer 2 (drop-off: Overpass + USGS
                         EPQS) and Layer 1 (routes: OSRM + Overpass + OpenTopoData).
  load_trip()            the committed data/geo/demo_trip.json, so the demo works with no network.

A layer that cannot be built (a service down and nothing cached) is None with a note saying why;
it never gets placeholder numbers.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

from core.geo.comfort import build_routes as _build_routes
from core.geo.config import GEO_DIR, GeoConfig
from core.geo.dropoff import dropoff_request, rank_candidates
from core.geo.http import FetchError, OpenDataClient
from core.geo.model import Dropoff, LatLng, Routes, Trip
from core.geo.geometry import LatLon
from core.geo.sources import OSM_ATTRIBUTION, USGS_ATTRIBUTION, dropoff_query, elevations_epqs, geocode, overpass, way_centre

log = logging.getLogger("clench.geo")

DEMO_TRIP_PATH = GEO_DIR / "demo_trip.json"
DURATION_NOTE = "Drive times are OSRM free-flow estimates from OpenStreetMap road speeds, without live traffic."


def build_dropoff(cfg: GeoConfig, http: OpenDataClient) -> Dropoff:
    center = destination_point(cfg, http)
    osm = overpass(http, dropoff_query(center, cfg.dropoff.radius_m))
    candidates, notes = rank_candidates(
        osm, center, cfg.dropoff, cfg.unknown_penalty, elevation=lambda pts: elevations_epqs(http, pts),
        destination_way=cfg.demo_trip.destination_osm_way,
    )
    return Dropoff(
        destination=cfg.demo_trip.destination,
        destination_point=LatLng.of(center),
        radius_m=cfg.dropoff.radius_m,
        candidates=candidates,
        request=dropoff_request(candidates),
        osm_fetched_at=osm.fetched_at,
        notes=notes,
    )


def destination_point(cfg: GeoConfig, http: OpenDataClient) -> LatLon:
    if cfg.demo_trip.destination_osm_way is not None:
        return way_centre(http, cfg.demo_trip.destination_osm_way)
    return geocode(http, cfg.demo_trip.destination_query).point


def build_routes(cfg: GeoConfig, http: OpenDataClient, dropoff: Dropoff | None = None) -> Routes:
    """Routes end at the chosen drop-off point when Layer 2 found one, else at the destination."""
    if dropoff is not None and dropoff.request is not None:
        return _build_routes(cfg, http, dropoff.request.point.tuple(), f"{cfg.demo_trip.destination} (drop-off: {dropoff.request.description})")
    return _build_routes(cfg, http, destination_point(cfg, http))


def build_trip(cfg: GeoConfig, http: OpenDataClient, *, layers: tuple[str, ...] = ("dropoff", "ride")) -> Trip:
    notes: list[str] = []
    dropoff = ride = None
    if "dropoff" in layers:
        try:
            dropoff = build_dropoff(cfg, http)
        except FetchError as e:
            notes.append(f"Drop-off not computed: {e}")
    if "ride" in layers:
        try:
            ride = build_routes(cfg, http, dropoff)
        except FetchError as e:
            notes.append(f"Routes not computed: {e}")
    return Trip(
        generated_at=datetime.now(UTC).isoformat(timespec="seconds"),
        attribution=[OSM_ATTRIBUTION, USGS_ATTRIBUTION],
        duration_note=DURATION_NOTE,
        dropoff=dropoff,
        ride=ride,
        notes=notes + http.notes,
    )


def save_trip(trip: Trip, path: Path = DEMO_TRIP_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # newline="\n": LF on Windows too (.gitattributes wants LF; the file is committed).
    path.write_text(json.dumps(trip.model_dump(), indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")


def load_trip(path: Path = DEMO_TRIP_PATH) -> Trip | None:
    try:
        return Trip.model_validate_json(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
