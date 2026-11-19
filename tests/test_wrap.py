"""Deterministic wrap and occlusion checks for the subtitle layout engine."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from subtitle.run_resize import (  # noqa: E402
    CANVAS_HEIGHT,
    FONT_PATH,
    FONT_SIZE,
    LINE_GAP,
    REGION_HEIGHT,
    SIDE_MARGIN,
    SUBTITLE,
    make_layout,
)
from subtitle.wrap import wrap_subtitle  # noqa: E402


def test_wide_player_uses_base_region_without_expansion() -> None:
    result = wrap_subtitle(SUBTITLE, make_layout(1280))
    assert not result.region_expanded
    assert result.effective_region_height_px == REGION_HEIGHT
    assert not result.occludes_picture


def test_narrow_player_wraps_more_lines() -> None:
    wide = wrap_subtitle(SUBTITLE, make_layout(1280))
    narrow = wrap_subtitle(SUBTITLE, make_layout(360))
    assert len(narrow.lines) > len(wide.lines)


def test_lines_never_exceed_content_width() -> None:
    for width in (1280, 1024, 800, 640, 480, 360):
        layout = make_layout(width)
        result = wrap_subtitle(SUBTITLE, layout)
        for line in result.lines:
            assert line.width_px <= layout.content_width_px


def test_no_occlusion_across_widths() -> None:
    for width in (1280, 1024, 800, 640, 480, 360):
        result = wrap_subtitle(SUBTITLE, make_layout(width))
        assert result.fits_scroll_region
        assert not result.occludes_picture
        assert result.block_top_px >= result.region_top_px


def test_expanded_region_stays_under_canvas_cap() -> None:
    result = wrap_subtitle(SUBTITLE, make_layout(360))
    max_h = int(CANVAS_HEIGHT * result.layout.max_region_fraction)
    assert result.effective_region_height_px <= max_h
    assert result.region_top_px > 0


def test_font_and_layout_constants() -> None:
    assert os.path.exists(FONT_PATH)
    assert FONT_SIZE > 0 and LINE_GAP >= 0 and SIDE_MARGIN >= 0
    assert REGION_HEIGHT < CANVAS_HEIGHT


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"PASS {name}")
