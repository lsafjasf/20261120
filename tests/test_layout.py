import unittest

from vtt_layout.engine import LayoutEngine
from vtt_layout.framerate import (check_frame_rate, collect_timestamp_seconds,
                                  derive_frame_rate)
from vtt_layout.metrics import TextMetrics, wrap_cue
from vtt_layout.parser import parse_webvtt


VTT = """WEBVTT

REGION
id:r1
width:40%
lines:3
regionanchor:0%,100%
viewportanchor:0%,100%
scroll:up

STYLE
::cue(.big) {
  font-size: 30px;
}

a
00:00:00.000 --> 00:00:04.000 region:r1
hello world ticker

b
00:00:01.000 --> 00:00:04.000 region:r1
second line arrives

c
00:00:02.000 --> 00:00:04.000 region:r1
third line arrives

d
00:00:03.000 --> 00:00:04.000 region:r1
fourth line overflows the three-line region

e
00:00:00.500 --> 00:00:03.500 position:80% align:middle size:30% line:0
<c.big>fixed size cue stays inside the viewport</c>

f
00:00:01.000 --> 00:00:02.000
tag test <00:00:01.200>alpha <00:00:01.800>beta
"""


class ParserTests(unittest.TestCase):
    def test_regions_styles_and_cues(self) -> None:
        doc = parse_webvtt(VTT)
        self.assertEqual(set(doc.regions), {"r1"})
        self.assertEqual(doc.regions["r1"].scroll, "up")
        self.assertEqual([rule.selector for rule in doc.styles], ["::cue(.big)"])
        self.assertEqual(len(doc.cues), 6)

    def test_inline_timestamp_offsets(self) -> None:
        doc = parse_webvtt(VTT)
        cue_f = next(cue for cue in doc.cues if cue.id == "f")
        self.assertEqual(cue_f.lines[0].timestamps, [(9, 1.2), (15, 1.8)])


class WrapTests(unittest.TestCase):
    def test_cjk_breaks_between_glyphs(self) -> None:
        doc = parse_webvtt("WEBVTT\n\nx\n00:00:00.000 --> 00:00:01.000\n汉字排版测试")
        metrics = TextMetrics(20.0)
        lines = wrap_cue(doc.cues[0].lines, 45.0, metrics, 20.0)
        self.assertGreater(len(lines), 1)
        self.assertTrue(all(line.width_px <= 45.0 + 1e-9 for line in lines))

    def test_timestamp_attached_to_following_break(self) -> None:
        doc = parse_webvtt(VTT)
        cue_f = next(cue for cue in doc.cues if cue.id == "f")
        metrics = TextMetrics(20.0)
        lines = wrap_cue(cue_f.lines, 90.0, metrics, 20.0)
        stamped = [line for line in lines if line.timestamp is not None]
        self.assertEqual([line.timestamp for line in stamped], [1.2, 1.8])


class EngineTests(unittest.TestCase):
    def test_region_clips_overflow_and_keeps_up_direction(self) -> None:
        doc = parse_webvtt(VTT)
        engine = LayoutEngine(doc, 640, 360)
        snapshots = engine.simulate([3.5])
        snapshot = snapshots[0]
        region_layout = snapshot.region_layouts[0]
        self.assertTrue(region_layout.inside_viewport)
        clip_events = snapshot.region_clip_events
        self.assertTrue(clip_events)
        self.assertTrue(all("clipped above top" in event for event in clip_events))
        region_cues = [c for c in snapshot.cue_layouts if c.region_id == "r1"]
        self.assertTrue(all(c.scroll_direction == "up" for c in region_cues))

    def test_everything_stays_inside_viewport(self) -> None:
        doc = parse_webvtt(VTT)
        engine = LayoutEngine(doc, 640, 360)
        snapshot = engine.simulate([2.0, 3.5])[-1]
        for layout in snapshot.cue_layouts:
            box = layout.box
            self.assertGreaterEqual(box.x, 0)
            self.assertGreaterEqual(box.y, 0)
            self.assertLessEqual(box.right, 640)
            self.assertLessEqual(box.bottom, 360)

    def test_fixed_px_font_size_from_style(self) -> None:
        doc = parse_webvtt(VTT)
        engine = LayoutEngine(doc, 640, 360)
        cue_e = next(cue for cue in doc.cues if cue.id == "e")
        font_px, _ = engine.cue_font_px(cue_e)
        self.assertEqual(font_px, 30.0)


class FrameRateTests(unittest.TestCase):
    HTML_MISMATCH = """
    <script type="text/vtt" data-fps="25">
WEBVTT
a
00:00:00.000 --> 00:00:02.500
x
b
00:00:03.000 --> 00:00:05.500
y
    </script>"""

    HTML_MATCH = HTML_MISMATCH.replace('data-fps="25"', 'data-fps="24"')

    def test_declared_vs_derived_mismatch(self) -> None:
        verdict = check_frame_rate(self.HTML_MISMATCH)
        self.assertEqual(verdict.declared_fps, 25.0)
        self.assertEqual(verdict.derived_fps, 24.0)
        self.assertFalse(verdict.consistent)

    def test_declared_vs_derived_match(self) -> None:
        verdict = check_frame_rate(self.HTML_MATCH)
        self.assertEqual(verdict.declared_fps, 24.0)
        self.assertEqual(verdict.derived_fps, 24.0)
        self.assertTrue(verdict.consistent)

    def test_grid_fit(self) -> None:
        seconds = collect_timestamp_seconds(self.HTML_MATCH)
        rate = derive_frame_rate(seconds)
        for value in seconds:
            self.assertAlmostEqual(round(value * rate) / rate, value)


if __name__ == "__main__":
    unittest.main()
