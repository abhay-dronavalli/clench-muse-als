"""Small hand-built OSM scenes in local meters, so each test states exactly what is mapped."""

from __future__ import annotations

from itertools import count

from core.geo.config import load_geo_config
from core.geo.geometry import LatLon, Projection
from core.geo.sources import Elevations, OsmData, OsmNode, OsmWay

ORIGIN: LatLon = (25.6754, -80.3720)
PROJ = Projection(ORIGIN)
_ids = count(1_000)


def at(x: float, y: float) -> LatLon:
    """Meters east (x) and north (y) of ORIGIN."""
    return PROJ.latlon(x, y)


def node(x: float, y: float, **tags: str) -> OsmNode:
    return OsmNode(next(_ids), at(x, y), dict(tags))


def way(points: list[tuple[float, float]], node_ids: list[int] | None = None, **tags: str) -> OsmWay:
    ids = node_ids or [next(_ids) for _ in points]
    return OsmWay(next(_ids), dict(tags), tuple(at(x, y) for x, y in points), tuple(ids))


def building(x0: float, y0: float, x1: float, y1: float, entrances: list[OsmNode] = (), **tags: str) -> OsmWay:
    """A rectangle; entrance nodes are inserted into its outline in order (they sit on the wall)."""
    corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    pts: list[tuple[float, float]] = []
    ids: list[int] = []
    for i, c in enumerate(corners):
        pts.append(c)
        ids.append(next(_ids))
        if i == 0:  # south wall
            for e in entrances:
                ex, ey = PROJ.xy(e.point)
                pts.append((ex, ey))
                ids.append(e.id)
    pts.append(corners[0])
    ids.append(ids[0])
    return way(pts, ids, **({"building": "yes"} | tags))


def cfg():
    return load_geo_config()


def flat(_points) -> Elevations:
    return Elevations([1.0] * len(_points), "test flat", 1.0)


def scene(*, entrance_tags: dict[str, str] | None = None, kerb: str | None = "lowered", sidewalk: bool = True, road: str = "service", walk_steps: bool = False, step_free_detour: bool = False) -> tuple[OsmData, OsmNode, OsmWay]:
    """A building whose south wall has one entrance at (0, 0); a road along y = -20; a footway from
    the road to the entrance. Returns (osm, entrance, road)."""
    ent = node(0, 0, **({"entrance": "main", "wheelchair": "yes"} if entrance_tags is None else entrance_tags))
    b = building(-20, 0, 20, 30, [ent])
    r = way([(-80, -20), (80, -20)], highway=road)
    nodes = [ent]
    ways = [b, r]
    curb_at = next(_ids)
    mid = next(_ids)
    kind = {"highway": "steps"} if walk_steps else {"highway": "footway"} if sidewalk else {"highway": "path"}
    # Without a sidewalk the connector is a highway=path: still walkable, but not a sidewalk.
    connector = {"highway": "footway", "footway": "sidewalk"} if sidewalk else {"highway": "path"}
    ways.append(way([(0, -18), (0, -8)], [curb_at, mid], **connector))
    ways.append(way([(0, -8), (0, 0)], [mid, ent.id], **kind))
    if step_free_detour:
        a, b2 = next(_ids), next(_ids)
        ways.append(way([(0, -8), (15, -8), (15, -2), (0, 0)], [mid, a, b2, ent.id], highway="footway", incline="5%"))
    if kerb is not None:
        nodes.append(node(0, -19, barrier="kerb", kerb=kerb))
    return OsmData(nodes=nodes, ways=ways, fetched_at="test"), ent, r
