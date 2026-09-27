"""Layer 2: accessible drop-off points near the destination.

A candidate is a pair: a point on a road where the car can stop, and the entrance the rider is
going to. Candidates come from OSM entrance nodes (entrance=*) within radius_m of the destination,
each paired with the nearest point of every nearby drivable road. Each is scored on eight factors
(weights in data/geo.yaml):

  entrance_access  wheelchair=* on the entrance node
  entrance_type    main / generic / secondary / emergency department / service
  slope            steepest grade on the straight walk, from USGS elevation samples
  steps            highway=steps near the walk (with or without a ramp tag)
  kerb             kerb=* nodes near the stopping point
  sidewalk         sidewalk=* on the road, or a mapped footway next to the stopping point
  road             quieter road types are safer to stop on
  walk             straight-line distance, and whether the line crosses a building or a major road

A factor the data does not give is None ("unknown"). It counts as `unknown_penalty` (0.3: never
as good) and is named in the reason. Missing OSM tags are unknown, not absent: "no steps mapped"
is not "no steps".

Google Places and Street View evidence is shown next to the result (/trip/live) but never scored.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TypeVar

from core.geo.config import DropoffConfig
from core.geo.geometry import (
    LatLon,
    Projection,
    bearing_deg,
    encode_polyline,
    even_distances,
    grade_stats,
    haversine_m,
    nearest_on_polyline,
    resample,
)
from core.geo.model import Candidate, DropoffRequest, Factor, LatLng, Slope
from core.geo.sources import Elevations, OsmData, OsmNode, OsmWay
from core.geo.walk import Walk, WalkGraph
from core.geo.walk import walk as walk_path

ElevationFn = Callable[[Sequence[LatLon]], Elevations]
T = TypeVar("T")

# Where a car may stop, and how calm each road type is for stopping (1 = calmest).
ROAD_CALM: dict[str, float] = {
    "service": 1.0,
    "living_street": 1.0,
    "residential": 1.0,
    "unclassified": 0.8,
    "tertiary": 0.6,
    "secondary": 0.3,
    "primary": 0.1,
    "trunk": 0.0,
}
MAJOR_ROADS = frozenset({"motorway", "trunk", "primary", "secondary", "tertiary"})
NO_STOP_SERVICE = frozenset({"drive-through", "emergency_access"})
NO_ACCESS = frozenset({"no", "private"})

# entrance=* values a rider cannot use: exits, fire exits, sealed doors, garages.
UNUSABLE_ENTRANCES = frozenset({"exit", "emergency", "no", "garage"})
ENTRANCE_KIND: dict[str, tuple[float, str]] = {
    "main": (1.0, "Main entrance"),
    "yes": (0.7, "Entrance"),
    "entrance": (0.7, "Entrance"),
    "secondary": (0.6, "Side entrance"),
    "home": (0.6, "Entrance"),
    "staircase": (0.6, "Entrance"),
    "shop": (0.6, "Entrance"),
    "service": (0.1, "Service entrance"),
}
EMERGENCY_WARD = (0.5, "Emergency department entrance")

WHEELCHAIR: dict[str, float] = {"yes": 1.0, "designated": 1.0, "limited": 0.5, "no": 0.0}
KERB: dict[str, float] = {"lowered": 1.0, "flush": 1.0, "no": 1.0, "rolled": 0.4, "raised": 0.0}
SIDEWALK_BOTH = frozenset({"both", "yes"})
SIDEWALK_ONE = frozenset({"left", "right"})
SIDEWALK_NONE = frozenset({"no", "none"})

GOOD, BAD = 0.7, 0.35  # factor values at or above GOOD are praised in the reason, at or below BAD warned about
ENTRANCE_END_M = 2.0  # the walk line touches the entrance's own building wall here; not a crossing


@dataclass(frozen=True)
class Target:
    """Where the rider is going: an OSM entrance node; with none mapped, the wall of a building
    (the closest point of `wall` to each road); with no building either, the destination point."""

    point: LatLon
    node: OsmNode | None
    kind: str
    kind_value: float | None
    label: str
    wall: tuple[LatLon, ...] = ()


@dataclass(frozen=True)
class Pair:
    target: Target
    road: OsmWay
    stop: LatLon
    goal: LatLon  # the target point this stop walks to (for a wall: its closest point)
    walk: Walk
    curb_node: OsmNode | None = None  # the lowered kerb this curbside stop was placed at

    @property
    def walk_m(self) -> float:
        return self.walk.length_m


# Buildings that are not where a hospital visitor is going.
NOT_A_DESTINATION = frozenset({"parking", "garage", "garages", "house", "shed", "roof", "carport", "residential", "apartments"})
CURB_OK = frozenset({"lowered", "flush", "no"})
CURB_STOP_M = 10.0  # a curbside stop is on a road within this of its kerb node


# ---------------------------------------------------------------------------------------------
# Candidates
# ---------------------------------------------------------------------------------------------


def targets(osm: OsmData, center: LatLon, radius_m: float, destination_way: int | None = None) -> tuple[list[Target], list[str]]:
    """Entrances of the destination building (destination_way) when it has any mapped; otherwise
    every usable entrance within radius_m; otherwise building walls; otherwise the point itself."""
    notes: list[str] = []
    out: list[Target] = []
    buildings_by_node: dict[int, OsmWay] = {}
    for w in osm.ways:
        if "building" in w.tags:
            for nid in w.node_ids:
                buildings_by_node.setdefault(nid, w)
    own: set[int] = set()
    if destination_way is not None:
        dest = next((w for w in osm.ways if w.id == destination_way), None)
        own = set(dest.node_ids) if dest else set()
        if not any(n.id in own and "entrance" in n.tags for n in osm.nodes):
            notes.append(f"The destination building (OSM way {destination_way}) has no entrance mapped; using every entrance within {radius_m:.0f} m.")
            own = set()
    for n in osm.nodes:
        value = n.tags.get("entrance")
        if value is None or haversine_m(n.point, center) > radius_m or value in UNUSABLE_ENTRANCES:
            continue
        if own and n.id not in own:
            continue
        if n.tags.get("emergency") == "emergency_ward_entrance":
            kind_value, label = EMERGENCY_WARD
            kind = "emergency_ward_entrance"
        else:
            kind_value, label = ENTRANCE_KIND.get(value, (None, "Entrance"))
            kind = value
        name = n.tags.get("name") or n.tags.get("ref")
        building = buildings_by_node.get(n.id)
        where = building.tags.get("name") or _address(building.tags) if building else None
        text = label + (f" ({name})" if name else "") + (f", {where}" if where else "")
        out.append(Target(n.point, n, kind, kind_value, text))
    if out:
        return out, notes
    buildings = [
        w
        for w in osm.ways
        if w.closed
        and "building" in w.tags
        and w.tags["building"] not in NOT_A_DESTINATION
        and w.tags.get("amenity") != "parking"
        and nearest_on_polyline(center, w.geometry).distance_m <= radius_m
    ]
    if buildings:
        notes.append(
            "No usable entrance is mapped in OpenStreetMap within the radius, so entrance access and type are "
            "unknown for every candidate; each one aims at the closest wall of a mapped building."
        )
        for w in buildings:
            name = w.tags.get("name") or _address(w.tags) or f"OSM way {w.id}"
            out.append(Target(w.geometry[0], None, "unknown", None, f"Building {name} (no entrance mapped)", w.geometry))
        return out, notes
    notes.append("No entrance or building is mapped in OpenStreetMap within the radius; candidates aim at the destination point.")
    return [Target(center, None, "unknown", None, "Destination (no building or entrance mapped)")], notes


def stoppable(way: OsmWay) -> bool:
    t = way.tags
    return (
        t.get("highway") in ROAD_CALM
        and t.get("service") not in NO_STOP_SERVICE
        and t.get("access") not in NO_ACCESS
        and t.get("motor_vehicle") not in NO_ACCESS
        and t.get("area") != "yes"
        and len(way.geometry) >= 2
    )


def pairs(osm: OsmData, center: LatLon, tgts: Sequence[Target], cfg: DropoffConfig) -> list[Pair]:
    """Every target with the closest point of every stoppable road near it, plus a curbside stop at
    every lowered or flush kerb (paired with the closest target). The walk follows mapped
    footpaths where they connect (core/geo/walk.py)."""
    roads = [w for w in osm.ways if stoppable(w)]
    graph = WalkGraph.build(osm)
    out: list[Pair] = []

    def add(t: Target, road: OsmWay, stop: LatLon, curb: OsmNode | None = None) -> None:
        goal = nearest_on_polyline(stop, t.wall).point if t.wall else t.point
        if haversine_m(stop, goal) <= cfg.max_walk_m and haversine_m(stop, center) <= cfg.radius_m:
            w = walk_path(graph, stop, goal, t.node.id if t.node else None)
            out.append(Pair(t, road, stop, goal, w, curb))

    for t in tgts:
        for road in roads:
            add(t, road, _closest_stop(t, road))
    for n in osm.nodes:
        if n.tags.get("kerb") not in CURB_OK or haversine_m(n.point, center) > cfg.radius_m:
            continue
        near = [(nearest_on_polyline(n.point, r.geometry), r) for r in roads]
        near = [(m, r) for m, r in near if m.distance_m <= CURB_STOP_M]
        if not near:
            continue
        m, road = min(near, key=lambda x: x[0].distance_m)
        t = min(tgts, key=lambda t: haversine_m(m.point, nearest_on_polyline(m.point, t.wall).point if t.wall else t.point))
        add(t, road, m.point, n)
    return out


def _closest_stop(t: Target, road: OsmWay) -> LatLon:
    """The point of the road closest to the target (for a wall: closest to any part of it)."""
    if not t.wall:
        return nearest_on_polyline(t.point, road.geometry).point
    best = min(
        [nearest_on_polyline(p, road.geometry) for p in t.wall]
        + [nearest_on_polyline(p, t.wall) for p in road.geometry],
        key=lambda n: n.distance_m,
    )
    # best.point lies on the road when it came from the first list, on the wall from the second.
    return nearest_on_polyline(best.point, road.geometry).point


def _address(tags: dict[str, str]) -> str | None:
    number, street = tags.get("addr:housenumber"), tags.get("addr:street")
    return f"{number} {street}" if number and street else None


# ---------------------------------------------------------------------------------------------
# Factors
# ---------------------------------------------------------------------------------------------


def f_entrance_access(t: Target) -> tuple[float | None, str]:
    if t.node is None:
        return None, "no entrance mapped"
    tag = t.node.tags.get("wheelchair")
    if tag in WHEELCHAIR:
        return WHEELCHAIR[tag], f"wheelchair={tag} on OSM node {t.node.id}"
    return None, f"OSM node {t.node.id} has no wheelchair tag"


def f_entrance_type(t: Target) -> tuple[float | None, str]:
    if t.node is None:
        return None, "no entrance mapped"
    if t.kind == "emergency_ward_entrance":
        return t.kind_value, "emergency=emergency_ward_entrance (emergency department, not the general entrance)"
    return t.kind_value, f"entrance={t.node.tags.get('entrance')}"


def f_slope(elev: Elevations | None, distances: Sequence[float], cfg: DropoffConfig, centred: bool = False) -> tuple[float | None, str, Slope | None]:
    if elev is None:
        return None, "no elevation data", None
    values = elev.values
    if len(values) < 2 or len(values) != len(distances) or any(v is None for v in values):
        return None, f"{elev.source} had no value for part of the walk", None
    zs = [float(v) for v in values if v is not None]
    walk_m = distances[-1]
    g = grade_stats(distances, zs, min_run_m=cfg.slope_min_run_m)
    slope = Slope(
        max_grade_pct=round(g.max_grade_pct, 1),
        avg_grade_pct=round(g.avg_grade_pct, 1),
        climb_m=round(g.climb_m, 2),
        length_m=round(walk_m, 1),
        samples=len(zs),
        source=elev.source,
        resolution_m=elev.resolution_m,
    )
    m = g.max_grade_pct
    # 2%: ADA cross slope; 5%: steeper than this is a ramp; 8.33%: the steepest ramp ADA allows.
    value = 1.0 if m <= 2 else 0.7 if m <= 5 else 0.4 if m <= 8.33 else 0.0
    where = f"over a {slope.length_m} m line centred on the walk (the walk is shorter)" if centred else f"over {slope.length_m} m of the walk"
    return value, f"max {slope.max_grade_pct}% / avg {slope.avg_grade_pct}% {where} ({slope.samples} samples, {elev.source})", slope


def f_steps(osm: OsmData, p: Pair, search_m: float) -> tuple[float | None, str]:
    w = p.walk
    if w.kind == "step-free path":
        if w.ramp_ways:
            r = w.ramp_ways[0]
            detail = f"incline={r.tags['incline']}" if "incline" in r.tags else "ramp:wheelchair=yes"
            return 0.9, f"step-free path on mapped footways, with a ramp (OSM way {r.id}, {detail})"
        return 1.0, "step-free path on mapped footways"
    if w.kind == "path with steps":
        with_ramp = [s for s in w.steps_ways if s.tags.get("ramp:wheelchair") == "yes" or s.tags.get("ramp") == "yes"]
        if with_ramp:
            return 0.7, f"the only mapped path uses steps with a ramp tag (OSM way {with_ramp[0].id})"
        return 0.0, f"the only mapped path uses steps (OSM way {w.steps_ways[0].id})"
    return _steps_near_line(osm, (p.stop, p.goal), search_m)


def _steps_near_line(osm: OsmData, walk: tuple[LatLon, LatLon], search_m: float) -> tuple[float | None, str]:
    """No mapped path: look for steps and ramps near the straight line instead."""
    steps = [w for w in osm.ways if w.tags.get("highway") == "steps" and _lines_within(walk, w.geometry, search_m)]
    if steps:
        with_ramp = [w for w in steps if w.tags.get("ramp:wheelchair") == "yes" or w.tags.get("ramp") == "yes"]
        if with_ramp:
            return 0.7, f"steps with a ramp tag mapped on the walk (OSM way {with_ramp[0].id})"
        return 0.0, f"steps mapped on the walk (OSM way {steps[0].id})"
    ramps = [
        w
        for w in osm.ways
        if w.tags.get("highway") in ("footway", "path", "pedestrian")
        and (w.tags.get("wheelchair") in ("yes", "designated") or "incline" in w.tags)
        and _lines_within(walk, w.geometry, search_m)
    ]
    if ramps and ramps[0].tags.get("wheelchair") in ("yes", "designated"):
        return 0.8, f"wheelchair={ramps[0].tags['wheelchair']} footway near the straight line (OSM way {ramps[0].id})"
    return None, "no mapped footpath connects the stop and the entrance, and no steps or ramps are mapped near the straight line (absence in OSM is not proof)"


def f_kerb(osm: OsmData, stop: LatLon, search_m: float) -> tuple[float | None, str]:
    kerbs = sorted(
        ((haversine_m(n.point, stop), n) for n in osm.nodes if "kerb" in n.tags),
        key=lambda x: x[0],
    )
    kerbs = [(d, n) for d, n in kerbs if d <= search_m]
    if not kerbs:
        return None, f"no kerb mapped within {search_m:.0f} m"
    d, n = kerbs[0]
    tag = n.tags["kerb"]
    if tag in KERB:
        return KERB[tag], f"kerb={tag} on OSM node {n.id}, {d:.0f} m away"
    return None, f"kerb={tag} on OSM node {n.id} (type not given)"


def f_sidewalk(osm: OsmData, road: OsmWay, stop: LatLon, search_m: float) -> tuple[float | None, str]:
    tags = road.tags
    value = tags.get("sidewalk") or tags.get("sidewalk:both")
    if value is None and ("sidewalk:left" in tags or "sidewalk:right" in tags):
        sides = [s for s in ("left", "right") if tags.get(f"sidewalk:{s}") in ("yes", "separate")]
        value = "both" if len(sides) == 2 else sides[0] if sides else "no"
    if value in SIDEWALK_BOTH:
        return 1.0, f"sidewalk={value} on the road"
    if value in SIDEWALK_ONE:
        return 0.7, f"sidewalk={value} on the road (one side only)"
    if value in SIDEWALK_NONE:
        return 0.0, f"sidewalk={value} on the road"
    footways = [
        (nearest_on_polyline(stop, w.geometry).distance_m, w)
        for w in osm.ways
        if w.tags.get("highway") in ("footway", "pedestrian") and len(w.geometry) >= 2
    ]
    footways = sorted((d, w) for d, w in footways if d <= search_m) if footways else []
    if footways:
        d, w = footways[0]
        return 1.0, f"mapped footway (OSM way {w.id}) {d:.0f} m from the stopping point"
    return None, "road has no sidewalk tag and no footway is mapped nearby"


def f_road(road: OsmWay) -> tuple[float | None, str]:
    hw = road.tags["highway"]
    name = road.tags.get("name")
    return ROAD_CALM[hw], f"highway={hw}" + (f" ({name})" if name else "")


def f_walk(osm: OsmData, pair: Pair, max_walk_m: float) -> tuple[float | None, str]:
    base = 1.0 if pair.walk_m <= 15 else round(max(0.2, 1.0 - 0.8 * (pair.walk_m - 15) / (max_walk_m - 15)), 2)
    what = "along mapped footways" if pair.walk.mapped else "straight line (no mapped footpath connects)"
    head = f"{pair.walk_m:.0f} m {what}"
    segments = list(zip(pair.walk.points, pair.walk.points[1:]))
    if not pair.walk.mapped:
        for w in osm.ways:
            if "building" in w.tags and w.closed and _crosses(segments[0], w.geometry, ignore_end_m=ENTRANCE_END_M):
                return 0.1, head + f"; crosses a building (OSM way {w.id}): the real walk is longer"
    for w in osm.ways:
        if w.id != pair.road.id and w.tags.get("highway") in MAJOR_ROADS and any(_crosses(s, w.geometry, ignore_end_m=0) for s in segments):
            name = w.tags.get("name") or f"a {w.tags['highway']} road"
            return min(base, 0.2), head + f"; crosses {name}"
    return base, head


# ---------------------------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------------------------

LABELS = {
    "entrance_access": "wheelchair access",
    "entrance_type": "entrance type",
    "slope": "slope",
    "steps": "steps/ramp",
    "kerb": "curb",
    "sidewalk": "sidewalk",
    "road": "road",
    "walk": "walk",
}


def score_factors(values: dict[str, tuple[float | None, str]], weights: dict[str, float], unknown_penalty: float) -> tuple[float, list[Factor]]:
    """0..100 weighted mean. An unknown factor counts as unknown_penalty, never as good."""
    factors: list[Factor] = []
    total = weight_sum = 0.0
    for name, (value, evidence) in values.items():
        w = weights[name]
        used = unknown_penalty if value is None else value
        factors.append(Factor(name=name, label=LABELS.get(name, name), value=value, used=used, weight=w, evidence=evidence))
        total += w * used
        weight_sum += w
    return (round(100 * total / weight_sum, 1) if weight_sum else 0.0), factors


def describe(pair: Pair) -> str:
    road = pair.road.tags.get("name") or f"{pair.road.tags['highway'].replace('_', ' ')} road"
    return f"{pair.target.label} from {road}"


def reason(factors: Sequence[Factor], slope: Slope | None) -> str:
    """One line: the good, then the bad, then every unknown by name."""
    by = {f.name: f for f in factors}
    good: list[str] = []
    bad: list[str] = []

    def put(f: Factor, good_text: str, bad_text: str) -> None:
        if f.value is None:
            return
        if f.value >= GOOD:
            good.append(good_text)
        elif f.value <= BAD:
            bad.append(bad_text)

    ea = by["entrance_access"]
    if ea.value is not None:
        put(ea, "wheelchair-accessible entrance", "entrance not wheelchair-accessible")
        if BAD < ea.value < GOOD:
            bad.append("limited wheelchair access")
    s = by["slope"]
    if slope is not None:
        word = "flat" if slope.max_grade_pct <= 2 else "gentle slope" if slope.max_grade_pct <= 5 else "steep slope"
        text = f"{word} (max {slope.max_grade_pct}%)"
        (good if s.value is not None and s.value >= GOOD else bad).append(text)
    st = by["steps"]
    put(st, "ramp on a step-free path" if st.value == 0.9 else "step-free path" if st.value == 1.0 else "ramp mapped", "steps on the way")
    put(by["kerb"], "lowered curb", "raised curb")
    put(by["sidewalk"], "sidewalk", "no sidewalk")
    put(by["road"], "quiet road", "busy road")
    w = by["walk"]
    if w.value is not None:
        if "; crosses " in w.evidence:
            crossed = w.evidence.split("; crosses ", 1)[1].split(" (OSM way")[0]
            bad.append(f"walk crosses {crossed}")
        elif w.value >= GOOD:
            good.append("short walk")
        elif w.value <= BAD:
            bad.append("long walk")
    unknown = [f.label for f in factors if f.value is None]
    parts = good + bad
    line = ", ".join(parts) if parts else "little is known"
    if unknown:
        line += "; unknown: " + ", ".join(unknown)
    return line[0].upper() + line[1:]


def rank_candidates(
    osm: OsmData,
    center: LatLon,
    cfg: DropoffConfig,
    unknown_penalty: float,
    elevation: ElevationFn | None,
    destination_way: int | None = None,
) -> tuple[list[Candidate], list[str]]:
    """Score every entrance/road pair, keep the best per stopping spot, and rank them.

    Elevation is the slow part (one USGS request per sample), so every pair is scored first
    without it (slope unknown), and only the best 2 x max_candidates get slope samples.
    """
    tgts, notes = targets(osm, center, cfg.radius_m, destination_way)
    ps = pairs(osm, center, tgts, cfg)
    if not ps:
        notes.append("No drivable road within reach of an entrance; no drop-off candidates.")
        return [], notes
    weights = cfg.weights.model_dump()

    def factor_values(p: Pair, elev: Elevations | None, distances: Sequence[float] = (), centred: bool = False) -> tuple[dict[str, tuple[float | None, str]], Slope | None]:
        slope_v, slope_ev, slope = f_slope(elev, distances, cfg, centred)
        return {
            "entrance_access": f_entrance_access(p.target),
            "entrance_type": f_entrance_type(p.target),
            "slope": (slope_v, slope_ev),
            "steps": f_steps(osm, p, cfg.steps_search_m),
            "kerb": f_kerb(osm, p.stop, cfg.kerb_search_m),
            "sidewalk": f_sidewalk(osm, p.road, p.stop, cfg.sidewalk_search_m),
            "road": f_road(p.road),
            "walk": f_walk(osm, p, cfg.max_walk_m),
        }, slope

    pre = sorted(ps, key=lambda p: -score_factors(factor_values(p, None)[0], weights, unknown_penalty)[0])
    shortlist = _dedupe([(p, p.stop) for p in pre], cfg.dedupe_m)[: 2 * cfg.max_candidates]

    scored: list[tuple[float, Pair, list[Factor], Slope | None]] = []
    for p in shortlist:
        elev = None
        points, distances, centred = slope_samples(p, cfg)
        if elevation is not None and distances and distances[-1] > 0:
            try:
                elev = elevation(points)
            except Exception as e:  # a failed lookup is an unknown slope, not a failed ranking
                notes.append(f"Elevation lookup failed for one walk ({type(e).__name__}); its slope is unknown.")
        values, slope = factor_values(p, elev, distances, centred)
        score, factors = score_factors(values, weights, unknown_penalty)
        scored.append((score, p, factors, slope))
    scored.sort(key=lambda s: -s[0])
    kept = _dedupe([(s, s[1].stop) for s in scored], cfg.dedupe_m)[: cfg.max_candidates]

    out: list[Candidate] = []
    for rank, (score, p, factors, slope) in enumerate(kept, start=1):
        out.append(
            Candidate(
                id=_candidate_id(p),
                rank=rank,
                score=score,
                stop=LatLng.of(p.stop),
                entrance=LatLng.of(p.goal),
                heading_to_entrance_deg=round(bearing_deg(p.stop, p.goal), 1),
                entrance_osm_id=p.target.node.id if p.target.node else None,
                entrance_kind=p.target.kind,
                road_osm_id=p.road.id,
                road_class=p.road.tags["highway"],
                road_name=p.road.tags.get("name"),
                walk_m=round(p.walk_m, 1),
                walk_kind=p.walk.kind,
                walk_polyline=encode_polyline(p.walk.points),
                slope=slope,
                factors=factors,
                unknown=[f.label for f in factors if f.value is None],
                description=describe(p),
                reason=reason(factors, slope),
            )
        )
    return out, notes


def dropoff_request(candidates: Sequence[Candidate]) -> DropoffRequest | None:
    if not candidates:
        return None
    best = candidates[0]
    fallback = candidates[1].stop if len(candidates) > 1 else None
    return DropoffRequest(request_id=f"dropoff-{best.id}", point=best.stop, description=best.description, reason=best.reason, fallback_point=fallback)


# ---------------------------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------------------------


def _candidate_id(p: Pair) -> str:
    key = f"{p.target.label}:{p.road.id}:{p.stop[0]:.6f},{p.stop[1]:.6f}"
    return hashlib.sha1(key.encode()).hexdigest()[:8]


def _dedupe(items: Sequence[tuple[T, LatLon]], within_m: float) -> list[T]:
    """Keep items in order, dropping any whose point is within within_m of an earlier kept one."""
    kept: list[tuple[T, LatLon]] = []
    for item, point in items:
        if all(haversine_m(point, q) > within_m for _, q in kept):
            kept.append((item, point))
    return [item for item, _ in kept]


def slope_samples(p: Pair, cfg: DropoffConfig) -> tuple[list[LatLon], list[float], bool]:
    """Where to sample elevation for a walk, and the distance of each sample along it.

    A walk at least slope_min_run_m long is sampled along its own path, at most
    max_elevation_samples points. A shorter one is sampled along a slope_min_run_m straight line
    centred on it (centred=True), since shorter runs are below lidar precision."""
    if p.walk_m >= cfg.slope_min_run_m:
        n = max(2, min(cfg.max_elevation_samples, math.ceil(p.walk_m / cfg.elevation_step_m) + 1))
        pts = resample(p.walk.points, p.walk_m / (n - 1))
        dist = [0.0]
        for a, b in zip(pts, pts[1:]):
            dist.append(dist[-1] + haversine_m(a, b))
        return pts, dist, False
    a, b, run = slope_line(p.stop, p.goal, cfg.slope_min_run_m)
    n = max(2, min(cfg.max_elevation_samples, math.ceil(run / cfg.elevation_step_m) + 1))
    return _line_points(a, b, n), even_distances(run, n), True


def slope_line(stop: LatLon, goal: LatLon, min_run_m: float) -> tuple[LatLon, LatLon, float]:
    """The line to sample for slope: the walk, or, if it is shorter than min_run_m, a min_run_m
    line along the same heading centred on the walk (shorter runs are below lidar precision)."""
    walk = haversine_m(stop, goal)
    if walk >= min_run_m:
        return stop, goal, walk
    proj = Projection(stop)
    gx, gy = proj.xy(goal)
    heading = math.atan2(gx, gy) if walk > 0 else 0.0
    mx, my, half = gx / 2, gy / 2, min_run_m / 2
    ux, uy = math.sin(heading), math.cos(heading)
    return proj.latlon(mx - ux * half, my - uy * half), proj.latlon(mx + ux * half, my + uy * half), min_run_m


def _line_points(a: LatLon, b: LatLon, n: int) -> list[LatLon]:
    return [(a[0] + (b[0] - a[0]) * i / (n - 1), a[1] + (b[1] - a[1]) * i / (n - 1)) for i in range(n)]


def _lines_within(walk: tuple[LatLon, LatLon], line: Sequence[LatLon], within_m: float) -> bool:
    """Whether any part of `line` comes within within_m of the walk segment."""
    if len(line) < 2:
        return False
    if _crosses(walk, line, ignore_end_m=0):
        return True
    length = haversine_m(*walk)
    n = max(2, math.ceil(length / 2) + 1)
    return any(nearest_on_polyline(p, line).distance_m <= within_m for p in _line_points(walk[0], walk[1], n)) or any(
        nearest_on_polyline(q, walk).distance_m <= within_m for q in line
    )


def _crosses(walk: tuple[LatLon, LatLon], line: Sequence[LatLon], ignore_end_m: float) -> bool:
    """Whether the walk segment crosses `line`, ignoring crossings within ignore_end_m of its end."""
    proj = Projection(walk[0])
    ax, ay = proj.xy(walk[0])
    bx, by = proj.xy(walk[1])
    length = math.hypot(bx - ax, by - ay)
    if length == 0:
        return False
    pts = [proj.xy(p) for p in line]
    for (cx, cy), (dx, dy) in zip(pts, pts[1:]):
        t = _segment_t((ax, ay), (bx, by), (cx, cy), (dx, dy))
        if t is not None and t * length < length - ignore_end_m:
            return True
    return False


def _segment_t(a, b, c, d) -> float | None:
    """Where segment ab crosses segment cd, as a fraction along ab; None if they do not cross."""
    rx, ry = b[0] - a[0], b[1] - a[1]
    sx, sy = d[0] - c[0], d[1] - c[1]
    denom = rx * sy - ry * sx
    if denom == 0:
        return None
    qx, qy = c[0] - a[0], c[1] - a[1]
    t = (qx * sy - qy * sx) / denom
    u = (qx * ry - qy * rx) / denom
    return t if 0 <= t <= 1 and 0 <= u <= 1 else None
