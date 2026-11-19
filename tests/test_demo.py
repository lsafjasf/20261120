from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from drm_sim.demo import NEW_KID, OLD_KID, run_demo


class DemoIntegrationTests(unittest.TestCase):
    def test_end_to_end_report_and_artifacts(self) -> None:
        with tempfile.TemporaryDirectory(prefix="drm-demo-") as temp_dir:
            output = Path(temp_dir)
            report = run_demo(output)
            self.assertTrue(all(report["checks"].values()), report["checks"])
            self.assertEqual(
                report["parameter_sets"]["nal_types"],
                [32, 33],
            )
            self.assertEqual(
                report["parameter_sets"]["raised_error"]["code"],
                "MISSING_PPS",
            )

            required = [
                "report.json",
                "report.md",
                "license_state_sequence.json",
                "byte_diffs.json",
                "player_probe.json",
                "parameter_sets.json",
                "clearkey_before.mp4",
                "clearkey_after.mp4",
                "playready_before.mp4",
                "playready_after.mp4",
                "clearkey_pssh_before.bin",
                "clearkey_pssh_after.bin",
                "playready_pssh_before.bin",
                "playready_pssh_after.bin",
            ]
            for name in required:
                self.assertTrue((output / name).is_file(), name)

            for provider in ("clearkey", "playready"):
                result = report["providers"][provider]
                self.assertEqual(result["kid_rotation"]["old_kid"], OLD_KID)
                self.assertEqual(result["kid_rotation"]["new_kid"], NEW_KID)
                self.assertFalse(result["old_license_after_rotation"]["valid"])
                self.assertEqual(result["old_request_after_rotation"]["status_code"], 403)
                self.assertTrue(result["new_license_validation"]["valid"])
                self.assertTrue(result["player_after_rewrite"]["parsed"])
                self.assertEqual(
                    result["player_after_rewrite"]["default_kid"], NEW_KID
                )
                self.assertTrue(
                    result["player_after_rewrite"]["header_kid_matches_default"]
                )
                self.assertTrue(
                    result["player_after_rewrite"]["payload_kid_matches_default"]
                )
                self.assertTrue(
                    result["whole_file_byte_audit"]["only_license_fields_changed"]
                )
                self.assertTrue(
                    result["whole_file_byte_audit"]["media_payload"]["unchanged"]
                )
                self.assertEqual(
                    result["pssh"]["diff"]["before_size"],
                    result["pssh"]["diff"]["after_size"],
                )

            saved = json.loads((output / "report.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["checks"], report["checks"])
            events = json.loads(
                (output / "license_state_sequence.json").read_text(encoding="utf-8")
            )["events"]
            revoked = [
                event for event in events if event["event"] == "license.state_changed"
            ]
            self.assertEqual(len(revoked), 2)
            self.assertTrue(all(event["after"] == "REVOKED" for event in revoked))


if __name__ == "__main__":
    unittest.main()
