"""Layer 2: candidates, the eight factors, scoring, and the "unknown is not good" rule."""

import pytest

from core.geo.dropoff import dropoff_request, rank_candidates, reason, score_factors, targets
from core.geo.sources import Elevations, OsmData
from core.geo.walk import WalkGraph, walk

from .fixtures import ORIGIN, at, building, cfg, flat, node, scene, way

LABELS_OK = {"entrance_access", "entrance_type", "slope", "steps", "kerb", "sidewalk", "road", "walk"}


def ranked(osm: OsmData, elevation=flat, destination_way=None, center=ORIGIN):
    c = cfg()
    return rank_candidates(osm, center, c.dropoff, c.unknown_penalty, elevation, destination_way)


def factor(candidate, name):
    return next(f for f in candidate.factors if f.name == name)


# --- unknown is not good ------------------------------------------------------------------------


def _score(values):
    return score_factors(values, {k: 1.0 for k in values}, unknown_penalty=0.3)


def test_unknown_counts_as_the_penalty_not_as_good():
    good, _ = _score({"a": (1.0, ""), "b": (1.0, "")})
    unknown, factors = _score({"a": (1.0, ""), "b": (None, "")})
    assert unknown == pytest.approx(65.0)  # (1.0 + 0.3) / 2
    assert unknown < good
    assert factors[1].value is None and factors[1].used == 0.3


def test_unknown_scores_below_a_mediocre_known_value():
    unknown, _ = _score({"a": (None, "")})
    mediocre, _ = _score({"a": (0.4, "")})
    assert unknown < mediocre


def test_every_unknown_is_named_in_the_reason():
    osm, _, _ = scene(kerb=None, entrance_tags={"entrance": "main"})  # no wheelchair tag, no kerb
    cands, _ = ranked(osm)
    top = cands[0]
    assert set(top.unknown) >= {"wheelchair access", "curb"}
    assert "unknown: " in top.reason
    for label in top.unknown:
        assert label in top.reason.split("unknown: ", 1)[1]


def test_fully_known_good_candidate_has_no_unknown_clause():
    osm, _, _ = scene()
    top = ranked(osm)[0][0]
    assert top.unknown == []
    assert "unknown" not in top.reason


def test_missing_wheelchair_tag_ranks_below_wheelchair_yes():
    good = ranked(scene()[0])[0][0]
    missing = ranked(scene(entrance_tags={"entrance": "main"})[0])[0][0]
    assert factor(missing, "entrance_access").value is None
    assert missing.score < good.score


def test_failed_elevation_is_an_unknown_slope_with_a_note():
    def broken(_pts):
        raise TimeoutError

    cands, notes = ranked(scene()[0], elevation=broken)
    assert factor(cands[0], "slope").value is None
    assert any("Elevation lookup failed" in n for n in notes)


def test_elevation_gap_is_unknown_slope():
    def gap(pts):
        return Elevations([1.0] * (len(pts) - 1) + [None], "test", 1.0)

    assert factor(ranked(scene()[0], elevation=gap)[0][0], "slope").value is None


# --- factors ------------------------------------------------------------------------------------


@pytest.mark.parametrize(("kerb", "value"), [("lowered", 1.0), ("flush", 1.0), ("rolled", 0.4), ("raised", 0.0), ("yes", None), (None, None)])
def test_kerb_values(kerb, value):
    assert factor(ranked(scene(kerb=kerb)[0])[0][0], "kerb").value == value


def test_steps_on_the_only_path_score_zero():
    top = ranked(scene(walk_steps=True)[0])[0][0]
    assert top.walk_kind == "path with steps"
    assert factor(top, "steps").value == 0.0
    assert "steps on the way" in top.reason


def test_step_free_detour_is_taken_over_shorter_steps():
    osm, ent, _ = scene(walk_steps=True, step_free_detour=True)
    top = ranked(osm)[0][0]
    assert top.walk_kind == "step-free path"
    assert factor(top, "steps").value == 0.9  # the detour is a ramp (incline=5%)
    assert "ramp on a step-free path" in top.reason


def test_no_mapped_path_falls_back_to_a_straight_line_and_steps_are_unknown():
    ent = node(0, 0, entrance="main", wheelchair="yes")
    osm = OsmData(nodes=[ent], ways=[building(-20, 0, 20, 30, [ent]), way([(-80, -20), (80, -20)], highway="service")])
    top = ranked(osm)[0][0]
    assert top.walk_kind == "straight line"
    assert factor(top, "steps").value is None
    assert "absence in OSM is not proof" in factor(top, "steps").evidence


def test_quiet_road_beats_major_road():
    service = ranked(scene(road="service")[0])[0][0]
    primary = ranked(scene(road="primary")[0])[0][0]
    assert factor(service, "road").value > factor(primary, "road").value
    assert service.score > primary.score


def test_no_sidewalk_and_no_footway_is_unknown_sidewalk():
    osm, _, _ = scene(sidewalk=False)
    assert factor(ranked(osm)[0][0], "sidewalk").value is None


def test_walk_across_a_major_road_is_penalised():
    ent = node(0, 0, entrance="main", wheelchair="yes")
    osm = OsmData(
        nodes=[ent],
        ways=[
            building(-20, 0, 20, 30, [ent]),
            way([(-80, -40), (80, -40)], highway="service"),
            way([(-80, -20), (80, -20)], highway="primary", name="Big Road"),
        ],
    )
    top = next(c for c in ranked(osm)[0] if c.road_class == "service")
    assert factor(top, "walk").value <= 0.2
    assert "walk crosses Big Road" in top.reason


# --- targets ------------------------------------------------------------------------------------


def test_fire_exits_and_exits_are_not_targets():
    fire = node(0, 0, entrance="emergency")
    out = node(5, 0, entrance="exit")
    main = node(-5, 0, entrance="main")
    osm = OsmData(nodes=[fire, out, main], ways=[building(-20, 0, 20, 30, [fire, out, main])])
    tgts, _ = targets(osm, ORIGIN, 150)
    assert [t.node.id for t in tgts] == [main.id]


def test_emergency_department_entrance_is_labelled_and_not_the_best_type():
    er = node(0, 0, entrance="yes", emergency="emergency_ward_entrance")
    tgts, _ = targets(OsmData(nodes=[er], ways=[building(-20, 0, 20, 30, [er])]), ORIGIN, 150)
    assert tgts[0].kind == "emergency_ward_entrance"
    assert tgts[0].kind_value < 1.0


def test_only_the_destination_buildings_entrances_when_it_has_some():
    mine = node(0, 0, entrance="main")
    other = node(60, 0, entrance="main")
    b1 = building(-20, 0, 20, 30, [mine], name="Mine")
    b2 = building(50, 0, 80, 30, [other], name="Other")
    tgts, notes = targets(OsmData(nodes=[mine, other], ways=[b1, b2]), ORIGIN, 150, destination_way=b1.id)
    assert [t.node.id for t in tgts] == [mine.id]
    assert tgts[0].label == "Main entrance, Mine"
    assert notes == []


def test_destination_building_without_entrances_uses_nearby_ones_with_a_note():
    other = node(60, 0, entrance="main")
    b1 = building(-20, 0, 20, 30)
    b2 = building(50, 0, 80, 30, [other])
    tgts, notes = targets(OsmData(nodes=[other], ways=[b1, b2]), ORIGIN, 150, destination_way=b1.id)
    assert [t.node.id for t in tgts] == [other.id]
    assert "has no entrance mapped" in notes[0]


def test_no_entrance_mapped_aims_at_the_wall_and_says_entrance_unknown():
    osm = OsmData(nodes=[], ways=[building(-20, 0, 20, 30, name="Clinic"), way([(-80, -20), (80, -20)], highway="service")])
    cands, notes = ranked(osm)
    assert "No usable entrance is mapped" in notes[0]
    top = cands[0]
    assert factor(top, "entrance_access").value is None and factor(top, "entrance_type").value is None
    assert "no entrance mapped" in top.description


def test_parking_garages_are_not_destinations():
    osm = OsmData(nodes=[], ways=[building(-20, 0, 20, 30, building="parking"), way([(-80, -20), (80, -20)], highway="service")])
    tgts, notes = targets(osm, ORIGIN, 150)
    assert tgts[0].wall == () and tgts[0].node is None
    assert "No entrance or building is mapped" in notes[0]


# --- ranking and the request --------------------------------------------------------------------


def test_nearby_stops_are_deduplicated():
    cands, _ = ranked(scene()[0])
    stops = [c.stop.tuple() for c in cands]
    from core.geo.geometry import haversine_m

    assert all(haversine_m(a, b) > cfg().dropoff.dedupe_m for i, a in enumerate(stops) for b in stops[i + 1 :])


def test_request_uses_the_best_and_falls_back_to_the_second():
    ent = node(0, 0, entrance="main", wheelchair="yes")
    osm = OsmData(
        nodes=[ent],
        ways=[building(-20, 0, 20, 30, [ent]), way([(-80, -20), (80, -20)], highway="service"), way([(-60, -60), (60, -60)], highway="primary")],
    )
    cands, _ = ranked(osm)
    req = dropoff_request(cands)
    assert req.point == cands[0].stop and req.fallback_point == cands[1].stop
    assert req.description == cands[0].description and req.reason == cands[0].reason


def test_no_candidates_no_request():
    assert dropoff_request([]) is None


def test_factor_names_are_the_configured_weights():
    top = ranked(scene()[0])[0][0]
    assert {f.name for f in top.factors} == LABELS_OK == set(cfg().dropoff.weights.model_dump())


# --- walk graph ---------------------------------------------------------------------------------


def test_walk_graph_ignores_private_ways_and_roads():
    a, b = 1, 2
    osm = OsmData(ways=[way([(0, 0), (10, 0)], [a, b], highway="footway", access="private"), way([(0, 5), (10, 5)], highway="service")])
    assert WalkGraph.build(osm).coords == {}


def test_walk_to_a_wall_is_a_straight_line():
    osm, _, _ = scene()
    w = walk(WalkGraph.build(osm), at(0, -20), at(0, 0), goal_node=None)
    assert w.kind == "straight line" and w.length_m == pytest.approx(20, abs=0.1)


def test_reason_orders_good_then_bad_then_unknown():
    top = ranked(scene(road="primary", kerb=None)[0])[0][0]
    text = reason(top.factors, top.slope)
    assert text.index("Wheelchair-accessible") < text.index("busy road") < text.index("unknown: curb")
