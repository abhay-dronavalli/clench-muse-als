"""Open-data sources: Nominatim geocoding, Overpass (OSM features), OSRM routes, USGS elevation.

All of it is OpenStreetMap (ODbL: credit "(c) OpenStreetMap contributors") or USGS 3DEP (public
domain), so it may be cached, committed, shown on any map, and spoken.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from core.geo.geometry import LatLon, decode_polyline
from core.geo.http import FetchError, OpenDataClient

OSM_ATTRIBUTION = "© OpenStreetMap contributors (ODbL)"
USGS_ATTRIBUTION = "USGS 3D Elevation Program (public domain)"
EPQS_NO_DATA = -1_000_000  # EPQS's value where it has no elevation
OPENTOPO_MAX_LOCATIONS = 100


def _fmt(p: LatLon) -> str:
    return f"{p[0]:.6f},{p[1]:.6f}"


# ---------------------------------------------------------------------------------------------
# Nominatim
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Place:
    query: str
    point: LatLon
    display_name: str
    osm_type: str
    osm_id: int


def geocode(http: OpenDataClient, query: str) -> Place:
    got = http.get("nominatim", http.config.endpoint("nominatim"), {"q": query, "format": "jsonv2", "limit": 1})
    if not isinstance(got.data, list) or not got.data:
        raise FetchError(f"Nominatim found nothing for {query!r}")
    hit = got.data[0]
    try:
        return Place(query, (float(hit["lat"]), float(hit["lon"])), hit.get("display_name", ""), hit.get("osm_type", ""), int(hit.get("osm_id", 0)))
    except (KeyError, TypeError, ValueError, AttributeError) as e:
        raise FetchError(f"Nominatim sent a malformed result ({type(e).__name__})") from None


# ---------------------------------------------------------------------------------------------
# Overpass
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class OsmNode:
    id: int
    point: LatLon
    tags: dict[str, str]


@dataclass(frozen=True)
class OsmWay:
    id: int
    tags: dict[str, str]
    geometry: tuple[LatLon, ...]
    node_ids: tuple[int, ...] = ()

    @property
    def closed(self) -> bool:
        return len(self.node_ids) > 3 and self.node_ids[0] == self.node_ids[-1]


@dataclass
class OsmData:
    nodes: list[OsmNode] = field(default_factory=list)
    ways: list[OsmWay] = field(default_factory=list)
    fetched_at: str = ""
    source: str = ""

    @classmethod
    def from_overpass(cls, data: dict[str, Any], fetched_at: str = "", source: str = "") -> OsmData:
        out = cls(fetched_at=fetched_at, source=source)
        seen_nodes: set[int] = set()
        seen_ways: set[int] = set()
        for el in data.get("elements", []):
            if el["type"] == "node" and el["id"] not in seen_nodes:
                seen_nodes.add(el["id"])
                out.nodes.append(OsmNode(el["id"], (el["lat"], el["lon"]), dict(el.get("tags", {}))))
            elif el["type"] == "way" and el["id"] not in seen_ways and el.get("geometry"):
                seen_ways.add(el["id"])
                geom = tuple((g["lat"], g["lon"]) for g in el["geometry"] if g)
                out.ways.append(OsmWay(el["id"], dict(el.get("tags", {})), geom, tuple(el.get("nodes", ()))))
        return out

    def ways_with_node(self, node_id: int) -> list[OsmWay]:
        return [w for w in self.ways if node_id in w.node_ids]


def way_centre(http: OpenDataClient, way_id: int) -> LatLon:
    """The mean of an OSM way's points (a building's centre, near enough for a 150 m search)."""
    osm = overpass(http, f"[out:json][timeout:25];way({way_id});out body geom;")
    if not osm.ways:
        raise FetchError(f"OSM way {way_id} not found")
    g = osm.ways[0].geometry
    pts = g[:-1] if len(g) > 1 and g[0] == g[-1] else g
    return (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))


def overpass(http: OpenDataClient, query: str) -> OsmData:
    got = http.post_form("overpass", http.config.endpoint_list("overpass"), {"data": query})
    if not isinstance(got.data, dict) or "elements" not in got.data:
        remark = got.data.get("remark", "") if isinstance(got.data, dict) else ""
        raise FetchError(f"Overpass sent no elements: {str(remark)[:200]}")
    try:
        return OsmData.from_overpass(got.data, got.fetched_at, got.source)
    except (KeyError, TypeError, ValueError) as e:
        raise FetchError(f"Overpass sent a malformed element ({type(e).__name__}: {e})") from None


def dropoff_query(center: LatLon, radius_m: float) -> str:
    """Everything the drop-off scoring reads near the destination. Ways are fetched with full
    geometry a bit past the radius so a road that starts inside it is whole."""
    around = f"around:{radius_m:.0f},{center[0]:.6f},{center[1]:.6f}"
    wide = f"around:{radius_m + 100:.0f},{center[0]:.6f},{center[1]:.6f}"
    return (
        "[out:json][timeout:25];\n("
        f'node({around})["entrance"];'
        f'node({around})["kerb"];'
        f'node({around})["barrier"="kerb"];'
        f'way({wide})["highway"];'
        f'way({around})["building"];'
        f'way({around})["amenity"~"^(hospital|parking)$"];'
        f'node({around})["amenity"~"^(taxi|parking_entrance)$"];'
        ");\nout body geom;"
    )


def route_query(line: Sequence[LatLon], radius_m: float) -> str:
    """Nodes that make a ride less smooth near a route, the ways they sit on (to tell whether they
    face the route), and the roads along it (surface, smoothness, bridges)."""
    coords = ",".join(f"{lat:.6f},{lon:.6f}" for lat, lon in line)
    around = f"around:{radius_m:.0f},{coords}"
    return (
        "[out:json][timeout:60];\n("
        f'node({around})["highway"~"^(traffic_signals|stop|give_way)$"];'
        f'node({around})["traffic_calming"];'
        ")->.n;\n"
        ".n out body;\n"
        "way(bn.n)[\"highway\"];out body geom;\n"
        f'way({around})["highway"]["highway"!~"^(footway|path|cycleway|steps|pedestrian|corridor|bridleway)$"];out body geom;'
    )


# ---------------------------------------------------------------------------------------------
# OSRM
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class OsrmRoute:
    index: int
    duration_s: float  # OSRM's free-flow estimate: no live traffic
    distance_m: float
    geometry: tuple[LatLon, ...]
    summary: str  # main road names, from OSRM's leg summary


def osrm_routes(http: OpenDataClient, start: LatLon, end: LatLon, alternatives: int = 3) -> tuple[list[OsrmRoute], str]:
    url = f"{http.config.endpoint('osrm')}/{start[1]:.6f},{start[0]:.6f};{end[1]:.6f},{end[0]:.6f}"
    params = {"alternatives": str(alternatives), "overview": "full", "geometries": "polyline6", "steps": "true"}  # steps: OSRM fills the leg summary (road names) only with them
    got = http.get("osrm", url, params)
    if not isinstance(got.data, dict) or got.data.get("code") != "Ok":
        data = got.data if isinstance(got.data, dict) else {}
        raise FetchError(f"OSRM: {data.get('code')} {data.get('message', '')}")
    try:
        return _osrm_parse(got.data), got.fetched_at
    except (KeyError, TypeError, ValueError, IndexError) as e:
        raise FetchError(f"OSRM sent a malformed route ({type(e).__name__}: {e})") from None


def _osrm_parse(data: dict) -> list[OsrmRoute]:
    return [
        OsrmRoute(
            index=i,
            duration_s=float(r["duration"]),
            distance_m=float(r["distance"]),
            geometry=tuple(decode_polyline(r["geometry"], precision=6)),
            summary=", ".join(leg.get("summary", "") for leg in r.get("legs", []) if leg.get("summary")),
        )
        for i, r in enumerate(data["routes"])
    ]


# ---------------------------------------------------------------------------------------------
# Elevation
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Elevations:
    values: list[float | None]  # meters; None where the service had no data
    source: str  # "USGS 3DEP 1 m lidar (EPQS)" / "USGS NED 10 m (OpenTopoData)"
    resolution_m: float | None


def elevations_epqs(http: OpenDataClient, points: Sequence[LatLon]) -> Elevations:
    """USGS Elevation Point Query Service: one request per point, best available 3DEP resolution
    (1 m lidar in Miami-Dade). For short walks, where 10 m data is too coarse."""
    values: list[float | None] = []
    resolutions: list[float] = []
    for lat, lon in points:
        got = http.get("epqs", http.config.endpoint("epqs"), {"x": f"{lon:.6f}", "y": f"{lat:.6f}", "units": "Meters", "wkid": "4326", "includeDate": "false"})
        raw = got.data.get("value") if isinstance(got.data, dict) else None
        try:
            value = float(raw)
        except (TypeError, ValueError):
            value = None
        values.append(None if value is None or value <= EPQS_NO_DATA else value)
        res = got.data.get("resolution") if isinstance(got.data, dict) else None
        if isinstance(res, (int, float)):
            resolutions.append(float(res))
    res_m = max(resolutions) if resolutions else None
    label = f"USGS 3DEP {res_m:g} m (EPQS)" if res_m is not None else "USGS 3DEP (EPQS)"
    return Elevations(values, label, res_m)


def elevations_ned(http: OpenDataClient, points: Sequence[LatLon]) -> Elevations:
    """OpenTopoData ned10m (USGS NED 1/3 arc-second): up to 100 points per request. For routes."""
    values: list[float | None] = []
    for i in range(0, len(points), OPENTOPO_MAX_LOCATIONS):
        chunk = points[i : i + OPENTOPO_MAX_LOCATIONS]
        got = http.get("opentopodata", http.config.endpoint("opentopodata"), {"locations": "|".join(_fmt(p) for p in chunk), "interpolation": "bilinear"})
        data = got.data if isinstance(got.data, dict) else {}
        if data.get("status") != "OK" or not isinstance(data.get("results"), list):
            raise FetchError(f"OpenTopoData: {data.get('status')} {data.get('error', '')}")
        if len(data["results"]) != len(chunk):
            raise FetchError(f"OpenTopoData sent {len(data['results'])} results for {len(chunk)} points")
        for r in data["results"]:
            e = r.get("elevation") if isinstance(r, dict) else None
            values.append(float(e) if isinstance(e, (int, float)) else None)
    return Elevations(values, "USGS NED 10 m (OpenTopoData)", 10.0)
