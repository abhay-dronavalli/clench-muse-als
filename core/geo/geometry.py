"""Plane geometry on small areas: polylines, distances, headings, turns, and grades.

Points are (lat, lon) tuples in degrees. Distances are in meters. Over a trip of a few kilometers a
local equirectangular projection is accurate to well under a meter, which is far finer than the
data (road centerlines, 10 m+ elevation grids).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

LatLon = tuple[float, float]

EARTH_RADIUS_M = 6_371_008.8


# ---------------------------------------------------------------------------------------------
# Encoded polylines (Google's format, 1e5 precision; Routes API encodedPolyline)
# ---------------------------------------------------------------------------------------------


def decode_polyline(encoded: str, precision: int = 5) -> list[LatLon]:
    factor = 10**precision
    points: list[LatLon] = []
    index = lat = lon = 0
    while index < len(encoded):
        deltas = []
        for _ in range(2):
            shift = result = 0
            while True:
                b = ord(encoded[index]) - 63
                index += 1
                result |= (b & 0x1F) << shift
                shift += 5
                if b < 0x20:
                    break
            deltas.append(~(result >> 1) if result & 1 else result >> 1)
        lat += deltas[0]
        lon += deltas[1]
        points.append((lat / factor, lon / factor))
    return points


def encode_polyline(points: Sequence[LatLon], precision: int = 5) -> str:
    factor = 10**precision
    out: list[str] = []
    prev_lat = prev_lon = 0
    for lat, lon in points:
        ilat, ilon = round(lat * factor), round(lon * factor)
        for delta in (ilat - prev_lat, ilon - prev_lon):
            value = ~(delta << 1) if delta < 0 else delta << 1
            while value >= 0x20:
                out.append(chr((0x20 | (value & 0x1F)) + 63))
                value >>= 5
            out.append(chr(value + 63))
        prev_lat, prev_lon = ilat, ilon
    return "".join(out)


# ---------------------------------------------------------------------------------------------
# Distances and headings
# ---------------------------------------------------------------------------------------------


def haversine_m(a: LatLon, b: LatLon) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(h)))


def bearing_deg(a: LatLon, b: LatLon) -> float:
    """Initial compass heading from a to b: 0 = north, 90 = east."""
    lat1, lat2 = math.radians(a[0]), math.radians(b[0])
    dlon = math.radians(b[1] - a[1])
    x = math.sin(dlon) * math.cos(lat2)
    y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    return math.degrees(math.atan2(x, y)) % 360.0


def angle_diff(a: float, b: float) -> float:
    """Signed smallest turn from heading a to heading b, in (-180, 180]. Positive = right turn."""
    d = (b - a + 180.0) % 360.0 - 180.0
    return 180.0 if d == -180.0 else d


class Projection:
    """Local equirectangular projection to meters around an origin."""

    def __init__(self, origin: LatLon) -> None:
        self.lat0, self.lon0 = origin
        self.kx = math.radians(1) * EARTH_RADIUS_M * math.cos(math.radians(self.lat0))
        self.ky = math.radians(1) * EARTH_RADIUS_M

    def xy(self, p: LatLon) -> tuple[float, float]:
        return ((p[1] - self.lon0) * self.kx, (p[0] - self.lat0) * self.ky)

    def latlon(self, x: float, y: float) -> LatLon:
        return (self.lat0 + y / self.ky, self.lon0 + x / self.kx)


def path_length_m(points: Sequence[LatLon]) -> float:
    return sum(haversine_m(points[i], points[i + 1]) for i in range(len(points) - 1))


@dataclass(frozen=True)
class Nearest:
    """The closest point on a polyline to a query point."""

    point: LatLon
    distance_m: float
    segment: int  # index i of the segment points[i] -> points[i + 1]
    along_m: float  # distance from the start of the polyline to `point`
    heading_deg: float  # heading of that segment


def nearest_on_polyline(p: LatLon, line: Sequence[LatLon]) -> Nearest:
    if len(line) < 2:
        raise ValueError("a polyline needs at least 2 points")
    proj = Projection(p)
    best: Nearest | None = None
    along = 0.0
    for i in range(len(line) - 1):
        ax, ay = proj.xy(line[i])
        bx, by = proj.xy(line[i + 1])
        dx, dy = bx - ax, by - ay
        seg2 = dx * dx + dy * dy
        t = 0.0 if seg2 == 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / seg2))
        cx, cy = ax + t * dx, ay + t * dy
        dist = math.hypot(cx, cy)
        seg_len = math.sqrt(seg2)
        if best is None or dist < best.distance_m:
            heading = bearing_deg(line[i], line[i + 1]) if seg2 > 0 else (best.heading_deg if best else 0.0)
            best = Nearest(proj.latlon(cx, cy), dist, i, along + t * seg_len, heading)
        along += seg_len
    assert best is not None
    return best


def resample(points: Sequence[LatLon], step_m: float) -> list[LatLon]:
    """Points every step_m along the polyline (plus the last point). Removes vertex-density effects."""
    if len(points) < 2:
        return list(points)
    proj = Projection(points[0])
    xy = [proj.xy(p) for p in points]
    out = [points[0]]
    carry = 0.0  # distance already walked since the last emitted point
    for (ax, ay), (bx, by) in zip(xy, xy[1:]):
        seg = math.hypot(bx - ax, by - ay)
        pos = step_m - carry
        while pos <= seg:
            t = pos / seg
            out.append(proj.latlon(ax + t * (bx - ax), ay + t * (by - ay)))
            pos += step_m
        carry = (carry + seg) % step_m if seg else carry
    if haversine_m(out[-1], points[-1]) > 0.01:
        out.append(points[-1])
    return out


def simplify(points: Sequence[LatLon], tolerance_m: float) -> list[LatLon]:
    """Douglas-Peucker. Keeps the shape within tolerance_m; used to keep Overpass queries short."""
    if len(points) < 3:
        return list(points)
    proj = Projection(points[0])
    xy = [proj.xy(p) for p in points]
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        s, e = stack.pop()
        (ax, ay), (bx, by) = xy[s], xy[e]
        dx, dy = bx - ax, by - ay
        norm = math.hypot(dx, dy)
        worst, idx = -1.0, -1
        for i in range(s + 1, e):
            px, py = xy[i]
            d = math.hypot(px - ax, py - ay) if norm == 0 else abs(dy * (px - ax) - dx * (py - ay)) / norm
            if d > worst:
                worst, idx = d, i
        if worst > tolerance_m:
            keep[idx] = True
            stack += [(s, idx), (idx, e)]
    return [p for p, k in zip(points, keep) if k]


# ---------------------------------------------------------------------------------------------
# Turns
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Turn:
    at: LatLon
    along_m: float
    angle_deg: float  # signed heading change across the window; positive = right


def sharp_turns(
    points: Sequence[LatLon],
    threshold_deg: float,
    min_rate_deg_per_m: float = 1.0,
    smooth_m: float = 10.0,
    step_m: float = 5.0,
) -> list[Turn]:
    """Turns that change heading by at least threshold_deg while turning at least min_rate_deg_per_m.

    1. Resample every step_m, so the answer does not depend on how densely the road was drawn.
    2. Heading at each sample = the chord from smooth_m before it to smooth_m after it (encoded
       polylines are rounded to about 1 m, which makes 5 m chords jitter by several degrees).
    3. A turn is a run of consecutive samples, all turning the same way at >= min_rate_deg_per_m.
       Its angle is the total heading change over the run. A rounded corner drawn with many
       vertices is one run; an S-bend is two; a long highway curve (radius over ~60 m at the
       default rate) is never counted however far it bends.
    """
    pts = resample(points, step_m)
    k = max(1, round(smooth_m / step_m))
    if len(pts) < 2 * k + 2:
        return []
    headings = [bearing_deg(pts[i - k], pts[i + k]) for i in range(k, len(pts) - k)]
    min_step = min_rate_deg_per_m * step_m
    turns: list[Turn] = []
    run_start = -1
    total = 0.0

    def close(end: int) -> None:
        if run_start >= 0 and abs(total) >= threshold_deg:
            mid = k + (run_start + end) // 2  # headings[j] belongs to pts[j + k]
            turns.append(Turn(pts[mid], mid * step_m, total))

    for j in range(len(headings) - 1):
        change = angle_diff(headings[j], headings[j + 1])
        turning = abs(change) >= min_step
        if turning and run_start >= 0 and (change > 0) == (total > 0):
            total += change
            continue
        close(j)
        run_start, total = (j, change) if turning else (-1, 0.0)
    close(len(headings) - 1)
    return turns


# ---------------------------------------------------------------------------------------------
# Grades from elevation samples
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class GradeStats:
    length_m: float
    climb_m: float  # sum of rises
    descent_m: float  # sum of drops (positive number)
    max_grade_pct: float  # steepest |grade| over any window of at least min_run_m
    avg_grade_pct: float  # distance-weighted mean |grade|
    net_grade_pct: float  # (end - start) / length; sign = uphill in the direction of travel


def grade_stats(distances_m: Sequence[float], elevations_m: Sequence[float], min_run_m: float = 0.0) -> GradeStats:
    """Grades along a path from elevations at the given cumulative distances.

    min_run_m: the steepest grade is measured over runs at least this long, so one noisy sample on
    a coarse elevation grid does not read as a cliff. With 0 it is the steepest single step.
    """
    if len(distances_m) != len(elevations_m) or len(distances_m) < 2:
        raise ValueError("need matching distances and elevations, at least 2 of each")
    length = distances_m[-1] - distances_m[0]
    if length <= 0:
        raise ValueError("distances must increase")
    d, z = distances_m, elevations_m
    climb = descent = 0.0
    for i in range(len(d) - 1):
        dz = z[i + 1] - z[i]
        if dz > 0:
            climb += dz
        else:
            descent -= dz
    run_min = max(min_run_m, 1e-9)
    if run_min > length:  # path shorter than the window: one end-to-end grade
        g = abs(z[-1] - z[0]) / length * 100
        return GradeStats(length, climb, descent, g, g, (z[-1] - z[0]) / length * 100)
    # Steepest: any window at least run_min long.
    steepest = 0.0
    for i in range(len(d)):
        for j in range(i + 1, len(d)):
            if d[j] - d[i] >= run_min:
                steepest = max(steepest, abs(z[j] - z[i]) / (d[j] - d[i]) * 100)
    # Average |grade|: consecutive chunks at least run_min long (a short tail joins the last
    # chunk), distance-weighted. Every chunk is a window the steepest considered, so avg <= max.
    cuts = [0]
    for j in range(1, len(d)):
        if d[j] - d[cuts[-1]] >= run_min:
            cuts.append(j)
    cuts[-1] = len(d) - 1  # run_min <= length, so there is at least one cut after 0
    weighted = sum(abs(z[b] - z[a]) for a, b in zip(cuts, cuts[1:]))
    return GradeStats(
        length_m=length,
        climb_m=climb,
        descent_m=descent,
        max_grade_pct=steepest,
        avg_grade_pct=weighted / length * 100,
        net_grade_pct=(z[-1] - z[0]) / length * 100,
    )


def even_distances(length_m: float, samples: int) -> list[float]:
    """Cumulative distances of `samples` equally spaced points (Elevation API path sampling)."""
    if samples < 2:
        raise ValueError("need at least 2 samples")
    return [length_m * i / (samples - 1) for i in range(samples)]
