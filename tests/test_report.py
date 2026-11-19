from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from dash_events.report import build_report, write_report

ROOT = Path(__file__).resolve().parents[1]


class ReportTests(unittest.TestCase):
    def test_report_validates_boundary_inheritance_and_stale_events(self) -> None:
        report = build_report(ROOT / "fixtures/two_period.mpd")
        checks = report["checks"]
        self.assertTrue(all(checks.values()), checks)
        payload = report["payload"]
        self.assertEqual(len(payload["event_inheritance"]), 5)
        self.assertEqual(len(payload["noise"]), 4)
        self.assertTrue(all(row["accepted"] for row in payload["noise"]))
        self.assertEqual(payload["event_checks"]["boundary_event_ids"], [1003])
        self.assertEqual(payload["event_checks"]["cross_period_event_ids"], [1002])
        self.assertEqual(payload["event_checks"]["stale_event_ids"], [1004])

        with tempfile.TemporaryDirectory() as directory:
            paths = write_report(directory, report)
            self.assertTrue(paths["markdown"].is_file())
            self.assertTrue(paths["json"].is_file())
            self.assertTrue(paths["csv"].is_file())
            saved = json.loads(paths["json"].read_text(encoding="utf-8"))
            self.assertEqual(saved["event_checks"]["cross_period_event_ids"], [1002])


if __name__ == "__main__":
    unittest.main()
