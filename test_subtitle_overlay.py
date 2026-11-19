"""Tests for subtitle overlay ordering and priority rules."""
import pytest

from subtitle_overlay import SubtitleEvent, SubtitleOverlay


def make_overlay():
    return SubtitleOverlay(priorities={"dialogue": 10, "system": 100})


def test_dual_channel_simultaneous_activation_order():
    overlay = make_overlay()
    overlay.add_event(SubtitleEvent("dialogue", "d", 1.0, 3.0))
    overlay.add_event(SubtitleEvent("system", "s", 1.0, 3.0))
    order = [e.channel for e in overlay.render_order(2.0)]
    # higher priority (system) must be on top
    assert order == ["dialogue", "system"]
    assert overlay.top_event(2.0).channel == "system"


def test_priority_beats_start_time():
    overlay = make_overlay()
    overlay.add_event(SubtitleEvent("system", "s", 2.0, 4.0))  # starts later
    overlay.add_event(SubtitleEvent("dialogue", "d", 1.0, 4.0))
    order = [e.channel for e in overlay.render_order(3.0)]
    assert order == ["dialogue", "system"]


def test_same_priority_later_start_on_top():
    overlay = SubtitleOverlay(priorities={"a": 10, "b": 10})
    overlay.add_event(SubtitleEvent("a", "first", 1.0, 5.0))
    overlay.add_event(SubtitleEvent("b", "second", 2.0, 5.0))
    order = [e.channel for e in overlay.render_order(3.0)]
    assert order == ["a", "b"]


def test_inactive_events_excluded():
    overlay = make_overlay()
    overlay.add_event(SubtitleEvent("dialogue", "d", 1.0, 2.0))
    assert overlay.render_order(0.5) == []
    assert overlay.render_order(2.0) == []  # end is exclusive
    assert overlay.top_event(0.5) is None


def test_unregistered_channel_rejected():
    overlay = make_overlay()
    with pytest.raises(KeyError):
        overlay.add_event(SubtitleEvent("ghost", "g", 0.0, 1.0))
