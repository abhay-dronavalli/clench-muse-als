"""Layer 1: OSM features matched to a route, grades with bridges left out, cost, and tile text."""

import pytest

from core.geo.comfort import comfort_cost, differences, measure, tiles, trade_off
from core.geo.model import RouteStats
from core.geo.sources import Elevations, OsmData

from .fixtures import at, cfg, node, way

C = cfg().comfort
ROUTE = [at(x, 0) for x in range(0, 1001, 50)]  # 1 km due east along y = 0


def road(*extra_nodes, **tags):
    """A two-way road under the route, through the given nodes (ids and positions)."""
    pts = sorted([(-50.0, 0.0), (1050.0, 0.0), *((n[1], n[2]) for n in extra_nodes)])
    ids = []
    for x, _ in pts:
        match = [n[0] for n in extra_nodes if n[1] == x]
        ids.append(match[0] if match else 10_000_000 + int(x) + 50)
    return way(pts, ids, **({"highway": "secondary"} | tags))


def on_road(x, **tags):
    """A node on the road under the route at x meters, and the (id, x, y) the road needs."""
    n = node(x, 0, **tags)
    return n, (n.id, float(x), 0.0)


def measured(nodes, ways, elevation=None, elev_points=()):
    return measure(ROUTE, OsmData(nodes=list(nodes), ways=list(ways)), elevation, list(elev_points), C)


# --- traffic calming ----------------------------------------------------------------------------


def test_traffic_calming_no_is_not_calming():
    hump, h = on_road(200, traffic_calming="hump")
    none, n = on_road(400, traffic_calming="no")
    m = measured([hump, none], [road(h, n)])
    assert dict(m.calming) == {"hump": 1}


def test_opposite_carriageway_is_not_counted():
    # A divided road: eastbound carriageway under the route, westbound 8 m north of it.
    east = node(300, 0, traffic_calming="table")
    west = node(300, 8, traffic_calming="table")
    eb = way([(-50, 0), (300, 0), (1050, 0)], [1, east.id, 2], highway="secondary", oneway="yes")
    wb = way([(1050, 8), (300, 8), (-50, 8)], [3, west.id, 4], highway="secondary", oneway="yes")
    m = measured([east, west], [eb, wb])
    assert dict(m.calming) == {"table": 1}


def test_oneway_minus_one_is_read_backwards():
    # Drawn westward but oneway=-1: traffic flows east, the way the route goes, so it counts.
    hump = node(300, 0, traffic_calming="hump")
    w = way([(1050, 0), (300, 0), (-50, 0)], [5, hump.id, 6], highway="secondary", oneway="-1")
    assert dict(measured([hump], [w]).calming) == {"hump": 1}


def test_oneway_minus_one_drawn_our_way_is_oncoming():
    # Drawn eastward, but oneway=-1: traffic flows west, so this is the other carriageway.
    hump = node(300, 0, traffic_calming="hump")
    w = way([(-50, 0), (300, 0), (1050, 0)], [13, hump.id, 14], highway="secondary", oneway="-1")
    assert sum(measured([hump], [w]).calming.values()) == 0


def test_calming_on_a_cross_street_is_not_on_the_route():
    hump = node(500, 8, traffic_calming="hump")  # 8 m off the route, on a north-south street
    cross = way([(500, -100), (500, 8), (500, 100)], [7, hump.id, 8], highway="residential")
    assert sum(measured([hump], [cross, road()]).calming.values()) == 0


# --- stop signs ---------------------------------------------------------------------------------


@pytest.mark.parametrize(("tags", "counted"), [
    ({}, 1),
    ({"direction": "forward"}, 1),  # the road is drawn eastward, like the route
    ({"direction": "backward"}, 0),
    ({"stop": "all", "direction": "backward"}, 1),  # all-way stops apply to everyone
])
def test_stop_sign_direction(tags, counted):
    stop, s = on_road(600, highway="stop", **tags)
    assert measured([stop], [road(s)]).stops == counted


def test_stop_sign_on_a_cross_street_is_not_ours():
    stop = node(500, -10, highway="stop")
    cross = way([(500, -100), (500, -10), (500, 0)], [11, stop.id, 12], highway="residential")
    assert measured([stop], [cross, road()]).stops == 0


# --- signals ------------------------------------------------------------------------------------


def test_signals_of_one_intersection_count_once():
    one = [node(500 + dx, dy, highway="traffic_signals") for dx, dy in [(-10, 0), (10, 0), (0, 8), (0, -8)]]
    other = node(900, 0, highway="traffic_signals")
    far = node(200, 40, highway="traffic_signals")  # 40 m off the route: not on it
    assert measured([*one, other, far], [road()]).signals == 2


# --- surface, bridges, grade --------------------------------------------------------------------


def test_rough_surface_meters_and_known_share():
    gravel = way([(-50, 0), (300, 0)], highway="unclassified", surface="gravel")
    smooth = way([(300, 0), (700, 0)], highway="unclassified", surface="asphalt")
    untagged = way([(700, 0), (1050, 0)], highway="unclassified")
    m = measured([], [gravel, smooth, untagged])
    assert m.rough_m == pytest.approx(300, abs=15)
    assert m.surface_known_pct == pytest.approx(70, abs=2)


def test_bad_smoothness_is_rough_even_on_asphalt():
    w = way([(-50, 0), (1050, 0)], highway="residential", surface="asphalt", smoothness="very_bad")
    assert measured([], [w]).rough_m == pytest.approx(1000, abs=15)


def test_bridge_samples_are_left_out_of_the_grade():
    pts = [at(x, 0) for x in range(0, 1001, 100)]
    # Bare-earth elevation under a bridge at 400-600 m: the ground drops 8 m into a canal.
    z = [2, 2, 2, 2, -6, -6, -6, 2, 2, 2, 2]
    elev = Elevations(z, "test", 10.0)
    bridge = way([(380, 0), (620, 0)], highway="secondary", bridge="yes")
    ground = [way([(-50, 0), (380, 0)], highway="secondary"), way([(620, 0), (1050, 0)], highway="secondary")]
    with_bridge = measured([], [bridge, *ground], elev, pts)
    assert with_bridge.climb_m == 0 and with_bridge.steepest_pct == 0
    assert with_bridge.bridge_m == pytest.approx(240, abs=20)
    without = measured([], ground, elev, pts)
    assert without.climb_m == pytest.approx(8)


def test_no_elevation_is_unknown_not_flat():
    m = measured([], [road()])
    assert m.climb_m is None and m.steepest_pct is None


# --- turns --------------------------------------------------------------------------------------


def test_turns_counted_on_the_route_line():
    line = [at(0, 0), at(300, 0), at(300, 300), at(0, 300)]
    m = measure(line, OsmData(), None, [], C)
    assert m.turns == 2


# --- cost and tiles -----------------------------------------------------------------------------


def stats(id, minutes, turns=0, signals=0, stops=0, calming=None, climb=0.0, summary="") -> RouteStats:
    calming = calming or {}
    return RouteStats(
        id=id, label="", duration_s=minutes * 60, distance_m=1000, summary=summary, polyline="",
        sharp_turns=turns, climb_m=climb, steepest_grade_pct=0.0, elevation_source="test",
        traffic_signals=signals, stop_signs=stops, traffic_calming=sum(calming.values()),
        traffic_calming_kinds=calming, rough_m=0, surface_known_pct=100, bridge_m=0, comfort_cost=0, features=[],
    )


PENALTY = cfg().unknown_penalty


def test_cost_uses_the_configured_weights():
    hump, h = on_road(200, traffic_calming="hump")
    m = measured([hump], [road(h, surface="asphalt")])
    assert comfort_cost(m, C, PENALTY) == pytest.approx(C.weights.traffic_calming * 1, abs=0.2)


def test_untagged_surface_costs_more_than_known_smooth_surface():
    smooth = measured([], [road(surface="asphalt")])
    untagged = measured([], [road()])
    assert untagged.unknown_surface_m == pytest.approx(1000, abs=15)
    assert comfort_cost(untagged, C, PENALTY) > comfort_cost(smooth, C, PENALTY)
    # ...but less than a road known to be rough all the way.
    rough = measured([], [road(surface="gravel")])
    assert comfort_cost(untagged, C, PENALTY) < comfort_cost(rough, C, PENALTY)


def test_unknown_climb_is_scored_as_the_worst_known_not_as_flat():
    unknown = measured([], [road(surface="asphalt")])  # no elevation
    assert unknown.climb_m is None
    flat = comfort_cost(unknown, C, PENALTY)
    as_worst = comfort_cost(unknown, C, PENALTY, climb_if_unknown=30.0, steep_if_unknown=4.0)
    assert as_worst == pytest.approx(flat + C.weights.climb_10m * 3 + C.weights.steep_pct * 4)


def test_one_tile_when_fastest_is_smoothest_plus_the_alternative_trade_off():
    a = stats("route-1", 16.3, turns=7, signals=19, stops=6, calming={"hump": 4}, summary="SW 17th St, SW 107th Ave")
    b = stats("route-2", 17.7, turns=10, signals=12, stops=9, calming={"hump": 3, "table": 4}, summary="SW 117th Ave, Turnpike")
    out = tiles([a, b], a, a, cfg())
    assert [t.kind for t in out] == ["fastest_and_smoothest", "alternative"]
    assert out[0].label == "Fastest and smoothest, 16 min"
    assert out[0].detail == "7 sharp turns, 19 traffic lights, 6 stop signs, 4 speed bumps"
    assert out[0].ride_profile.route_preference == "ROUTE_PREFERENCE_SMOOTHEST"
    assert out[1].label == "Via Turnpike, 18 min"
    assert out[1].detail.startswith("7 fewer traffic lights, but ")
    for part in ("3 more sharp turns", "3 more stop signs", "3 more speed bumps"):
        assert part in out[1].detail
    assert out[1].ride_profile is None  # RideProfile cannot name a specific alternative


def test_fastest_and_smoothest_tiles_use_real_differences():
    fast = stats("route-1", 14, turns=12, calming={"bump": 2})
    smooth = stats("route-2", 16, turns=3)
    out = tiles([fast, smooth], fast, smooth, cfg())
    assert [t.label for t in out] == ["Fastest, 14 min", "Smoothest, 16 min"]
    assert out[1].detail == "9 fewer sharp turns, no speed bumps mapped"
    assert out[1].ride_profile.route_preference == "ROUTE_PREFERENCE_SMOOTHEST"


def test_absence_of_bumps_is_always_worded_as_not_mapped():
    r = stats("route-1", 10)
    text = tiles([r], r, r, cfg())[0].detail
    assert "no speed bumps mapped" in text
    assert "no speed bumps," not in text and not text.endswith("no speed bumps")


def test_identical_routes_have_no_invented_difference():
    a, b = stats("route-1", 10, turns=2), stats("route-2", 12, turns=2)
    assert differences(a, b, C) == ([], [])
    assert trade_off(a, b, C) == "the same counts"


def test_non_bump_calming_is_not_called_a_speed_bump():
    a = stats("route-1", 10, calming={"chicane": 2})
    b = stats("route-2", 12)
    less, more = differences(b, a, C)
    assert less == [] and more == []  # chicanes count in the cost, but are not speed bumps
