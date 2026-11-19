from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from dcp_qc.fixture_builder import (
    PIC_R2,
    SND_R1,
    SND_R2,
    build_two_reel,
)
from dcp_qc.inspector import inspect_package
from dcp_qc.model import DiagnosticCode
from dcp_qc.render import render_report

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures"


class InspectorTests(unittest.TestCase):
    def test_two_reel_package_has_unified_timeline(self) -> None:
        report = inspect_package(
            FIXTURES / "base_two_reel", chunk_size=7
        )
        self.assertTrue(report.ok, report.diagnostics)
        self.assertEqual(float(report.total_duration), 20.0)
        self.assertEqual(report.checked_assets, 6)
        self.assertEqual(report.checked_bytes, 880)
        self.assertEqual(report.edit_rates, ["24", "48000"])
        self.assertEqual(len(report.timeline), 6)
        r2_sound = next(
            segment
            for segment in report.timeline
            if segment.asset_id == SND_R2
        )
        self.assertEqual(r2_sound.volume, "VOL1, VOL2")
        self.assertEqual(float(r2_sound.start), 10.0)
        self.assertEqual(float(r2_sound.end), 20.0)
        rendered = render_report(report)
        self.assertIn("R1 P", rendered)
        self.assertIn("R2 S", rendered)
        self.assertIn("[VOL1, VOL2]", rendered)

    def test_supplemental_inherits_base_assets(self) -> None:
        report = inspect_package(
            FIXTURES / "supplemental",
            base_packages=(FIXTURES / "base_two_reel",),
            chunk_size=13,
        )
        self.assertTrue(report.ok, report.diagnostics)
        inherited = [segment for segment in report.timeline if segment.inherited]
        self.assertEqual(len(inherited), 4)
        self.assertEqual(report.checked_assets, 6)

    def test_inherited_asset_missing_reports_reel_asset_and_time(self) -> None:
        case = FIXTURES / "faults" / "inherited_missing"
        report = inspect_package(
            case / "supplemental",
            base_packages=(case / "base",),
        )
        self.assertFalse(report.ok)
        self.assertEqual(len(report.diagnostics), 1)
        diagnostic = report.diagnostics[0]
        self.assertEqual(diagnostic.code, DiagnosticCode.MISSING_REEL)
        self.assertEqual(diagnostic.reel, "reel-2")
        self.assertEqual(diagnostic.asset_id, PIC_R2)
        self.assertEqual(diagnostic.volume, "VOL2")
        self.assertEqual(diagnostic.screening_time, "00:00:10.000")
        self.assertTrue(diagnostic.details["inherited"])

    def test_different_edit_rate_overflow_reports_screening_boundary(self) -> None:
        report = inspect_package(FIXTURES / "rate_overflow")
        self.assertEqual(len(report.diagnostics), 1)
        diagnostic = report.diagnostics[0]
        self.assertEqual(
            diagnostic.code, DiagnosticCode.PLAYABLE_TIME_OVERFLOW
        )
        self.assertEqual(diagnostic.reel, "reel-2")
        self.assertEqual(diagnostic.asset_id, SND_R2)
        self.assertEqual(diagnostic.screening_time, "00:00:20.000")
        self.assertEqual(diagnostic.volume, "VOL1, VOL2")
        self.assertEqual(
            diagnostic.details["boundary"], "reel_picture_duration"
        )
        self.assertEqual(diagnostic.details["edit_rate"], "48000")
        self.assertIn("10.208333", diagnostic.message)

    def test_missing_second_chunk_reports_that_volume(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            package = build_two_reel(Path(temporary) / "package")
            (package / "VOL2" / "sound_r2.part2.mxf").unlink()
            report = inspect_package(package)
        self.assertEqual(len(report.diagnostics), 1)
        diagnostic = report.diagnostics[0]
        self.assertEqual(diagnostic.code, DiagnosticCode.MISSING_REEL)
        self.assertEqual(diagnostic.asset_id, SND_R2)
        self.assertEqual(diagnostic.volume, "VOL2")
        self.assertEqual(diagnostic.reel, "reel-2")

    def test_checksum_failure_is_streamed_and_located(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            package = build_two_reel(Path(temporary) / "package")
            asset_path = package / "VOL1" / "sound_r1.mxf"
            data = bytearray(asset_path.read_bytes())
            data[0] ^= 0xFF
            asset_path.write_bytes(data)
            report = inspect_package(package, chunk_size=3)
        self.assertEqual(len(report.diagnostics), 1)
        diagnostic = report.diagnostics[0]
        self.assertEqual(diagnostic.code, DiagnosticCode.CHECKSUM_FAILURE)
        self.assertEqual(diagnostic.reel, "reel-1")
        self.assertEqual(diagnostic.asset_id, SND_R1)
        self.assertEqual(diagnostic.screening_time, "00:00:00.000")
        self.assertEqual(diagnostic.volume, "VOL1")
        self.assertNotEqual(
            diagnostic.details["actual_hash"],
            diagnostic.details["expected_hash"],
        )

    def test_reference_mismatch_is_distinct_from_missing_media(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            package = build_two_reel(Path(temporary) / "package")
            cpl_path = package / "VOL1" / "CPL.xml"
            text = cpl_path.read_text(encoding="utf-8")
            unknown = "urn:uuid:00000000-0000-4000-8000-000000009999"
            cpl_path.write_text(
                text.replace(PIC_R2, unknown), encoding="utf-8"
            )
            report = inspect_package(package)
        self.assertEqual(len(report.diagnostics), 1)
        diagnostic = report.diagnostics[0]
        self.assertEqual(
            diagnostic.code, DiagnosticCode.REFERENCE_MISMATCH
        )
        self.assertEqual(diagnostic.reel, "reel-2")
        self.assertEqual(diagnostic.asset_id, unknown)
        self.assertEqual(diagnostic.screening_time, "00:00:10.000")

    def test_source_intrinsic_overflow_reports_available_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            package = build_two_reel(Path(temporary) / "package")
            cpl_path = package / "VOL1" / "CPL.xml"
            text = cpl_path.read_text(encoding="utf-8")
            prefix, rest = text.split(SND_R2, 1)
            rest = rest.replace(
                "<IntrinsicDuration>480000</IntrinsicDuration>",
                "<IntrinsicDuration>240000</IntrinsicDuration>",
                1,
            )
            cpl_path.write_text(prefix + SND_R2 + rest, encoding="utf-8")
            report = inspect_package(package)
        self.assertEqual(len(report.diagnostics), 1)
        diagnostic = report.diagnostics[0]
        self.assertEqual(
            diagnostic.code, DiagnosticCode.PLAYABLE_TIME_OVERFLOW
        )
        self.assertEqual(
            diagnostic.details["boundary"], "source_available_duration"
        )
        self.assertEqual(diagnostic.reel, "reel-2")
        self.assertEqual(diagnostic.screening_time, "00:00:15.000")

    def test_supplemental_without_base_reports_dangling_inheritance(self) -> None:
        report = inspect_package(FIXTURES / "supplemental")
        codes = {item.code for item in report.diagnostics}
        self.assertIn(DiagnosticCode.REFERENCE_MISMATCH, codes)
        self.assertNotIn(DiagnosticCode.MISSING_REEL, codes)


if __name__ == "__main__":
    unittest.main()
