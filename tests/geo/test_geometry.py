"""Polyline codec, distances, the turn counter, and grade math."""

import math

import pytest

from core.geo.geometry import (
    Projection,
    angle_diff,
    bearing_deg,
    decode_polyline,
    encode_polyline,
    even_distances,
    grade_stats,
    haversine_m,
    nearest_on_polyline,
    path_length_m,
    resample,
    sharp_turns,
    simplify,
)

ORIGIN = (25.7574, -80.3733)  # near FIU; any point works


def walk(*legs: tuple[float, float], start=ORIGIN, step_m: float = 5.0) -> list[tuple[float, float]]:
    """A path from `start` made of (heading_deg, length_m) legs, drawn with a vertex every step_m."""
    proj = Projection(start)
    x = y = 0.0
    pts = [start]
    for heading, length in legs:
        n = max(1, round(length / step_m))
        for _ in range(n):
            x += math.sin(math.radians(heading)) * length / n
            y += math.cos(math.radians(heading)) * length / n
            pts.append(proj.latlon(x, y))
    return pts


# --- polyline codec -----------------------------------------------------------------------------


def test_decode_matches_googles_published_example():
    pts = decode_polyline("_p~iF~ps|U_ulLnnqC_mqNvxq`@")
    assert pts == pytest.approx([(38.5, -120.2), (40.7, -120.95), (43.252, -126.453)])


def test_encode_decode_round_trip():
    pts = [(25.75741, -80.37332), (25.75001, -80.36), (25.7, -80.4)]
    assert decode_polyline(encode_polyline(pts)) == pytest.approx(pts)


# --- distances and headings ---------------------------------------------------------------------


def test_haversine_one_degree_of_latitude():
    assert haversine_m((25.0, -80.0), (26.0, -80.0)) == pytest.approx(111_195, rel=1e-3)


def test_bearing_cardinal_directions():
    assert bearing_deg(ORIGIN, (ORIGIN[0] + 0.01, ORIGIN[1])) == pytest.approx(0, abs=0.01)
    assert bearing_deg(ORIGIN, (ORIGIN[0], ORIGIN[1] + 0.01)) == pytest.approx(90, abs=0.01)


def test_angle_diff_wraps_and_signs():
    assert angle_diff(350, 10) == pytest.approx(20)
    assert angle_diff(10, 350) == pytest.approx(-20)
    assert angle_diff(0, 180) == pytest.approx(180)


def test_nearest_on_polyline_perpendicular_foot():
    line = walk((90, 100))  # 100 m due east
    proj = Projection(ORIGIN)
    q = proj.latlon(40, 12)  # 12 m north of the 40 m mark
    near = nearest_on_polyline(q, line)
    assert near.distance_m == pytest.approx(12, abs=0.05)
    assert near.along_m == pytest.approx(40, abs=0.1)
    assert near.heading_deg == pytest.approx(90, abs=0.5)


def test_resample_spacing_and_length_kept():
    line = walk((0, 50), (90, 50), step_m=25)
    pts = resample(line, 5)
    gaps = [haversine_m(a, b) for a, b in zip(pts, pts[1:-1])]
    assert all(g <= 5.01 for g in gaps)
    assert path_length_m(line) == pytest.approx(100, abs=0.1)


def test_simplify_keeps_corner_drops_collinear():
    line = walk((0, 100), (90, 100), step_m=5)
    simple = simplify(line, 1.0)
    assert len(simple) == 3


# --- turn counter -------------------------------------------------------------------------------


def test_straight_road_has_no_turns():
    assert sharp_turns(walk((45, 500)), threshold_deg=60) == []


def test_right_angle_corner_is_one_right_turn():
    turns = sharp_turns(walk((0, 200), (90, 200)), threshold_deg=60)
    assert len(turns) == 1
    assert turns[0].angle_deg == pytest.approx(90, abs=10)
    assert turns[0].along_m == pytest.approx(200, abs=15)


def test_left_turn_is_negative():
    turns = sharp_turns(walk((0, 200), (270, 200)), threshold_deg=60)
    assert len(turns) == 1 and turns[0].angle_deg < 0


def test_corner_drawn_with_many_vertices_is_still_one_turn():
    # A 90-degree bend drawn as nine 10-degree steps, 3 m apart (a rounded corner).
    legs = [(0.0, 150.0)] + [(10.0 * i, 3.0) for i in range(1, 10)] + [(90.0, 150.0)]
    assert len(sharp_turns(walk(*legs, step_m=1), threshold_deg=60)) == 1


def test_gentle_curve_below_threshold_is_not_counted():
    legs = [(0.0, 100.0)] + [(2.0 * i, 10.0) for i in range(1, 16)]  # 30 degrees over 150 m
    assert sharp_turns(walk(*legs), threshold_deg=45) == []


def test_s_bend_is_two_turns():
    turns = sharp_turns(walk((0, 150), (90, 60), (0, 150)), threshold_deg=60)
    assert [t.angle_deg > 0 for t in turns] == [True, False]


def test_turns_do_not_depend_on_vertex_density():
    sparse = walk((0, 200), (90, 200), (180, 200), step_m=100)
    dense = walk((0, 200), (90, 200), (180, 200), step_m=2)
    assert len(sharp_turns(sparse, 60)) == len(sharp_turns(dense, 60)) == 2


def test_rounding_jitter_on_a_straight_road_is_not_a_turn():
    # Encoded polylines keep 5 decimals (about 1.1 m): round-trip a straight, diagonal road.
    road = decode_polyline(encode_polyline(walk((33, 1500), step_m=7)))
    assert sharp_turns(road, threshold_deg=45) == []


def test_wide_highway_curve_is_not_sharp_however_far_it_bends():
    # 90 degrees on a 200 m radius (0.29 degrees per meter), drawn every ~5 m.
    arc = 200 * math.pi / 2
    legs = [(0.0, 100.0)] + [(90.0 * (i + 0.5) / 60, arc / 60) for i in range(60)] + [(90.0, 100.0)]
    assert sharp_turns(walk(*legs), threshold_deg=60) == []


def test_threshold_is_respected():
    path = walk((0, 200), (50, 200))  # a 50-degree bend
    assert len(sharp_turns(path, threshold_deg=45)) == 1
    assert sharp_turns(path, threshold_deg=60) == []


# --- grade math ---------------------------------------------------------------------------------


def test_constant_uphill():
    g = grade_stats([0, 50, 100], [10, 12, 14])
    assert g.climb_m == pytest.approx(4)
    assert g.descent_m == 0
    assert g.max_grade_pct == pytest.approx(4)
    assert g.avg_grade_pct == pytest.approx(4)
    assert g.net_grade_pct == pytest.approx(4)


def test_up_then_down_nets_zero_but_climbs():
    g = grade_stats([0, 10, 20], [0, 1, 0])
    assert g.climb_m == pytest.approx(1)
    assert g.descent_m == pytest.approx(1)
    assert g.net_grade_pct == pytest.approx(0)
    assert g.avg_grade_pct == pytest.approx(10)
    assert g.max_grade_pct == pytest.approx(10)


def test_min_run_smooths_a_single_noisy_step():
    d = [0, 5, 10, 15, 20, 25, 30]
    z = [0, 0, 0.5, 0, 0, 0, 0]  # one 0.5 m blip: 10% over 5 m
    assert grade_stats(d, z).max_grade_pct == pytest.approx(10)
    assert grade_stats(d, z, min_run_m=20).max_grade_pct == pytest.approx(2.5)


@pytest.mark.parametrize("min_run", [0, 10, 20])
def test_average_never_exceeds_steepest(min_run):
    d = [0, 5, 10, 15, 20, 25, 30, 35]
    z = [0.0, 0.2, 0.0, 0.25, 0.1, 0.3, 0.05, 0.2]  # lidar-like jitter on flat ground
    g = grade_stats(d, z, min_run_m=min_run)
    assert g.avg_grade_pct <= g.max_grade_pct + 1e-9


def test_path_shorter_than_min_run_uses_end_to_end():
    assert grade_stats([0, 8], [0, 0.4], min_run_m=20).max_grade_pct == pytest.approx(5)


def test_flat():
    g = grade_stats(even_distances(100, 5), [3.0] * 5)
    assert (g.climb_m, g.max_grade_pct, g.avg_grade_pct) == (0, 0, 0)


def test_grade_rejects_bad_input():
    with pytest.raises(ValueError):
        grade_stats([0], [1])
    with pytest.raises(ValueError):
        grade_stats([0, 0], [1, 2])


def test_even_distances():
    assert even_distances(30, 4) == [0, 10, 20, 30]
