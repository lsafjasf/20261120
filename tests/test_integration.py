from __future__ import annotations

import unittest
import tempfile
from pathlib import Path

from aspect_audit.compare import compare_all
from aspect_audit.detect import analyze_video
from aspect_audit.generate import ClipSpec, encode_clip


ROOT = Path(__file__).resolve().parents[1]
MATERIALS = ROOT / "artifacts" / "materials"


@unittest.skipUnless(
    (MATERIALS / "long_master.mp4").exists(),
    "generated demo materials are not present",
)
class DemoIntegrationTests(unittest.TestCase):
    def audit(self, name: str) -> tuple[dict, dict]:
        analysis = analyze_video(str(MATERIALS / f"{name}_master.mp4"))
        comparison = compare_all(
            analysis,
            schedule_path=str(MATERIALS / f"{name}_declared.csv"),
            truth_path=str(MATERIALS / f"{name}_truth.json"),
        )
        return analysis, comparison

    def test_long_master(self) -> None:
        analysis, comparison = self.audit("long")
        self.assertEqual(
            [frame.layout for frame in analysis["segments"]],
            ["full", "letterbox"],
        )
        self.assertEqual(analysis["switches"][0]["frame"], 72)
        truth = comparison["truth_validation"]
        self.assertEqual(truth["switch_checks"][0]["error_frames"], 0)
        self.assertEqual(truth["false_positive_intervals"], [])
        black = analysis["events"]["black"]
        self.assertTrue(
            any(
                event.start_frame == 60 and event.end_frame == 71
                for event in black
            )
        )
        subtitle = analysis["events"]["subtitle"]
        self.assertEqual(
            (subtitle[0].start_frame, subtitle[0].end_frame), (204, 239)
        )
        self.assertEqual(len(comparison["mismatches"]), 2)

    def test_short_master(self) -> None:
        analysis, comparison = self.audit("short")
        self.assertEqual(
            [frame.layout for frame in analysis["segments"]],
            ["pillarbox", "letterbox"],
        )
        self.assertEqual(analysis["switches"][0]["frame"], 66)
        truth = comparison["truth_validation"]
        self.assertEqual(truth["switch_checks"][0]["error_frames"], 0)
        self.assertEqual(truth["false_positive_intervals"], [])
        self.assertEqual(
            (
                analysis["events"]["subtitle"][0].start_frame,
                analysis["events"]["subtitle"][0].end_frame,
            ),
            (126, 150),
        )
        self.assertEqual(len(comparison["mismatches"]), 1)

    def test_four_side_black_video(self) -> None:
        spec = ClipSpec(
            name="four",
            duration=0.75,
            fade_frames=0,
            initial_layout="fourside",
            switch_frame=999,
            black_start=999,
            black_end=999,
            subtitle_start=999,
            subtitle_end=999,
            crf=24,
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "four_side.mp4"
            encode_clip(spec, path, width=640, height=360)
            analysis = analyze_video(str(path))
        self.assertEqual(
            [segment.layout for segment in analysis["segments"]],
            ["fourside"],
        )
        self.assertTrue(
            all(
                value > 0
                for value in analysis["frames"][6].bars.values()
            )
        )


if __name__ == "__main__":
    unittest.main()
