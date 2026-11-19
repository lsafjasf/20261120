from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

from aspect_audit.compare import (
    ScheduleEntry,
    compare_schedule,
    load_schedule,
    parse_aspect,
)
from aspect_audit.config import AuditConfig
from aspect_audit.models import FrameResult, Rect


def frame_result(index: int, layout: str, aspect: float) -> FrameResult:
    rect = Rect(0, 0, round(1000 * aspect), 1000)
    return FrameResult(
        frame=index,
        time=index / 24,
        layout=layout,
        rect=rect,
        aspect=aspect,
        confidence=0.9,
        brightness=100,
        scene_brightness=100,
        dark_ratio=0.2,
        bars={"top": 0, "bottom": 0, "left": 0, "right": 0},
    )


class CompareTests(unittest.TestCase):
    def test_parse_aspect(self) -> None:
        self.assertAlmostEqual(parse_aspect("2.39:1"), 2.39)
        self.assertAlmostEqual(parse_aspect("16:9"), 16 / 9)
        self.assertAlmostEqual(parse_aspect(1.85), 1.85)

    def test_load_json_schedule(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "schedule.json"
            path.write_text(
                json.dumps(
                    {"entries": [
                        {"start_frame": 0, "end_frame": 23, "aspect": "16:9"},
                        {"start_frame": 24, "end_frame": 47, "aspect": "2.39:1"},
                    ]}
                ),
                encoding="utf-8",
            )
            entries, _ = load_schedule(path, 24, 48)
        self.assertEqual(len(entries), 2)
        self.assertAlmostEqual(entries[1].aspect, 2.39)

    def test_load_csv_schedule_in_seconds(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "schedule.csv"
            with path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(
                    handle, fieldnames=["start_sec", "end_sec", "aspect"]
                )
                writer.writeheader()
                writer.writerow(
                    {"start_sec": 0, "end_sec": 1, "aspect": "4:3"}
                )
            entries, _ = load_schedule(path, 24, 24)
        self.assertEqual(entries[0].start_frame, 0)
        self.assertEqual(entries[0].end_frame, 23)

    def test_mismatch_grouping(self) -> None:
        frames = [
            frame_result(index, "full", 16 / 9) for index in range(6)
        ] + [
            frame_result(index, "letterbox", 2.39) for index in range(6, 12)
        ]
        entries = [
            ScheduleEntry(0, 7, 16 / 9, "16:9"),
            ScheduleEntry(8, 11, 2.39, "2.39:1"),
        ]
        mismatches = compare_schedule(frames, entries, AuditConfig())
        self.assertEqual(len(mismatches), 1)
        self.assertEqual(mismatches[0].start_frame, 6)
        self.assertEqual(mismatches[0].end_frame, 7)


if __name__ == "__main__":
    unittest.main()
