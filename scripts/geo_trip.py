"""Regenerate the demo trip (data/geo/demo_trip.json) from open map data.

  uv run python scripts/geo_trip.py                  rebuild from the local cache only (no network), print it
  uv run python scripts/geo_trip.py --send           fetch what is not cached yet (Nominatim, Overpass,
                                                     OSRM, OpenTopoData, USGS EPQS), then write the file
  uv run python scripts/geo_trip.py --send --refresh ask every service again (falls back to the cache)

Only open data is used here, so the result may be committed and spoken. No Google service is
called (Places and Street View are live-only on /trip/live; docs/decisions.md #21). All of these
services are free; --send is still required because it sends requests to public servers with
usage limits (one per second, one Overpass query every 2 s).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.geo.config import CACHE_DIR, load_geo_config  # noqa: E402
from core.geo.http import DiskCache, OpenDataClient  # noqa: E402
from core.geo.model import Trip  # noqa: E402
from core.geo.trip import DEMO_TRIP_PATH, build_trip, save_trip  # noqa: E402


def describe(trip: Trip) -> str:
    lines = [f"generated {trip.generated_at}", trip.duration_note]
    if trip.dropoff:
        d = trip.dropoff
        lines.append(f"\nDrop-off at {d.destination}: {len(d.candidates)} candidates")
        for c in d.candidates:
            lines.append(f"  #{c.rank} {c.score:5.1f}  {c.description}  ({c.walk_m:.0f} m, {c.walk_kind})")
            lines.append(f"         {c.reason}")
    if trip.ride:
        lines.append("\nRoutes")
        for r in trip.ride.routes:
            climb = "unknown" if r.climb_m is None else f"{r.climb_m} m"
            lines.append(
                f"  {r.id} [{r.label}] {r.duration_s / 60:.1f} min, {r.distance_m / 1000:.2f} km: {r.sharp_turns} sharp turns, "
                f"{r.traffic_signals} lights, {r.stop_signs} stop signs, calming {r.traffic_calming_kinds or 'none mapped'}, climb {climb}"
            )
        for t in trip.ride.tiles:
            lines.append(f"  TILE {t.label}: {t.detail}")
    for n in trip.notes:
        lines.append(f"NOTE {n}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--send", action="store_true", help="allow requests to the open-data services and write the result")
    parser.add_argument("--refresh", action="store_true", help="with --send: ask every service again instead of using cached responses")
    args = parser.parse_args(argv)
    cfg = load_geo_config()
    http = OpenDataClient(cfg.http, DiskCache(CACHE_DIR), live=args.send, refresh=args.refresh)
    try:
        trip = build_trip(cfg, http)
    finally:
        http.close()
    print(describe(trip))
    if not args.send:
        print(f"\nBuilt from the cache only. Run with --send to fetch missing data and write {DEMO_TRIP_PATH.name}.")
        return 0
    if trip.dropoff is None or trip.ride is None:
        print("\nNot written: a layer could not be built (see the notes above).")
        return 1
    save_trip(trip)
    print(f"\nWrote {DEMO_TRIP_PATH} ({sum(http.requests_sent.values())} requests: {http.requests_sent or 'all cached'})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
