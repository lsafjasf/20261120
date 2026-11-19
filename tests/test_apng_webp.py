from __future__ import annotations

import tempfile
import unittest
import zlib
from fractions import Fraction
from pathlib import Path

from apng_webp.apng import read_apng
from apng_webp.fixtures import build_builtin_fixtures
from apng_webp.model import APNGError, FrameControl
from apng_webp.pipeline import (
    _frame_difference,
    convert_apng,
    preflight_apng,
    verify_files,
    verify_webp,
)
from apng_webp.png import PNGHeader, PNGInfo, decode_png_frame
from apng_webp.timing import plan_delays
from apng_webp.webp import encode_animation, parse_webp_container


class FixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        cls.fixture_dir = cls.root / "fixtures"
        cls.paths = {
            path.stem: path for path in build_builtin_fixtures(cls.fixture_dir)
        }

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp.cleanup()

    def pixel(self, pixels: bytes, width: int, x: int, y: int) -> bytes:
        start = (y * width + x) * 4
        return pixels[start : start + 4]

    def test_builtin_fixture_metadata(self) -> None:
        expected = {
            "offset_subframe": (48, 32, 4, 3),
            "alpha_overlay": (40, 40, 4, 0),
            "restore_previous": (32, 32, 3, 2),
            "fractional_delay": (16, 16, 6, 1),
            "zero_delay": (16, 16, 3, 0),
            "stress_mixed": (24, 20, 6, 5),
        }
        for name, values in expected.items():
            with self.subTest(name=name):
                animation = read_apng(self.paths[name])
                self.assertEqual(
                    (
                        animation.width,
                        animation.height,
                        len(animation.frames),
                        animation.num_plays,
                    ),
                    values,
                )
                self.assertEqual(len(animation.rendered), values[2])

    def test_offset_source_and_background_disposal(self) -> None:
        animation = read_apng(self.paths["offset_subframe"])
        first = animation.rendered[0]
        second = animation.rendered[1]
        fourth = animation.rendered[3]
        self.assertEqual(
            self.pixel(second, animation.width, 20, 6),
            bytes((240, 190, 40, 220)),
        )
        self.assertEqual(
            self.pixel(fourth, animation.width, 34, 18),
            bytes((0, 0, 0, 0)),
        )
        self.assertEqual(
            self.pixel(fourth, animation.width, 0, 0),
            self.pixel(first, animation.width, 0, 0),
        )

    def test_alpha_overlay_composites_visible_pixels(self) -> None:
        animation = read_apng(self.paths["alpha_overlay"])
        third = animation.rendered[2]
        self.assertEqual(self.pixel(third, animation.width, 15, 15)[3], 255)
        self.assertGreater(
            self.pixel(third, animation.width, 20, 20)[3],
            self.pixel(animation.rendered[1], animation.width, 20, 20)[3],
        )

    def test_restore_previous_canvas(self) -> None:
        animation = read_apng(self.paths["restore_previous"])
        first = animation.rendered[0]
        second = animation.rendered[1]
        third = animation.rendered[2]
        self.assertEqual(
            self.pixel(second, animation.width, 12, 12),
            bytes((240, 40, 60, 255)),
        )
        self.assertEqual(
            self.pixel(third, animation.width, 12, 12),
            self.pixel(first, animation.width, 12, 12),
        )

    def test_fractional_delay_uses_cumulative_rounding(self) -> None:
        animation = read_apng(self.paths["fractional_delay"])
        timing = plan_delays(frame.control for frame in animation.frames)
        self.assertEqual(
            [frame.duration_ms for frame in timing.frames],
            [167, 166, 167, 167, 166, 167],
        )
        self.assertEqual(
            [frame.cumulative_ms for frame in timing.frames],
            [167, 333, 500, 667, 833, 1000],
        )
        self.assertEqual(timing.total_ms, 1000)
        self.assertEqual(timing.diagnostics, [])

    def test_submillisecond_frame_is_reported_and_kept(self) -> None:
        animation = read_apng(self.paths["zero_delay"])
        timing = plan_delays(frame.control for frame in animation.frames)
        self.assertEqual([frame.duration_ms for frame in timing.frames], [100, 1, 99])
        self.assertEqual(len(timing.diagnostics), 1)
        self.assertEqual(timing.diagnostics[0]["frame_number"], 2)
        self.assertEqual(timing.diagnostics[0]["exact_delay"], "1/10")

    def test_zero_delay_is_not_silently_swallowed(self) -> None:
        output = self.root / "zero_delay.webp"
        result = convert_apng(self.paths["zero_delay"], output)
        self.assertTrue(result.ok, result.first_error)
        self.assertEqual(result.webp_frame_count, 3)
        self.assertEqual(result.webp_durations_ms, [100, 1, 99])
        self.assertEqual(result.timing.diagnostics[0]["frame_number"], 2)
        self.assertTrue(parse_webp_container(output.read_bytes()).all_lossless)

    def test_strict_timing_aborts_before_writing(self) -> None:
        output = self.root / "strict.webp"
        with self.assertRaisesRegex(APNGError, "frame\\(s\\) 2"):
            convert_apng(
                self.paths["zero_delay"], output, strict_timing=True
            )
        self.assertFalse(output.exists())

    def test_all_builtin_animations_round_trip(self) -> None:
        for name, path in self.paths.items():
            with self.subTest(name=name):
                output = self.root / f"{name}.webp"
                result = convert_apng(path, output)
                self.assertTrue(result.ok, result.first_error)
                container = parse_webp_container(output.read_bytes())
                self.assertTrue(container.all_lossless)
                source = read_apng(path)
                self.assertEqual(container.loop_count, source.num_plays)
                again = verify_files(path, output)
                self.assertTrue(again.ok, again.first_error)


class TimingAndDiffTests(unittest.TestCase):
    def test_delay_denominator_zero_means_one_hundred(self) -> None:
        control = FrameControl(0, 1, 1, 0, 0, 1, 0, 0, 0)
        self.assertEqual(control.delay, Fraction(1, 100))

    def test_zero_length_slot_gets_diagnostic(self) -> None:
        controls = [
            FrameControl(0, 1, 1, 0, 0, 1, 2000, 0, 0),
            FrameControl(1, 1, 1, 0, 0, 1, 2000, 0, 0),
        ]
        timing = plan_delays(controls)
        self.assertEqual([item.duration_ms for item in timing.frames], [1, 1])
        self.assertEqual(timing.diagnostics[0]["frame_number"], 2)

    def test_diff_ignores_rgb_under_full_transparency(self) -> None:
        expected = bytes((1, 2, 3, 0))
        actual = bytes((9, 8, 7, 0))
        self.assertTrue(_frame_difference(expected, actual, 1, 1).empty)
        changed_alpha = bytes((9, 8, 7, 1))
        difference = _frame_difference(expected, changed_alpha, 1, 1)
        self.assertEqual(difference.pixels, 1)
        self.assertEqual((difference.x, difference.y), (0, 0))

    def test_verification_reports_first_frame_and_bbox(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = build_builtin_fixtures(root)[1]
            animation = read_apng(source)
            timing = plan_delays(frame.control for frame in animation.frames)
            frames = [bytes(item) for item in animation.rendered]
            index = (15 * animation.width + 15) * 4
            frames[2] = (
                frames[2][:index]
                + bytes((255, 0, 0, 255))
                + frames[2][index + 4 :]
            )
            webp = encode_animation(
                frames,
                animation.width,
                animation.height,
                [item.duration_ms for item in timing.frames],
                animation.num_plays,
            )
            result = verify_webp(animation, timing, webp)
            self.assertFalse(result.ok)
            self.assertEqual(result.first_error["frame_number"], 3)
            self.assertEqual(
                result.first_error["bbox"],
                {"x": 15, "y": 15, "width": 1, "height": 1},
            )

    def test_preflight_exposes_regions_and_delays(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = build_builtin_fixtures(temp)[0]
            report = preflight_apng(source)
            self.assertEqual(report["loop_count"], 3)
            self.assertEqual(report["frames"][1]["region"]["x"], 20)
            self.assertEqual(report["frames"][1]["region"]["y"], 6)
            self.assertEqual(report["frames"][1]["duration_ms"], 200)

    def test_adam7_png_frame_decoding(self) -> None:
        width = height = 8
        pixels = bytearray()
        for y in range(height):
            for x in range(width):
                pixels.extend((x * 30, y * 30, (x + y) * 10, 255))
        raw = bytearray()
        passes = (
            (0, 0, 8, 8),
            (4, 0, 8, 8),
            (0, 4, 4, 8),
            (2, 0, 4, 4),
            (0, 2, 2, 4),
            (1, 0, 2, 2),
            (0, 1, 1, 2),
        )
        for start_x, start_y, step_x, step_y in passes:
            pass_width = (
                (width - start_x + step_x - 1) // step_x
                if start_x < width
                else 0
            )
            pass_height = (
                (height - start_y + step_y - 1) // step_y
                if start_y < height
                else 0
            )
            for pass_y in range(pass_height):
                raw.append(0)
                for pass_x in range(pass_width):
                    x = start_x + pass_x * step_x
                    y = start_y + pass_y * step_y
                    start = (y * width + x) * 4
                    raw.extend(pixels[start : start + 4])
        info = PNGInfo(PNGHeader(width, height, 8, 6, 1), None, None)
        decoded = decode_png_frame(
            zlib.compress(bytes(raw)), width, height, info
        )
        self.assertEqual(decoded, bytes(pixels))


if __name__ == "__main__":
    unittest.main()
