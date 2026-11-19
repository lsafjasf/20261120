import unittest

from subtitle_convert.model import Color
from subtitle_convert.timefmt import (
    parse_srt_time, parse_vtt_time, parse_ass_time,
    format_srt_time, format_vtt_time, format_ass_time,
)


class TimeFormatTests(unittest.TestCase):
    def test_srt_roundtrip(self):
        for ms in (0, 999, 3_661_500, 1234):
            self.assertEqual(parse_srt_time(format_srt_time(ms)), ms)

    def test_vtt_optional_hours(self):
        self.assertEqual(parse_vtt_time("00:01.500"), 1500)
        self.assertEqual(parse_vtt_time("01:00:01.500"), 3_601_500)
        for ms in (0, 250, 7_200_001):
            self.assertEqual(parse_vtt_time(format_vtt_time(ms)), ms)

    def test_ass_centisecond_grid(self):
        self.assertEqual(parse_ass_time("1:00:01.23"), 3_601_230)
        self.assertEqual(format_ass_time(1234), "0:00:01.23")
        self.assertEqual(format_ass_time(1235), "0:00:01.24")

    def test_comma_and_dot(self):
        self.assertEqual(parse_srt_time("00:00:01,500"), 1500)

    def test_ass_color_bgr_and_alpha(self):
        color = Color.from_ass("&H0000E1FF&")
        self.assertEqual((color.r, color.g, color.b), (255, 225, 0))
        self.assertEqual(color.alpha, 0)
        translucent = Color.from_ass("&H80000000&")
        self.assertEqual((translucent.r, translucent.g, translucent.b),
                         (0, 0, 0))
        self.assertEqual(translucent.alpha, 128)
        self.assertIn("0.498", translucent.css_rgba())
        self.assertEqual(Color.from_ass(translucent.ass()).alpha, 128)


if __name__ == "__main__":
    unittest.main()
