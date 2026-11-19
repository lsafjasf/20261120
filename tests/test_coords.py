import math

from agc_demo.coords import (
    Label,
    Viewport,
    click_hits,
    map_label,
    round_trip_error,
)


def test_anchor_scales_and_pans():
    vp = Viewport(scale=1.5, pan_x=10.0, pan_y=-4.0)
    label = Label("mic", (20.0, 30.0), (10, 20, 40, 50))
    mapping = map_label(label, vp)
    assert mapping.screen_anchor == (40.0, 41.0)
    assert mapping.screen_hit_box == (25.0, 26.0, 70.0, 71.0)


def test_click_round_trip_has_zero_offset():
    vp = Viewport(scale=2.25, pan_x=17.0, pan_y=9.0)
    label = Label("speaker", (12.0, 8.0), (0, 0, 24, 16))
    mapping = map_label(label, vp)
    click = mapping.screen_anchor
    assert click_hits(click, mapping)
    assert round_trip_error(label, vp, click) < 1e-12


def test_miss_outside_hit_box():
    vp = Viewport(scale=1.0, pan_x=0.0, pan_y=0.0)
    label = Label("tag", (5.0, 5.0), (0, 0, 10, 10))
    mapping = map_label(label, vp)
    assert not click_hits((20.0, 20.0), mapping)


def test_inverse_transform_math():
    vp = Viewport(scale=0.75, pan_x=3.0, pan_y=-6.0)
    sx, sy = vp.logical_to_screen(11.0, -2.0)
    lx, ly = vp.screen_to_logical(sx, sy)
    assert math.isclose(lx, 11.0, abs_tol=1e-12)
    assert math.isclose(ly, -2.0, abs_tol=1e-12)
