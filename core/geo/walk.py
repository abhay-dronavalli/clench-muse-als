"""The rider's walk from the stopping point to the entrance, along mapped OSM footpaths.

A wheelchair route first: shortest path over walkable ways without highway=steps or
wheelchair=no. If there is none, the shortest path that uses steps (flagged). If the footpath
network does not connect the two ends (or a target is not an entrance node), a straight line,
flagged as such. Ramps are footways with incline=* (OSM wiki: a stand-alone ramp is
highway=footway + incline=*) or ramp:wheelchair=yes.
"""

from __future__ import annotations

import heapq
from collections.abc import Sequence
from dataclasses import dataclass, field

from core.geo.geometry import LatLon, haversine_m
from core.geo.sources import OsmData, OsmWay

WALKABLE = frozenset({"footway", "path", "pedestrian", "steps", "corridor", "living_street"})
CONNECT_M = 20.0  # the stop and the entrance join the footpath network within this distance


@dataclass(frozen=True)
class Walk:
    kind: str  # "step-free path" / "path with steps" / "straight line"
    points: tuple[LatLon, ...]
    length_m: float
    steps_ways: tuple[OsmWay, ...] = ()
    ramp_ways: tuple[OsmWay, ...] = ()
    ways: tuple[OsmWay, ...] = ()

    @property
    def mapped(self) -> bool:
        return self.kind != "straight line"


@dataclass
class WalkGraph:
    """Nodes are OSM node ids; edges come from consecutive nodes of walkable ways."""

    coords: dict[int, LatLon] = field(default_factory=dict)
    edges: dict[int, list[tuple[int, float, OsmWay]]] = field(default_factory=dict)

    @classmethod
    def build(cls, osm: OsmData) -> WalkGraph:
        g = cls()
        for w in osm.ways:
            if w.tags.get("highway") not in WALKABLE or len(w.node_ids) != len(w.geometry) or w.tags.get("access") in ("no", "private"):
                continue
            for (a, pa), (b, pb) in zip(zip(w.node_ids, w.geometry), zip(w.node_ids[1:], w.geometry[1:])):
                g.coords[a], g.coords[b] = pa, pb
                d = haversine_m(pa, pb)
                g.edges.setdefault(a, []).append((b, d, w))
                g.edges.setdefault(b, []).append((a, d, w))
        return g

    def nearest(self, p: LatLon, within_m: float) -> tuple[int, float] | None:
        best = min(((haversine_m(p, q), n) for n, q in self.coords.items()), default=None)
        return (best[1], best[0]) if best and best[0] <= within_m else None

    def route(self, start: int, goal: int, step_free: bool) -> tuple[list[int], list[OsmWay]] | None:
        dist = {start: 0.0}
        prev: dict[int, tuple[int, OsmWay]] = {}
        heap = [(0.0, start)]
        while heap:
            d, n = heapq.heappop(heap)
            if n == goal:
                break
            if d > dist.get(n, float("inf")):
                continue
            for m, w_len, way in self.edges.get(n, ()):
                if step_free and (way.tags.get("highway") == "steps" or way.tags.get("wheelchair") == "no"):
                    continue
                nd = d + w_len
                if nd < dist.get(m, float("inf")):
                    dist[m] = nd
                    prev[m] = (n, way)
                    heapq.heappush(heap, (nd, m))
        if goal not in dist:
            return None
        nodes, ways = [goal], []
        while nodes[-1] != start:
            n, w = prev[nodes[-1]]
            nodes.append(n)
            ways.append(w)
        return nodes[::-1], ways[::-1]


def is_ramp(w: OsmWay) -> bool:
    return w.tags.get("highway") != "steps" and ("incline" in w.tags or w.tags.get("ramp:wheelchair") == "yes")


def walk(graph: WalkGraph, stop: LatLon, goal: LatLon, goal_node: int | None) -> Walk:
    straight = Walk("straight line", (stop, goal), haversine_m(stop, goal))
    s = graph.nearest(stop, CONNECT_M)
    if goal_node is not None and goal_node in graph.coords:
        t: tuple[int, float] | None = (goal_node, 0.0)
    elif goal_node is not None:
        t = graph.nearest(goal, CONNECT_M)
    else:
        t = None  # a building wall, not an entrance: no path to route to
    if s is None or t is None:
        return straight
    for step_free in (True, False):
        found = graph.route(s[0], t[0], step_free)
        if found is None:
            continue
        nodes, ways = found
        pts = (stop, *(graph.coords[n] for n in nodes), goal)
        length = sum(haversine_m(a, b) for a, b in zip(pts, pts[1:]))
        uniq = tuple({w.id: w for w in ways}.values())
        steps = tuple(w for w in uniq if w.tags.get("highway") == "steps")
        ramps = tuple(w for w in uniq if is_ramp(w))
        return Walk("step-free path" if not steps else "path with steps", pts, length, steps, ramps, uniq)
    return straight


def dedupe_points(points: Sequence[LatLon]) -> list[LatLon]:
    out: list[LatLon] = []
    for p in points:
        if not out or haversine_m(out[-1], p) > 0.05:
            out.append(p)
    return out
