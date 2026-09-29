"""Layer 1: ride comfort for each route between the two ends of the trip.

Routes come from OSRM (OpenStreetMap roads, free-flow times, up to 3 alternatives). For each:

  sharp turns     heading changes along the line (core/geo/geometry.sharp_turns)
  climb, grade    USGS NED 10 m elevation samples (OpenTopoData); samples on OSM bridges are left
                  out, because bare-earth elevation reads a bridge deck as the ground under it
  signals         highway=traffic_signals near the route; nodes of one intersection count once
  stop signs      highway=stop on an OSM way running with the route, facing our direction
  traffic calming traffic_calming=* on a way running with the route (bumps, humps, tables, ...)
  rough surface   meters on ways whose surface=* or smoothness=* is rough; and how much of the
                  route has either tag at all (an untagged road is unknown, not smooth)

Comfort cost = sum of weight x count (data/geo.yaml). The fastest route has the least duration,
the smoothest the least cost. Tile text is built only from these computed numbers.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

from core.geo.config import ComfortConfig, GeoConfig
from core.geo.geometry import (
    LatLon,
    angle_diff,
    bearing_deg,
    encode_polyline,
    grade_stats,
    haversine_m,
    nearest_on_polyline,
    path_length_m,
    resample,
    sharp_turns,
    simplify,
)
from core.geo.http import FetchError, OpenDataClient
from core.geo.model import Feature, LatLng, RideProfile, Routes, RouteStats, Tile
from core.geo.sources import Elevations, OsmData, OsmNode, OsmWay, OsrmRoute, elevations_ned, geocode, osrm_routes, overpass, route_query

SPEED_BUMPS = frozenset({"bump", "hump", "table", "cushion", "mini_bumps", "yes"})
MATCH_STEP_M = 10.0  # route sample spacing for matching roads (surface, bridges)


# ---------------------------------------------------------------------------------------------
# Matching OSM features to a route
# ---------------------------------------------------------------------------------------------


@dataclass
class RouteIndex:
    """Nearest-way lookups along one route, with a bounding-box prefilter."""

    ways: list[OsmWay]
    boxes: list[tuple[float, float, float, float]]

    @classmethod
    def build(cls, ways: Sequence[OsmWay], pad_m: float) -> RouteIndex:
        pad = pad_m / 111_000 * 1.2  # degrees; generous at this latitude
        ws = [w for w in ways if len(w.geometry) >= 2]
        boxes = [
            (min(p[0] for p in w.geometry) - pad, min(p[1] for p in w.geometry) - pad, max(p[0] for p in w.geometry) + pad, max(p[1] for p in w.geometry) + pad)
            for w in ws
        ]
        return cls(ws, boxes)

    def running_with(self, p: LatLon, heading: float, within_m: float, parallel_deg: float) -> OsmWay | None:
        """The closest way near p whose direction there is within parallel_deg of heading (either way)."""
        best: tuple[float, OsmWay] | None = None
        for w, (a, b, c, d) in zip(self.ways, self.boxes):
            if not (a <= p[0] <= c and b <= p[1] <= d):
                continue
            near = nearest_on_polyline(p, w.geometry)
            if near.distance_m > within_m or not _parallel(near.heading_deg, heading, parallel_deg):
                continue
            if best is None or near.distance_m < best[0]:
                best = (near.distance_m, w)
        return best[1] if best else None


def _parallel(a: float, b: float, tolerance: float) -> bool:
    d = abs(angle_diff(a, b))
    return d <= tolerance or d >= 180 - tolerance


def on_route(n: OsmNode, line: Sequence[LatLon], within_m: float) -> tuple[bool, float, float]:
    """(near, along_m, route heading there)."""
    near = nearest_on_polyline(n.point, line)
    return near.distance_m <= within_m, near.along_m, near.heading_deg


def node_way_heading(osm: OsmData, n: OsmNode, route_heading: float, parallel_deg: float) -> tuple[OsmWay, float] | None:
    """The way through node n that runs with the route, and that way's own heading at n."""
    for w in osm.ways_with_node(n.id):
        i = w.node_ids.index(n.id)
        if len(w.geometry) != len(w.node_ids) or len(w.geometry) < 2:
            continue
        a, b = (w.geometry[i], w.geometry[i + 1]) if i + 1 < len(w.geometry) else (w.geometry[i - 1], w.geometry[i])
        h = bearing_deg(a, b)
        if _parallel(h, route_heading, parallel_deg):
            return w, h
    return None


def oncoming(way: OsmWay, way_heading: float, route_heading: float, parallel_deg: float) -> bool:
    """A one-way road drawn against our direction: the opposite carriageway, which we do not drive."""
    oneway = way.tags.get("oneway")
    if oneway == "-1":
        way_heading = (way_heading + 180) % 360
    elif oneway not in ("yes", "true", "1"):
        return False
    return abs(angle_diff(way_heading, route_heading)) > 180 - parallel_deg


def stop_applies(n: OsmNode, way_heading: float, route_heading: float, parallel_deg: float) -> bool:
    """A stop sign tagged direction=forward/backward (relative to its way) applies to us only if we
    drive that way; untagged or all-way stops apply to both directions."""
    direction = n.tags.get("direction") or n.tags.get("stop:direction")
    if n.tags.get("stop") == "all" or direction in (None, "both"):
        return True
    same = abs(angle_diff(way_heading, route_heading)) <= parallel_deg
    if direction == "forward":
        return same
    if direction == "backward":
        return not same
    return True  # a compass direction or other value: we cannot tell, so we count it


def cluster(alongs: Sequence[float], within_m: float) -> list[float]:
    """Positions along the route, merged when closer than within_m (one intersection)."""
    out: list[float] = []
    for a in sorted(alongs):
        if not out or a - out[-1] > within_m:
            out.append(a)
    return out


# ---------------------------------------------------------------------------------------------
# One route
# ---------------------------------------------------------------------------------------------


@dataclass
class Measured:
    turns: int
    climb_m: float | None
    steepest_pct: float | None
    elevation_source: str | None
    signals: int
    stops: int
    calming: Counter
    rough_m: float
    surface_known_pct: float
    unknown_surface_m: float  # route meters on no matched road, or on one with no surface/smoothness tag
    bridge_m: float
    features: list[Feature]


def measure(line: Sequence[LatLon], osm: OsmData, elevation: Elevations | None, elev_points: Sequence[LatLon], cfg: ComfortConfig) -> Measured:
    features: list[Feature] = []
    turns = sharp_turns(line, cfg.sharp_turn_deg, cfg.turn_rate_deg_per_m)
    for t in turns:
        features.append(Feature(kind="sharp_turn", point=LatLng.of(t.at), detail=f"{abs(t.angle_deg):.0f}° {'right' if t.angle_deg > 0 else 'left'}"))

    roads = [w for w in osm.ways if "highway" in w.tags]
    index = RouteIndex.build(roads, cfg.match_m)

    # Surface and bridges, sampled every MATCH_STEP_M along the route.
    samples = resample(line, MATCH_STEP_M)
    rough = known = bridge = 0.0
    on_bridge: list[bool] = []
    for i, p in enumerate(samples):
        heading = bearing_deg(samples[max(0, i - 1)], samples[min(len(samples) - 1, i + 1)])
        w = index.running_with(p, heading, cfg.match_m, cfg.parallel_deg)
        step = MATCH_STEP_M if i < len(samples) - 1 else 0.0
        is_bridge = bool(w and w.tags.get("bridge") not in (None, "no"))
        on_bridge.append(is_bridge)
        if w is None:
            continue
        if is_bridge:
            bridge += step
        s, sm = w.tags.get("surface"), w.tags.get("smoothness")
        if s is not None or sm is not None:
            known += step
            if s in cfg.rough_surface or sm in cfg.rough_smoothness:
                rough += step
    length = path_length_m(line)

    # Point features.
    signal_alongs: list[float] = []
    stops = 0
    calming: Counter = Counter()
    for n in osm.nodes:
        near, along, heading = on_route(n, line, cfg.match_m)
        if not near:
            continue
        hw, tc = n.tags.get("highway"), n.tags.get("traffic_calming")
        if hw == "traffic_signals":
            signal_alongs.append(along)
            continue
        way = node_way_heading(osm, n, heading, cfg.parallel_deg)
        if way is None:
            continue  # on a cross street, not on our road
        if oncoming(way[0], way[1], heading, cfg.parallel_deg):
            continue  # the other carriageway of a divided road
        if hw == "stop" and stop_applies(n, way[1], heading, cfg.parallel_deg):
            stops += 1
            features.append(Feature(kind="stop_sign", point=LatLng.of(n.point), detail=f"OSM node {n.id}"))
        if tc and tc != "no":  # traffic_calming=no says there is none
            calming[tc] += 1
            features.append(Feature(kind="traffic_calming", point=LatLng.of(n.point), detail=f"traffic_calming={tc} (OSM node {n.id})"))
    signals = cluster(signal_alongs, cfg.signal_cluster_m)
    for a in signals:
        features.append(Feature(kind="traffic_signal", point=LatLng.of(_point_at(line, a)), detail="traffic signals"))

    # Grade, leaving out samples on bridges.
    climb = steepest = None
    source = None
    if elevation is not None and len(elevation.values) == len(elev_points):
        dist = [0.0]
        for a, b in zip(elev_points, elev_points[1:]):
            dist.append(dist[-1] + haversine_m(a, b))
        keep = [
            (d, z)
            for d, z, p in zip(dist, elevation.values, elev_points)
            if z is not None and not _near_bridge(p, index, cfg)
        ]
        if len(keep) >= 2 and keep[-1][0] > keep[0][0]:
            g = grade_stats([k[0] for k in keep], [k[1] for k in keep], min_run_m=cfg.grade_min_run_m)
            climb, steepest, source = round(g.climb_m, 1), round(g.max_grade_pct, 1), elevation.source
    return Measured(
        turns=len(turns),
        climb_m=climb,
        steepest_pct=steepest,
        elevation_source=source,
        signals=len(signals),
        stops=stops,
        calming=calming,
        rough_m=round(rough),
        surface_known_pct=round(100 * known / length, 1) if length else 0.0,
        unknown_surface_m=round(max(0.0, length - known)),
        bridge_m=round(bridge),
        features=features,
    )


def _near_bridge(p: LatLon, index: RouteIndex, cfg: ComfortConfig) -> bool:
    for w, (a, b, c, d) in zip(index.ways, index.boxes):
        if w.tags.get("bridge") in (None, "no") or not (a <= p[0] <= c and b <= p[1] <= d):
            continue
        if nearest_on_polyline(p, w.geometry).distance_m <= cfg.match_m:
            return True
    return False


def _point_at(line: Sequence[LatLon], along_m: float) -> LatLon:
    walked = 0.0
    for a, b in zip(line, line[1:]):
        seg = haversine_m(a, b)
        if walked + seg >= along_m and seg > 0:
            t = (along_m - walked) / seg
            return (a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1]))
        walked += seg
    return line[-1]


def comfort_cost(m: Measured, cfg: ComfortConfig, unknown_penalty: float, climb_if_unknown: float = 0.0, steep_if_unknown: float = 0.0) -> float:
    """Unknown is never good: a meter of road with no surface tag costs (1 - unknown_penalty) of a
    rough meter, and an unknown climb or grade costs the worst value known on the other routes."""
    w = cfg.weights
    climb = m.climb_m if m.climb_m is not None else climb_if_unknown
    steep = m.steepest_pct if m.steepest_pct is not None else steep_if_unknown
    cost = (
        w.sharp_turn * m.turns
        + w.traffic_signal * m.signals
        + w.stop_sign * m.stops
        + w.traffic_calming * sum(m.calming.values())
        + w.rough_100m * (m.rough_m + (1 - unknown_penalty) * m.unknown_surface_m) / 100
        + w.climb_10m * climb / 10
        + w.steep_pct * steep
    )
    return round(cost, 2)


# ---------------------------------------------------------------------------------------------
# Tiles
# ---------------------------------------------------------------------------------------------


def minutes(seconds: float) -> int:
    return max(1, round(seconds / 60))


def bumps(r: RouteStats) -> int:
    return sum(n for kind, n in r.traffic_calming_kinds.items() if kind in SPEED_BUMPS)


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


@dataclass(frozen=True)
class Diff:
    weight: float  # weighted size, to put the biggest differences first
    less: str  # "3 fewer sharp turns"
    more: str  # "3 more sharp turns"


def differences(a: RouteStats, b: RouteStats, cfg: ComfortConfig) -> tuple[list[str], list[str]]:
    """What route a has less of than b, and more of, biggest weighted difference first. Only
    computed differences, and only non-zero ones."""
    w = cfg.weights
    counts = [
        (a.sharp_turns, b.sharp_turns, w.sharp_turn, "sharp turn", "sharp turns"),
        (a.traffic_signals, b.traffic_signals, w.traffic_signal, "traffic light", "traffic lights"),
        (a.stop_signs, b.stop_signs, w.stop_sign, "stop sign", "stop signs"),
        (bumps(a), bumps(b), w.traffic_calming, "speed bump", "speed bumps"),
    ]
    less: list[Diff] = []
    more: list[Diff] = []
    for x, y, weight, one, many in counts:
        d = y - x
        if d > 0:
            text = f"no {many} mapped" if many == "speed bumps" and x == 0 else _plural(d, f"fewer {one}", f"fewer {many}")
            less.append(Diff(d * weight, text, ""))
        elif d < 0:
            more.append(Diff(-d * weight, "", _plural(-d, f"more {one}", f"more {many}")))
    d = b.rough_m - a.rough_m
    if d:
        (less if d > 0 else more).append(Diff(abs(d) / 100 * w.rough_100m, f"{d:.0f} m less rough road", f"{-d:.0f} m more rough road"))
    if a.climb_m is not None and b.climb_m is not None:
        d = round(b.climb_m - a.climb_m, 1)
        if d:
            (less if d > 0 else more).append(Diff(abs(d) / 10 * w.climb_10m, f"{d:g} m less climbing", f"{-d:g} m more climbing"))
    less.sort(key=lambda x: -x.weight)
    more.sort(key=lambda x: -x.weight)
    return [x.less for x in less], [x.more for x in more]


def advantages(better: RouteStats, other: RouteStats, cfg: ComfortConfig, limit: int = 2) -> list[str]:
    return differences(better, other, cfg)[0][:limit]


def trade_off(route: RouteStats, versus: RouteStats, cfg: ComfortConfig) -> str:
    """"7 fewer traffic lights, but 3 more sharp turns, 3 more stop signs" (route vs versus)."""
    less, more = differences(route, versus, cfg)
    text = ", ".join(less[:2])
    if more:
        text += (", but " if text else "") + ", ".join(more[:3])
    return text or "the same counts"


def route_summary(r: RouteStats) -> str:
    parts = [
        _plural(r.sharp_turns, "sharp turn", "sharp turns"),
        _plural(r.traffic_signals, "traffic light", "traffic lights"),
        _plural(r.stop_signs, "stop sign", "stop signs"),
        "no speed bumps mapped" if bumps(r) == 0 else _plural(bumps(r), "speed bump", "speed bumps"),
    ]
    return ", ".join(parts)


def via(route: RouteStats, others: Sequence[RouteStats]) -> str:
    """The road that sets this route apart: the last name in its summary no other route uses."""
    theirs = {n.strip() for o in others for n in o.summary.split(",")}
    names = [n.strip() for n in route.summary.split(",") if n.strip()]
    own = [n for n in names if n not in theirs]
    return (own or names or [route.id])[-1]


def tiles(routes: Sequence[RouteStats], fastest: RouteStats, smoothest: RouteStats, cfg: GeoConfig) -> list[Tile]:
    """Fastest and smoothest (one tile if the same route), then every other route as an
    alternative with its trade-off against the first tile's route. Times are estimates."""
    rider = cfg.rider

    def profile(pref: str) -> RideProfile:
        return RideProfile(route_preference=pref, uses_wheelchair=rider.uses_wheelchair, needs_extra_boarding_time=rider.needs_extra_boarding_time)

    if fastest.id == smoothest.id:
        out = [Tile(route_id=fastest.id, kind="fastest_and_smoothest", label=f"Fastest and smoothest, {minutes(fastest.duration_s)} min", detail=route_summary(fastest), ride_profile=profile("ROUTE_PREFERENCE_SMOOTHEST"))]
    else:
        better = advantages(smoothest, fastest, cfg.comfort)
        out = [
            Tile(route_id=fastest.id, kind="fastest", label=f"Fastest, {minutes(fastest.duration_s)} min", detail=route_summary(fastest), ride_profile=profile("ROUTE_PREFERENCE_FASTEST")),
            Tile(route_id=smoothest.id, kind="smoothest", label=f"Smoothest, {minutes(smoothest.duration_s)} min", detail=", ".join(better) if better else route_summary(smoothest), ride_profile=profile("ROUTE_PREFERENCE_SMOOTHEST")),
        ]
    main = out[0].route_id
    base = next(r for r in routes if r.id == main)
    for r in routes:
        if r.id in (fastest.id, smoothest.id):
            continue
        others = [o for o in routes if o.id != r.id]
        # RideProfile can only ask for fastest or smoothest, so an alternative has no profile.
        out.append(Tile(route_id=r.id, kind="alternative", label=f"Via {via(r, others)}, {minutes(r.duration_s)} min", detail=trade_off(r, base, cfg.comfort), ride_profile=None))
    return out


# ---------------------------------------------------------------------------------------------
# All routes
# ---------------------------------------------------------------------------------------------


def build_routes(cfg: GeoConfig, http: OpenDataClient, end: LatLon | None = None, end_label: str | None = None) -> Routes:
    notes: list[str] = []
    start = geocode(http, cfg.demo_trip.pickup_query).point
    if end is None:
        end = geocode(http, cfg.demo_trip.destination_query).point
    found, _ = osrm_routes(http, start, end)
    if not found:
        raise FetchError("OSRM found no route")
    if len(found) == 1:
        notes.append("OSRM returned one route, so it is both the fastest and the smoothest.")
    c = cfg.comfort
    stats: list[RouteStats] = []
    fetched = ""
    measured: list[tuple[OsrmRoute, list[LatLon], Measured]] = []
    for r in found:
        line = list(r.geometry)
        osm = overpass(http, route_query(simplify(line, 3.0), c.match_m + 3))
        fetched = osm.fetched_at
        elev_points = resample(line, c.route_elevation_step_m)
        elevation = None
        try:
            elevation = elevations_ned(http, elev_points)
        except FetchError as e:
            notes.append(f"Route {r.index + 1}: no elevation ({e}); climb and grade are unknown.")
        measured.append((r, line, measure(line, osm, elevation, elev_points, c)))
    known_climb = [m.climb_m for _, _, m in measured if m.climb_m is not None]
    known_steep = [m.steepest_pct for _, _, m in measured if m.steepest_pct is not None]
    climb_fill, steep_fill = max(known_climb, default=0.0), max(known_steep, default=0.0)
    if known_climb and len(known_climb) < len(measured):
        notes.append("A route with unknown climb is scored as climbing as much as the hilliest known route.")
    for r, line, m in measured:
        stats.append(
            RouteStats(
                id=f"route-{r.index + 1}",
                label="Alternative",
                duration_s=round(r.duration_s, 1),
                distance_m=round(r.distance_m, 1),
                summary=r.summary,
                polyline=encode_polyline(line),
                sharp_turns=m.turns,
                climb_m=m.climb_m,
                steepest_grade_pct=m.steepest_pct,
                elevation_source=m.elevation_source,
                traffic_signals=m.signals,
                stop_signs=m.stops,
                traffic_calming=sum(m.calming.values()),
                traffic_calming_kinds=dict(m.calming),
                rough_m=m.rough_m,
                surface_known_pct=m.surface_known_pct,
                bridge_m=m.bridge_m,
                comfort_cost=comfort_cost(m, c, cfg.unknown_penalty, climb_fill, steep_fill),
                features=m.features,
            )
        )
    fastest = min(stats, key=lambda s: s.duration_s)
    smoothest = min(stats, key=lambda s: (s.comfort_cost, s.duration_s))
    for s in stats:
        s.label = "Fastest and smoothest" if s is fastest and s is smoothest else "Fastest" if s is fastest else "Smoothest" if s is smoothest else "Alternative"
    return Routes(
        pickup=cfg.demo_trip.pickup,
        destination=end_label or cfg.demo_trip.destination,
        pickup_point=LatLng.of(start),
        destination_point=LatLng.of(end),
        routes=stats,
        fastest_id=fastest.id,
        smoothest_id=smoothest.id,
        tiles=tiles(stats, fastest, smoothest, cfg),
        osm_fetched_at=fetched,
        notes=notes,
    )
