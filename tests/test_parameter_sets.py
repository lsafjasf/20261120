from __future__ import annotations

import unittest

from drm_sim.demo import HEVC_FIXTURE
from drm_sim.parameter_sets import (
    MissingParameterSetError,
    inspect_parameter_sets,
    require_parameter_sets,
)


def _hevc_nal(nal_type: int, payload: bytes = b"\x80") -> bytes:
    header = bytes(((nal_type << 1) & 0x7E, 1))
    return b"\x00\x00\x00\x01" + header + payload


class ParameterSetTests(unittest.TestCase):
    def test_vps_sps_without_pps_reports_missing_pps(self) -> None:
        data = HEVC_FIXTURE.read_bytes()
        report = inspect_parameter_sets(data, "hevc")
        self.assertEqual(report.nal_types, (32, 33))
        self.assertEqual(report.counts, {32: 1, 33: 1})
        self.assertFalse(report.complete)
        self.assertEqual(report.missing_types, (34,))
        self.assertEqual(report.to_dict()["error_code"], "MISSING_PPS")
        with self.assertRaises(MissingParameterSetError) as caught:
            require_parameter_sets(data, "hevc")
        self.assertEqual(caught.exception.code, "MISSING_PPS")
        self.assertIn("VPS=1", str(caught.exception))
        self.assertIn("SPS=1", str(caught.exception))

    def test_complete_hevc_parameter_sets_pass(self) -> None:
        data = _hevc_nal(32) + _hevc_nal(33) + _hevc_nal(34)
        report = require_parameter_sets(data, "hevc")
        self.assertTrue(report.complete)
        self.assertEqual(report.missing_types, ())

    def test_h264_sps_pps_without_pps_is_reported(self) -> None:
        sps = b"\x00\x00\x00\x01\x67\x42\x00\x1e"
        report = inspect_parameter_sets(sps, "h264")
        self.assertEqual(report.nal_types, (7,))
        self.assertEqual(report.missing_types, (8,))
        self.assertEqual(report.to_dict()["error_code"], "MISSING_PPS")


if __name__ == "__main__":
    unittest.main()
