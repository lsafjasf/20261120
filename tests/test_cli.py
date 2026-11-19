from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run_cli(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "dcp_qc", *arguments],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


class CliTests(unittest.TestCase):
    def test_valid_package_json_exit_zero(self) -> None:
        result = run_cli(
            "inspect",
            "fixtures/base_two_reel",
            "--json",
            "--chunk-size",
            "5",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["total_duration_timecode"], "00:00:20.000")
        self.assertEqual(len(payload["timeline"]), 6)
        self.assertEqual(payload["checked_assets"], 6)

    def test_error_package_json_includes_location(self) -> None:
        result = run_cli(
            "inspect", "fixtures/rate_overflow", "--json"
        )
        self.assertEqual(result.returncode, 1, result.stderr)
        payload = json.loads(result.stdout)
        self.assertFalse(payload["ok"])
        error = payload["errors"][0]
        self.assertEqual(error["code"], "PLAYABLE_TIME_OVERFLOW")
        self.assertEqual(error["reel"], "reel-2")
        self.assertTrue(error["asset_id"].endswith("0202"))
        self.assertEqual(error["screening_time"], "00:00:20.000")

    def test_inherited_missing_cli_case(self) -> None:
        result = run_cli(
            "inspect",
            "fixtures/faults/inherited_missing/supplemental",
            "--base",
            "fixtures/faults/inherited_missing/base",
            "--json",
        )
        self.assertEqual(result.returncode, 1, result.stderr)
        payload = json.loads(result.stdout)
        error = payload["errors"][0]
        self.assertEqual(error["code"], "MISSING_REEL")
        self.assertEqual(error["volume"], "VOL2")
        self.assertEqual(error["screening_time"], "00:00:10.000")

    def test_demo_all_builds_every_scenario(self) -> None:
        result = run_cli("demo", "all", "--json")
        self.assertEqual(result.returncode, 1, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(
            [item["scenario"] for item in payload],
            ["valid", "supplemental", "inherited-missing", "rate-overflow"],
        )
        self.assertTrue(payload[0]["report"]["ok"])
        self.assertTrue(payload[1]["report"]["ok"])
        self.assertFalse(payload[2]["report"]["ok"])
        self.assertFalse(payload[3]["report"]["ok"])

    def test_missing_package_is_a_usage_error_without_traceback(self) -> None:
        result = run_cli("inspect", "does-not-exist")
        self.assertEqual(result.returncode, 2)
        self.assertIn("package path does not exist", result.stderr)
        self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()
