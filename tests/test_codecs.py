import unittest

from subtitle_convert import srt, vtt, ass, convert
from subtitle_convert.model import Color


SRT_TEXT = """1
00:00:01,000 --> 00:00:03,500
<b>Hello</b> <font color="#FF0000">red</font>

2
00:00:04,000 --> 00:00:06,250
line one
line two
"""


class CodecTests(unittest.TestCase):
    def test_srt_parse(self):
        doc = srt.parse(SRT_TEXT)
        self.assertEqual(len(doc.cues), 2)
        self.assertEqual(doc.cues[0].start, 1000)
        self.assertEqual(doc.cues[0].end, 3500)
        self.assertIn("<b>Hello</b>", doc.cues[0].text)
        self.assertIn("<c #FF0000>red</c>", doc.cues[0].text)
        self.assertIn("\n", doc.cues[1].text)

    def test_srt_identity_roundtrip(self):
        doc = srt.parse(SRT_TEXT)
        again = srt.parse(srt.dump(doc))
        self.assertEqual([c.text for c in doc.cues],
                         [c.text for c in again.cues])
        self.assertEqual([(c.start, c.end) for c in doc.cues],
                         [(c.start, c.end) for c in again.cues])

    def test_vtt_class_color_roundtrip(self):
        doc = vtt.parse("WEBVTT\n\n00:00:01.000 --> 00:00:02.000\n"
                        "<c.FF3030>red text</c>\n")
        self.assertIn("<c #FF3030>", doc.cues[0].text)
        text = vtt.dump(doc)
        self.assertIn("<c.FF3030>", text)

    def test_vtt_style_block(self):
        sample = ("WEBVTT\n\nSTYLE\n::cue(.Nar) { color: #00FF00; "
                  "font-style: italic; font-size: 28px }\n\n"
                  "00:00:01.000 --> 00:00:02.000\nhi\n")
        doc = vtt.parse(sample)
        self.assertEqual(doc.styles["Nar"].primary_color,
                         Color(0, 255, 0))
        self.assertTrue(doc.styles["Nar"].italic)

    def test_ass_color_order_and_geometry(self):
        sample = (
            "[Script Info]\nScriptType: v4.00+\n\n[V4+ Styles]\n"
            "Format: Name, Fontname, Fontsize, PrimaryColour, "
            "SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
            "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
            "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, "
            "MarginV, Encoding\n"
            "Style: Default,Arial,20,&H0000FF&,&H000000FF,&H000000&,"
            "&H000000&,0,0,0,0,100,100,0,0,1,2,2,2,10,10,10,1\n\n"
            "[Events]\n"
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, "
            "MarginV, Effect, Text\n"
            "Dialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,"
            "{\\pos(100,200)\\frz(10)}{\\c&H0000FF&}red{\\c} x\n")
        doc = ass.parse(sample)
        cue = doc.cues[0]
        self.assertEqual(cue.pos, (100.0, 200.0))
        self.assertEqual(cue.rotation, 10)
        self.assertIn("<c #FF0000>red</c>", cue.text)
        names = {loss.name for loss in doc.losses}
        self.assertIn("ass-pos", names)
        self.assertIn("ass-frz", names)

    def test_ass_identity_roundtrip_keeps_geometry(self):
        sample = open("samples/rich.ass", encoding="utf-8").read()
        doc = ass.parse(sample)
        again = ass.parse(ass.dump(doc))
        self.assertEqual(len(again.cues), len(doc.cues))
        self.assertEqual(again.cues[1].pos, doc.cues[1].pos)
        self.assertEqual(again.cues[2].rotation, doc.cues[2].rotation)
        self.assertEqual([c.text for c in doc.cues],
                         [c.text for c in again.cues])

    def test_ass_typography_fields_survive_identity(self):
        sample = open("samples/rich.ass", encoding="utf-8").read()
        doc = ass.parse(sample)
        again = ass.parse(ass.dump(doc))
        before, after = doc.styles["Caption"], again.styles["Caption"]
        self.assertEqual((after.scale_x, after.scale_y, after.spacing),
                         (before.scale_x, before.scale_y, before.spacing))
        self.assertTrue((110, 95, 3) ==
                        (after.scale_x, after.scale_y, after.spacing))
        self.assertTrue(after.strike_out)

    def test_detect_format(self):
        self.assertEqual(convert.detect_format("WEBVTT\n"), "vtt")
        self.assertEqual(convert.detect_format("[Script Info]\n"), "ass")
        self.assertEqual(convert.detect_format("1\n00:00:01,000 -->"), "srt")


if __name__ == "__main__":
    unittest.main()
