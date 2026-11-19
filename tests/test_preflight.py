from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from fractions import Fraction
from pathlib import Path

from stereo_preflight.analysis import AnalysisOptions, analyze_video
from stereo_preflight.export import ExportError, export_corrected, validate_corrections
from stereo_preflight.fixtures import TestPattern, write_test_video
from stereo_preflight.media import probe_video
from stereo_preflight.preview import render_preview


class PreflightCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp = Path(tempfile.mkdtemp(prefix="stereo-preflight-test-"))
        base = dict(
            eye_width=480,
            eye_height=360,
            frames=12,
            frame_rate=25,
            foreground_disparity=8,
            background_disparity=-4,
        )
        cls.paths = {}
        patterns = {
            "normal": TestPattern(**base),
            "swapped": TestPattern(**base, swap_eyes=True),
            "dropped": TestPattern(**base, drop_second_at=4),
            "offset": TestPattern(**base, second_frame_shift=2),
            "parallax": TestPattern(**{**base, "foreground_disparity": 32}),
            "vertical": TestPattern(**base, vertical_offset=6),
            "ou": TestPattern(**base, packing="ou"),
        }
        for name, pattern in patterns.items():
            path = cls.temp / f"{name}.mp4"
            write_test_video(path, pattern)
            cls.paths[name] = path
        cls.reports = {
            name: analyze_video(path, AnalysisOptions(packing="ou" if name == "ou" else "sbs"))
            for name, path in cls.paths.items()
        }
        cls.plain = cls.temp / "unmarked.mp4"
        subprocess.run(
            [
                "ffmpeg", "-v", "error", "-y", "-f", "lavfi",
                "-i", "color=c=gray:s=960x360:d=0.4:r=25",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", str(cls.plain),
            ],
            check=True,
        )
        cls.reports["unmarked"] = analyze_video(cls.plain, AnalysisOptions(packing="sbs"))

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.temp, ignore_errors=True)

    def anomaly_types(self, name: str) -> list[str]:
        return [item["type"] for item in self.reports[name]["anomalies"]]

    def test_normal_clip_passes_and_measures_known_disparity(self) -> None:
        report = self.reports["normal"]
        self.assertEqual(report["summary"]["status"], "pass")
        self.assertEqual(report["eye_identity"]["detected_order"], "ab")
        self.assertEqual(report["anomalies"], [])
        first = report["frames"][0]
        self.assertAlmostEqual(first["foreground_dx"], 8.0, delta=1.0)
        self.assertAlmostEqual(first["background_dx"], -4.0, delta=1.0)
        self.assertAlmostEqual(first["foreground_dy"], 0.0, delta=0.75)
        self.assertEqual(first["first"]["source_frame"], first["second"]["source_frame"])

    def test_swapped_eyes_are_reported_for_full_interval(self) -> None:
        report = self.reports["swapped"]
        self.assertEqual(report["summary"]["status"], "fail")
        swapped = next(item for item in report["anomalies"] if item["type"] == "eye_order_swapped")
        self.assertEqual((swapped["start_frame"], swapped["end_frame"]), (0, 11))
        self.assertEqual(swapped["frame_count"], 12)
        self.assertEqual(report["eye_identity"]["detected_order"], "ba")

    def test_dropped_frame_is_a_gap_not_a_global_offset(self) -> None:
        report = self.reports["dropped"]
        self.assertEqual(report["summary"]["status"], "fail")
        gap = next(item for item in report["anomalies"] if item["type"] == "timeline_gap")
        self.assertEqual(gap["eye"], "second")
        self.assertEqual(gap["start_frame"], 4)
        self.assertEqual(gap["details"]["missing_source_frames"], [4])
        mismatch = next(item for item in report["anomalies"] if item["type"] == "frame_pair_mismatch")
        self.assertEqual((mismatch["start_frame"], mismatch["end_frame"]), (4, 11))
        for offset_left in range(-3, 4):
            for offset_right in range(-3, 4):
                checked = validate_corrections(
                    report,
                    order="auto",
                    offset_left=offset_left,
                    offset_right=offset_right,
                )
                self.assertTrue(checked["errors"], (offset_left, offset_right))

    def test_integer_frame_offset_can_repair_constant_pair_shift(self) -> None:
        report = self.reports["offset"]
        checked = validate_corrections(report, order="auto")
        self.assertFalse(checked["ok"])
        self.assertEqual(checked["errors"][0]["type"], "frame_pair_unresolved")
        repaired = validate_corrections(report, order="auto", offset_left=-2)
        self.assertTrue(repaired["ok"], repaired)
        destination = self.temp / "offset_repaired.mp4"
        export_corrected(report, destination, order="auto", offset_left=-2)
        output_report = analyze_video(destination, AnalysisOptions(packing="sbs"))
        self.assertNotEqual(output_report["summary"]["status"], "fail")
        self.assertNotIn("frame_pair_mismatch", [item["type"] for item in output_report["anomalies"]])
        self.assertEqual(output_report["frames"][0]["first"]["source_frame"], 2)
        self.assertEqual(output_report["frames"][0]["second"]["source_frame"], 2)

    def test_parallax_and_vertical_limits_fail(self) -> None:
        parallax = self.reports["parallax"]
        self.assertEqual(parallax["summary"]["status"], "fail")
        self.assertIn("parallax_exceeded", self.anomaly_types("parallax"))
        vertical = self.reports["vertical"]
        self.assertEqual(vertical["summary"]["status"], "fail")
        self.assertIn("vertical_offset", self.anomaly_types("vertical"))

    def test_over_under_layout(self) -> None:
        report = self.reports["ou"]
        self.assertEqual(report["media"]["packing"], "ou")
        self.assertEqual(report["summary"]["status"], "pass")
        self.assertEqual((report["media"]["width"], report["media"]["height"]), (480, 720))
        self.assertEqual(report["eye_identity"]["detected_order"], "ab")

    def test_preview_modes_and_marks(self) -> None:
        report = self.reports["normal"]
        info = probe_video(self.paths["normal"])
        for mode in ("left", "right", "anaglyph"):
            image, metadata = render_preview(report, info, 2, mode=mode)
            self.assertTrue(image.startswith(b"\x89PNG\r\n\x1a\n"))
            self.assertEqual((metadata["width"], metadata["height"]), (480, 360))
            self.assertGreaterEqual(len(metadata["marks"]), 2)
        image, metadata = render_preview(report, info, 2, mode="marked")
        self.assertTrue(image.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertEqual(metadata["width"], 480 * 2 + 8)
        self.assertEqual(metadata["height"], 360)

    def test_export_keeps_source_rate_and_frame_count(self) -> None:
        normal = self.reports["normal"]
        destination = self.temp / "normal_export.mp4"
        result = export_corrected(normal, destination)
        self.assertEqual(result.frame_rate, "25/1")
        info = probe_video(destination)
        self.assertEqual(info.frame_rate, Fraction(25, 1))
        self.assertEqual(info.frame_count, 12)
        self.assertEqual((info.width, info.height), (960, 360))

    def test_fractional_source_rate_is_preserved(self) -> None:
        source = self.temp / "fractional.mp4"
        write_test_video(
            source,
            TestPattern(
                eye_width=480,
                eye_height=360,
                frames=12,
                frame_rate=Fraction(30000, 1001),
                foreground_disparity=8,
                background_disparity=-4,
            ),
        )
        report = analyze_video(source, AnalysisOptions(packing="sbs"))
        self.assertEqual(report["media"]["frame_rate"], "30000/1001")
        destination = self.temp / "fractional_export.mp4"
        export_corrected(report, destination)
        self.assertEqual(probe_video(destination).frame_rate_text, "30000/1001")

    def test_eye_order_repair_exports_a_clean_clip(self) -> None:
        swapped = self.reports["swapped"]
        destination = self.temp / "swapped_repaired.mp4"
        result = export_corrected(swapped, destination, order="auto")
        self.assertEqual(result.order, "ba")
        repaired = analyze_video(destination, AnalysisOptions(packing="sbs"))
        self.assertEqual(repaired["summary"]["status"], "pass")
        self.assertEqual(repaired["eye_identity"]["detected_order"], "ab")
        self.assertEqual(repaired["frames"][0]["first"]["label"], "L")
        self.assertEqual(repaired["frames"][0]["second"]["label"], "R")

    def test_unresolved_gap_export_is_blocked(self) -> None:
        dropped = self.reports["dropped"]
        destination = self.temp / "should_not_exist.mp4"
        with self.assertRaises(ExportError):
            export_corrected(dropped, destination, order="auto")
        self.assertFalse(destination.exists())

    def test_missing_eye_markers_require_manual_review(self) -> None:
        report = self.reports["unmarked"]
        self.assertEqual(report["summary"]["status"], "warning")
        self.assertTrue(report["eye_identity"]["manual_review_required"])
        self.assertFalse(report["eye_identity"]["confirmed"])
        self.assertIn("eye_identity_unconfirmed", self.anomaly_types("unmarked"))
        blocked = validate_corrections(report, order="auto")
        self.assertEqual(blocked["errors"][0]["type"], "eye_order_required")
        manual = validate_corrections(report, order="ab")
        self.assertTrue(manual["ok"])

    def test_export_can_convert_packaging(self) -> None:
        normal = self.reports["normal"]
        destination = self.temp / "normal_ou.mp4"
        export_corrected(normal, destination, packaging="ou")
        info = probe_video(destination)
        self.assertEqual((info.width, info.height), (480, 720))
        output_report = analyze_video(destination, AnalysisOptions(packing="ou"))
        self.assertEqual(output_report["summary"]["status"], "pass")


class ReportShapeCase(unittest.TestCase):
    def test_static_ui_references_existing_ids(self) -> None:
        root = Path(__file__).resolve().parents[1]
        html = (root / "stereo_preflight" / "static" / "index.html").read_text(encoding="utf-8")
        script = (root / "stereo_preflight" / "static" / "app.js").read_text(encoding="utf-8")
        import re

        html_ids = set(re.findall(r'\bid="([^"]+)"', html))
        used_ids = set(re.findall(r'\$\("#([A-Za-z0-9_-]+)"\)', script))
        self.assertEqual(used_ids - html_ids, set())

    def test_json_report_shape_is_serializable(self) -> None:
        report = {
            "schema_version": 1,
            "summary": {"status": "pass"},
            "anomalies": [],
            "frames": [],
        }
        self.assertIn("status", json.loads(json.dumps(report))["summary"])


if __name__ == "__main__":
    unittest.main()
