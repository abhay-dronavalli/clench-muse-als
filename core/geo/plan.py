"""Planning a new trip live (Car mode's "Plan a trip", /car-sim's address box): the same open-data
pipeline as the demo trip (core/geo/trip.py), from the configured pickup to any destination.

It runs in a worker thread with the Core's time limit around it; a layer that cannot be built is None
with a note, and the caller then keeps the committed demo trip (never made-up numbers).
"""

from __future__ import annotations

from core.geo.config import CACHE_DIR, GeoConfig, load_geo_config
from core.geo.http import DiskCache, OpenDataClient
from core.geo.model import Trip
from core.geo.trip import build_trip


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
