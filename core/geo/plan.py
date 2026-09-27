"""Planning a new trip live (Car mode's "Plan a trip", /car-sim's address box): the same open-data
pipeline as the demo trip (core/geo/trip.py), from the configured pickup to any destination.

It runs in a worker thread with the Core's time limit around it; a layer that cannot be built is None
with a note, and the caller then keeps the committed demo trip (never made-up numbers).
"""

from __future__ import annotations

from core.geo.config import CACHE_DIR, GeoConfig, load_geo_config
from core.geo.http import DiskCache, OpenDataClient
from core.geo.model import Trip
from core.geo.trip import DEMO_TRIP_PATH, build_trip, load_trip, save_trip

PLACES_DIR = DEMO_TRIP_PATH.parent / "places"


def place_path(key: str):
    return DEMO_TRIP_PATH if key == "mdc" else PLACES_DIR / f"{key}.json"


def load_places(cfg: GeoConfig | None = None) -> dict[str, Trip]:
    """Every saved place's committed trip (data/geo/places/<key>.json; MDC Kendall = the demo trip).
    The board uses only these: picking a place is instant on any machine, with no live planning."""
    cfg = cfg or load_geo_config()
    out: dict[str, Trip] = {}
    for p in cfg.places:
        trip = load_trip(place_path(p.key))
        if trip is not None and trip.ride is not None and trip.ride.tiles:  # a drop-off may be unmapped
            out[p.key] = trip
    return out


def plan_trip(query: str, label: str, cfg: GeoConfig | None = None) -> Trip:
    """Layers 1 and 2 for `query` (a Nominatim search), shown as `label`. Blocking: call in a thread."""
    cfg = cfg or load_geo_config()
    demo = cfg.demo_trip.model_copy(update={"destination": label, "destination_query": query, "destination_osm_way": None})
    planned = cfg.model_copy(update={"demo_trip": demo})
    http = OpenDataClient(planned.http, DiskCache(CACHE_DIR), live=True)
    try:
        return build_trip(planned, http)
    finally:
        http.close()
