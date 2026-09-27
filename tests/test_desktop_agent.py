"""Desktop agent, pure parts (docs/desktop-control.md): Eyedid coordinates, gaze filtering and look-
back, snapping and zoom, and the gesture state machine. No Qt, no Windows calls, no SDK."""

import math

import pytest

from desktop.agent.gaze import STALE_S, Gaze, OneEuro, Sample, Trail, parse_worker_line, sample_from
from desktop.agent.interaction import (BACK_CONFIRM_S, DEBOUNCE_S, LATE_CLENCH_S, Calibrate, CancelCalibration, Click,
                                       Controller, FocusBoard, Keys, SetTarget, ZoomShot)
from desktop.agent.snap import Candidate, Rect, Target, choose, make_zoom, usable
from desktop.eyedid.convert import Display
from desktop.eyedid.worker import gaze_message, parse_screen

LAPTOP = Display(1920, 1080, 344.0, 193.0)
SCREEN = Rect(0, 0, 1920, 1080)
PX_MM = LAPTOP.px_per_mm


# --- Eyedid coordinates -----------------------------------------------------------------------------


def test_camera_millimetres_map_to_pixels_as_the_sdk_does():
    # makeDefaultCameraToDisplayConverter: the camera sits at the top-center of the screen.
    assert LAPTOP.to_px(0, 0) == pytest.approx((960, 0))
    assert LAPTOP.to_px(-172, 0) == pytest.approx((0, 0))
    assert LAPTOP.to_px(172, -193) == pytest.approx((1920, 1080))  # y points up from the camera
    for px in [(0, 0), (123.4, 567.8), (1919, 1079)]:
        assert LAPTOP.to_px(*LAPTOP.to_mm(*px)) == pytest.approx(px)


def test_a_screen_argument_parses():
    assert parse_screen("1920x1080@344x193") == LAPTOP


class FakeGaze:
    def __init__(self, x, y, fx=-1001.0, fy=-1001.0, tracking_state=0, movement_state=0):
        self.x, self.y, self.fixation_x, self.fixation_y = x, y, fx, fy
        self.tracking_state, self.movement_state = tracking_state, movement_state


def test_a_worker_gaze_line_is_in_pixels_and_missing_values_are_null():
    msg = gaze_message(LAPTOP, 1_700_000_000_123, FakeGaze(0, -96.5, float("nan"), 0))
    assert msg == {"type": "gaze", "t": 1_700_000_000.123, "x": 960.0, "y": 540.0, "fx": None, "fy": None,
                   "state": "SUCCESS", "move": "fixation"}
    lost = gaze_message(LAPTOP, 0, FakeGaze(-1001.0, -1001.0, tracking_state=1, movement_state=3))
    assert (lost["x"], lost["state"], lost["move"]) == (None, "FACE_MISSING", "unknown")


def test_only_protocol_lines_are_read_from_the_worker():
    assert parse_worker_line("Eyedid Info  | Authorization trying... 1") is None
    assert parse_worker_line("{broken") is None
    assert parse_worker_line('{"type": "status", "state": "running"}\n') == {"type": "status", "state": "running"}
    s = sample_from({"type": "gaze", "t": 5.0, "x": 10, "y": 20, "state": "SUCCESS"})
    assert s == Sample(5.0, 10.0, 20.0, "SUCCESS") and s.found
    assert not sample_from({"type": "gaze", "t": 5.0, "x": 10, "y": 20, "state": "GAZE_NOT_FOUND"}).found


# --- gaze -------------------------------------------------------------------------------------------


def test_one_euro_smooths_jitter_but_follows_a_jump():
    f = OneEuro(1920, 1080)
    xs = [f(960 + (8 if i % 2 else -8), 540, i / 30)[0] for i in range(60)]
    assert max(abs(x - 960) for x in xs[30:]) < 4  # +/- 8 px jitter shrinks
    for i in range(60, 75):
        x, _ = f(1500, 540, i / 30)
    assert x > 1450  # half a second after a saccade it is there


def test_gaze_is_stale_after_half_a_second_and_kept_on_the_screen():
    g = Gaze(1920, 1080)
    g.feed(Sample(10.0, 2500, -40))
    assert g.point == (1919, 0)
    assert g.fresh(10.0 + STALE_S) and not g.fresh(10.0 + STALE_S + 0.01)
    g.feed(Sample(10.2, None, None, "FACE_MISSING"))
    assert g.tracker_alive(10.6) and not g.fresh(10.6)


def test_the_trail_answers_what_was_true_then():
    trail: Trail[str] = Trail(2.0)
    trail.add(1.0, "a")
    trail.add(2.0, "b")
    assert trail.at(1.5) == "a" and trail.at(2.5) == "b"
    assert trail.at(0.5) == "a"  # before the oldest: the oldest
    trail.add(5.0, "c")
    assert trail.at(2.5) == "c"  # "a" and "b" are gone (older than 2 s)


# --- snapping -----------------------------------------------------------------------------------------


def button(left, top, w=60, h=30, name="b"):
    return Candidate(Rect(left, top, left + w, top + h), name, "Button")


def test_a_lone_button_near_the_gaze_is_sure():
    target = choose([button(500, 500)], 520, 540, radius=80, slack=30)
    assert target.sure and target.click_point == (530, 515)


def test_two_close_buttons_are_not_sure_and_far_apart_ones_are():
    a, b = button(500, 500, name="a"), button(566, 500, name="b")  # 6 px apart
    target = choose([a, b], 530, 515, radius=80, slack=45)  # 8 mm on this laptop
    assert target.candidate == a and not target.sure
    assert target.click_point == (530, 515)  # not sure: a clench zooms here instead
    far = button(700, 500, name="far")
    assert choose([a, far], 530, 515, radius=80, slack=45).sure


def test_nothing_in_reach_is_a_spot_to_zoom():
    assert choose([button(0, 0)], 900, 900, radius=80, slack=30) == Target((900, 900))


def test_panes_and_containers_are_not_targets():
    pane = Candidate(Rect(0, 0, 1900, 1000), "page", "Pane")
    lst = Candidate(Rect(100, 100, 400, 400), "list", "List")
    item = Candidate(Rect(110, 110, 390, 140), "item", "ListItem")
    assert usable([pane, lst, item], SCREEN) == [item]


def test_the_agent_tab_wins_when_the_gaze_is_on_it():
    tab = Candidate(Rect(1870, 440, 1920, 640), "Clench", "agent:menu")
    target = choose([tab, button(1850, 520)], 1880, 530, radius=80, slack=30)
    assert target.candidate == tab and target.sure


def test_zoom_maps_the_panel_back_to_the_screen():
    z = make_zoom(1900, 20, SCREEN, factor=3)
    assert z.source.right == 1920 and z.source.top == 0  # kept on the screen
    assert z.factor == pytest.approx(3)
    px, py = z.to_panel(1850, 60)
    assert z.to_real(px, py) == pytest.approx((1850, 60))
    assert z.to_real(0, 0) is None  # outside the panel


# --- the state machine ---------------------------------------------------------------------------------


@pytest.fixture
def ctl():
    c = Controller(SCREEN, PX_MM)
    c.set_input_target("desktop")
    c.set_candidates([button(300, 300, name="ok"), button(900, 600, name="a"), button(962, 600, name="b")])
    return c


def look(c, x, y, now, fresh=True):
    """The eyes rest on (x, y) for 0.3 s up to `now`, as real eyes do before a clench."""
    c.tick(now - 0.3, (x, y), fresh)
    c.tick(now, (x, y), fresh)


def test_nothing_happens_until_the_desktop_is_the_target():
    c = Controller(SCREEN, PX_MM)
    c.set_candidates([button(300, 300)])
    look(c, 310, 310, 1.0)
    assert c.on_gesture("CLENCH", 1.0, 1.0) == [] and c.target is None


def test_a_clench_clicks_the_sure_element_under_the_gaze(ctl):
    look(ctl, 320, 310, 1.0)
    assert ctl.target.sure and ctl.target.candidate.name == "ok"
    assert ctl.on_gesture("CLENCH", 1.0, 1.0) == [Click(330, 315)]


def test_the_clench_uses_the_highlight_from_before_it(ctl):
    look(ctl, 320, 310, 1.0)
    ctl.tick(1.2, (1500, 100), True)  # the eyes jump as the jaw clenches
    assert ctl.on_gesture("CLENCH", 1.3, 1.3) == [Click(330, 315)]  # 1.3 - 0.25 = 1.05: still on "ok"


def test_an_unsure_spot_opens_the_zoom_and_the_next_clench_clicks_inside_it(ctl):
    look(ctl, 961, 615, 1.0)  # between "a" and "b"
    (shot,) = ctl.on_gesture("CLENCH", 1.0, 1.0)
    assert isinstance(shot, ZoomShot) and ctl.mode == "zoom"
    zoom = shot.zoom
    look(ctl, *zoom.to_panel(985, 615), 2.0)  # well inside "b" now, magnified
    assert ctl.target.sure and ctl.target.candidate.name == "b"
    assert ctl.on_gesture("CLENCH", 2.0, 2.0) == [Click(992, 615)]
    assert ctl.mode == "pointing"


def test_a_double_blink_closes_the_zoom_at_once(ctl):
    look(ctl, 961, 615, 1.0)
    ctl.on_gesture("CLENCH", 1.0, 1.0)
    assert ctl.on_gesture("DOUBLE_BLINK", 1.5, 1.5) == [] and ctl.mode == "pointing"


def test_go_back_needs_a_clench_within_the_prompt(ctl):
    look(ctl, 320, 310, 1.0)
    assert ctl.on_gesture("DOUBLE_BLINK", 1.0, 1.0) == [] and ctl.mode == "back"
    assert ctl.on_gesture("CLENCH", 2.0, 2.0) == [Keys("alt+left")]
    assert ctl.mode == "pointing"


def test_doing_nothing_on_the_prompt_does_nothing_and_a_late_clench_is_ignored(ctl):
    look(ctl, 320, 310, 1.0)
    ctl.on_gesture("DOUBLE_BLINK", 1.0, 1.0)
    look(ctl, 320, 310, 1.0 + BACK_CONFIRM_S)
    assert ctl.mode == "pointing"
    now = 1.0 + BACK_CONFIRM_S + LATE_CLENCH_S / 2
    look(ctl, 320, 310, now)
    assert ctl.on_gesture("CLENCH", now, now) == []  # meant for the prompt: no click
    later = now + LATE_CLENCH_S
    look(ctl, 320, 310, later)
    assert ctl.on_gesture("CLENCH", later, later) == [Click(330, 315)]


def test_no_click_without_eyes(ctl):
    look(ctl, 320, 310, 1.0, fresh=False)
    assert ctl.on_gesture("CLENCH", 1.0, 1.0) == []
    assert ctl.toast[0] == "Eyes not detected"


def test_clenches_are_debounced(ctl):
    look(ctl, 320, 310, 1.0)
    assert ctl.on_gesture("CLENCH", 1.0, 1.0)
    assert ctl.on_gesture("CLENCH", 1.1, 1.0 + DEBOUNCE_S / 2) == []


def palette_tile(ctl, action):
    return next(t for t in ctl.palette_tiles() if t.agent_action == action)


def open_palette(ctl, now):
    look(ctl, *ctl.menu_tab.rect.center, now)
    assert ctl.on_gesture("CLENCH", now, now) == [] and ctl.mode == "palette"


def use_tile(ctl, action, now):
    look(ctl, *palette_tile(ctl, action).rect.center, now)
    return ctl.on_gesture("CLENCH", now, now)


def test_the_palette_arms_a_right_click_for_one_click(ctl):
    open_palette(ctl, 1.0)
    assert use_tile(ctl, "right", 2.0) == [] and ctl.armed == "right"
    look(ctl, 320, 310, 3.0)
    assert ctl.on_gesture("CLENCH", 3.0, 3.0) == [Click(330, 315, "right")]
    look(ctl, 320, 310, 4.0)
    assert ctl.on_gesture("CLENCH", 4.0, 4.0) == [Click(330, 315)]  # back to left


def test_a_double_blink_disarms_before_it_asks_to_go_back(ctl):
    open_palette(ctl, 1.0)
    use_tile(ctl, "double", 2.0)
    assert ctl.on_gesture("DOUBLE_BLINK", 3.0, 3.0) == [] and ctl.armed == "left" and ctl.mode == "pointing"


def test_pause_stops_clicks_and_the_palette_still_resumes(ctl):
    open_palette(ctl, 1.0)
    use_tile(ctl, "pause", 2.0)
    look(ctl, 320, 310, 3.0)
    assert ctl.on_gesture("CLENCH", 3.0, 3.0) == [] and ctl.toast[0] == "Clicks are paused"
    open_palette(ctl, 4.0)
    use_tile(ctl, "resume", 5.0)
    look(ctl, 320, 310, 6.0)
    assert ctl.on_gesture("CLENCH", 6.0, 6.0) == [Click(330, 315)]


def test_the_type_tile_asks_to_compose(ctl):
    from desktop.agent.interaction import Compose

    open_palette(ctl, 1.0)
    assert use_tile(ctl, "type", 2.0) == [Compose()] and ctl.mode == "pointing"


def test_the_board_tile_hands_input_to_the_board(ctl):
    open_palette(ctl, 1.0)
    assert use_tile(ctl, "board", 2.0) == [SetTarget("board"), FocusBoard()]


def test_calibration_ignores_clenches_and_a_double_blink_stops_it(ctl):
    open_palette(ctl, 1.0)
    assert use_tile(ctl, "calibrate", 2.0) == [Calibrate()]
    assert ctl.on_gesture("CLENCH", 3.0, 3.0) == []
    assert ctl.on_gesture("DOUBLE_BLINK", 4.0, 4.0) == [CancelCalibration()] and ctl.mode == "pointing"


def test_the_help_countdown_closes_our_screens_and_blocks_gestures(ctl):
    look(ctl, 961, 615, 1.0)
    ctl.on_gesture("CLENCH", 1.0, 1.0)
    ctl.set_help(True)
    assert ctl.mode == "pointing" and ctl.zoom is None
    look(ctl, 320, 310, 2.0)
    assert ctl.on_gesture("CLENCH", 2.0, 2.0) == [] and ctl.target is None
    ctl.set_help(False)
    look(ctl, 320, 310, 3.0)
    assert ctl.on_gesture("CLENCH", 3.0, 3.0) == [Click(330, 315)]


def test_losing_the_desktop_target_drops_an_armed_click(ctl):
    open_palette(ctl, 1.0)
    use_tile(ctl, "right", 2.0)
    ctl.set_input_target("board")
    ctl.set_input_target("desktop")
    look(ctl, 320, 310, 3.0)
    assert ctl.on_gesture("CLENCH", 3.0, 3.0) == [Click(330, 315)]


def test_palette_tiles_fit_the_screen_and_do_not_overlap(ctl):
    tiles = ctl.palette_tiles()
    assert len(tiles) == 9
    for i, a in enumerate(tiles):
        assert SCREEN.encloses(a.rect)
        assert not any(a.rect.intersects(b.rect) for b in tiles[i + 1:])
    assert math.isclose(ctl.menu_tab.rect.right, 1920)


# --- Savitzky-Golay ----------------------------------------------------------------------------------

import random  # noqa: E402
import statistics  # noqa: E402

from desktop.agent.gaze import SavitzkyGolay  # noqa: E402


def feed(chain, points):
    out = None
    for x, y, t in points:
        out = (x, y)
        for f in chain:
            out = f(*out, t)
    return out


def noisy_rest_then_jump(chain, fps=30, sigma=30.0, seed=1):
    """Jitter left at rest (std of x, px) and how long a 600 px jump takes to get 90% there (s)."""
    rng = random.Random(seed)
    t, rest = 0.0, []
    for i in range(3 * fps):
        out = feed(chain, [(500 + rng.gauss(0, sigma), 500 + rng.gauss(0, sigma), t)])
        if i > fps:
            rest.append(out[0])
        t += 1 / fps
    for i in range(2 * fps):
        out = feed(chain, [(1100 + rng.gauss(0, sigma), 500 + rng.gauss(0, sigma), t)])
        t += 1 / fps
        if out[0] >= 1040:
            return statistics.pstdev(rest), (i + 1) / fps
    return statistics.pstdev(rest), float("inf")


def test_savitzky_golay_follows_a_smooth_path_exactly():
    sg = SavitzkyGolay(window_s=0.5, order=2, lag_s=0.08)
    path = [(3 + 40 * t - 7 * t * t, 5 * t, t) for t in [i / 30 for i in range(40)]]
    x, y = feed([sg], path)
    at = path[-1][2] - 0.08  # it reads the fit 80 ms back: exactly the path there, no noise to remove
    assert (x, y) == pytest.approx((3 + 40 * at - 7 * at * at, 5 * at), abs=1e-6)


def test_savitzky_golay_handles_an_uneven_frame_rate_and_starts_over_after_a_gap():
    sg = SavitzkyGolay()
    times = [0.0, 0.03, 0.09, 0.1, 0.17, 0.25, 0.26, 0.34]
    x, _ = feed([sg], [(100 + 10 * t, 0, t) for t in times])
    assert x == pytest.approx(100 + 10 * (0.34 - 0.08))
    assert sg(900, 900, 5.0) == (900, 900)  # eyes back after 4.7 s: no pull toward the old spot


def test_savitzky_golay_then_one_euro_halves_the_jitter_and_still_follows_a_jump():
    euro_jitter, euro_reach = noisy_rest_then_jump([OneEuro(1920, 1080)])
    both_jitter, both_reach = noisy_rest_then_jump([SavitzkyGolay(), OneEuro(1920, 1080)])
    assert both_jitter < euro_jitter * 0.6  # 13 px -> 7 px on 30 px noise at 30 fps
    assert both_reach <= 0.3  # a jump across a third of the screen still lands within 300 ms


def test_gaze_smooths_with_savitzky_golay_then_one_euro():
    g = Gaze(1920, 1080, sg=SavitzkyGolay())
    for i in range(20):
        g.feed(Sample(i / 30, 500 + (30 if i % 2 else -30), 500))
    assert abs(g.point[0] - 500) < 15  # +/- 30 px alternating jitter mostly gone


def test_typed_text_is_unicode_key_presses_with_enter_for_newlines():
    from desktop.agent.winput import KEYEVENTF_KEYUP, KEYEVENTF_UNICODE, VK_RETURN, text_keys

    down, up = KEYEVENTF_UNICODE, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP
    assert text_keys("é\r\n") == [(0, 0xE9, down), (0, 0xE9, up), (VK_RETURN, 0, 0), (VK_RETURN, 0, KEYEVENTF_KEYUP)]
    # an emoji is two UTF-16 units, each pressed and released
    assert [scan for _, scan, _ in text_keys("\U0001F600")] == [0xD83D, 0xD83D, 0xDE00, 0xDE00]


# --- scroll and drag -----------------------------------------------------------------------------

from desktop.agent.interaction import (SCROLL_EVERY_S, WHEEL_NOTCH, MoveTo, Press, Release,  # noqa: E402
                                       Scroll)


def arm(ctl, action, now):
    open_palette(ctl, now)
    assert use_tile(ctl, action, now + 1) == [] and ctl.armed == action


def test_scroll_zones_scroll_the_window_under_the_spot(ctl):
    arm(ctl, "scroll", 1.0)
    look(ctl, 320, 310, 3.0)
    assert ctl.on_gesture("CLENCH", 3.0, 3.0) == [] and ctl.mode == "scroll"
    up = ctl.scroll.up.center
    assert ctl.tick(3.05, up, True) == []  # not at once
    assert ctl.tick(3.001 + SCROLL_EVERY_S, up, True) == [Scroll(330, 315, WHEEL_NOTCH)]
    assert ctl.tick(3.2, up, True) == []  # one notch per 150 ms
    assert ctl.tick(3.5, ctl.scroll.anchor, True) == []  # the still band between the zones
    down = ctl.scroll.down.center
    assert ctl.tick(3.7, down, True) == [Scroll(330, 315, -WHEEL_NOTCH)]
    past = (down[0], ctl.scroll.down.bottom + 20)  # past the zone: twice as fast
    assert ctl.tick(3.7 + SCROLL_EVERY_S / 2, past, True) == [Scroll(330, 315, -WHEEL_NOTCH)]
    assert ctl.tick(4.5, up, False) == []  # eyes lost: no scrolling
    ctl.tick(5.0, (320, 310), True)
    assert ctl.on_gesture("CLENCH", 5.0, 5.0) == [] and ctl.mode == "pointing"
    assert ctl.armed == "left"


def test_the_scroll_control_fits_on_the_screen_near_an_edge(ctl):
    from desktop.agent.interaction import scroll_control

    c = scroll_control(10, 5, SCREEN, PX_MM)
    assert SCREEN.encloses(c.up) or c.up.top >= 0 and c.up.left >= 0
    assert c.up.top >= SCREEN.top and c.down.bottom <= SCREEN.bottom and c.up.left >= SCREEN.left


def test_a_double_blink_stops_scrolling(ctl):
    arm(ctl, "scroll", 1.0)
    look(ctl, 320, 310, 3.0)
    ctl.on_gesture("CLENCH", 3.0, 3.0)
    assert ctl.on_gesture("DOUBLE_BLINK", 4.0, 4.0) == [] and ctl.mode == "pointing"


def start_drag(ctl, at=1.0):
    arm(ctl, "drag", at)
    look(ctl, 320, 310, at + 2)
    assert ctl.on_gesture("CLENCH", at + 2, at + 2) == [Press(330, 315)] and ctl.mode == "drag"


def test_a_drag_follows_the_eyes_and_drops_where_they_are(ctl):
    start_drag(ctl)
    assert ctl.tick(3.5, (800, 600), True) == [MoveTo(800, 600)]
    assert ctl.tick(3.6, (801, 600), True) == []  # a pixel of jitter does not move it
    assert ctl.tick(3.7, (1400, 200), False) == []  # eyes lost: it holds still
    look(ctl, 900, 650, 5.0)
    assert ctl.on_gesture("CLENCH", 5.0, 5.0) == [Release(900, 650)] and ctl.mode == "pointing"


def test_a_double_blink_cancels_the_drag_where_it_started(ctl):
    start_drag(ctl)
    ctl.tick(3.5, (800, 600), True)
    assert ctl.on_gesture("DOUBLE_BLINK", 4.0, 4.0) == [Keys("escape"), Release(330, 315)]


def test_help_or_the_board_never_leaves_the_button_held(ctl):
    start_drag(ctl)
    assert ctl.set_help(True) == [Keys("escape"), Release(330, 315)] and ctl.mode == "pointing"
    ctl.set_help(False)
    start_drag(ctl, at=10.0)
    assert ctl.set_input_target("board") == [Keys("escape"), Release(330, 315)]
