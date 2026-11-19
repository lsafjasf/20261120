import unittest

from caf_audio.errors import TimecodeError
from caf_audio.timebase import (
    FILM_24,
    FOUR_TIMEBASES,
    NTSC_30,
    NTSC_30_DROP,
    PAL_25,
    Timecode,
    boundary_values,
    convert_timecode,
    drop_frame_checks,
    format_frame,
    frames_to_timecode,
    parse_timecode,
    timecode_to_frames,
)


class TimeBaseTests(unittest.TestCase):
    def test_four_timebase_day_boundaries(self):
        expected = {
            FILM_24: 2_073_600,
            PAL_25: 2_160_000,
            NTSC_30: 2_592_000,
            NTSC_30_DROP: 2_589_408,
        }
        for base, frames in expected.items():
            self.assertEqual(base.frames_per_day, frames)
            self.assertEqual(
                format_frame(frames - 1, base),
                "23:59:59" + base.separator + (
                    "23" if base is FILM_24 else
                    "24" if base is PAL_25 else "29"
                ),
            )

    def test_drop_frame_first_minute(self):
        self.assertEqual(format_frame(1799, NTSC_30_DROP), "00:00:59;29")
        self.assertEqual(format_frame(1800, NTSC_30_DROP), "00:01:00;02")
        self.assertEqual(
            timecode_to_frames(Timecode(0, 1, 0, 2), NTSC_30_DROP), 1800
        )
        for label in ("00:01:00;00", "00:01:00;01"):
            with self.assertRaisesRegex(TimecodeError, "do not exist"):
                parse_timecode(label, NTSC_30_DROP)

    def test_drop_frame_tenth_minute_and_hour(self):
        self.assertEqual(format_frame(17982, NTSC_30_DROP), "00:10:00;00")
        self.assertEqual(format_frame(107891, NTSC_30_DROP), "00:59:59;29")
        self.assertEqual(format_frame(107892, NTSC_30_DROP), "01:00:00;00")
        self.assertEqual(
            timecode_to_frames(Timecode(0, 10, 0, 0), NTSC_30_DROP), 17982
        )
        self.assertEqual(
            timecode_to_frames(Timecode(1, 0, 0, 0), NTSC_30_DROP), 107892
        )
        checks = drop_frame_checks()
        self.assertEqual(checks["dropped_labels_per_hour"], 108)
        self.assertEqual(checks["frames_per_day"], 2_589_408)

    def test_nonzero_seconds_allow_00_and_01_frames(self):
        self.assertEqual(format_frame(1828, NTSC_30_DROP), "00:01:01;00")
        self.assertEqual(
            timecode_to_frames(Timecode(0, 1, 1, 1), NTSC_30_DROP), 1829
        )

    def test_drop_frame_roundtrip_across_full_day(self):
        for frame in range(NTSC_30_DROP.frames_per_day):
            label = frames_to_timecode(frame, NTSC_30_DROP)
            self.assertEqual(timecode_to_frames(label, NTSC_30_DROP), frame)

    def test_conversion_boundaries_between_four_bases(self):
        one_second = {
            FILM_24: Timecode(0, 0, 1, 0),
            PAL_25: Timecode(0, 0, 1, 0),
            NTSC_30: Timecode(0, 0, 1, 0),
            NTSC_30_DROP: Timecode(0, 0, 1, 0),
        }
        for source in FOUR_TIMEBASES:
            for target in FOUR_TIMEBASES:
                self.assertEqual(
                    convert_timecode(one_second[source], source, target),
                    one_second[target],
                )

        ten_minutes = Timecode(0, 10, 0, 0)
        self.assertEqual(
            convert_timecode(ten_minutes, NTSC_30_DROP, FILM_24),
            ten_minutes,
        )
        hour = Timecode(1, 0, 0, 0)
        self.assertEqual(
            convert_timecode(hour, NTSC_30_DROP, PAL_25),
            hour,
        )

    def test_24_hour_label_is_exact_day_boundary(self):
        for base in FOUR_TIMEBASES:
            self.assertEqual(
                timecode_to_frames(Timecode(24, 0, 0, 0), base),
                base.frames_per_day,
            )
            self.assertEqual(
                convert_timecode(
                    Timecode(24, 0, 0, 0), NTSC_30_DROP, base
                ),
                Timecode(24, 0, 0, 0),
            )
        with self.assertRaisesRegex(TimecodeError, "only valid"):
            timecode_to_frames(Timecode(24, 0, 0, 1), NTSC_30)

    def test_boundary_report_has_four_bases(self):
        rows = boundary_values()
        self.assertEqual(len(rows), 24)
        self.assertEqual({row["timebase"] for row in rows}, {"24", "25", "30", "30-drop"})


if __name__ == "__main__":
    unittest.main()
