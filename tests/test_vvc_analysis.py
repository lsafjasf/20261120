from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from vvc_analysis import analyze_stream, iter_nal_units, rbsp_from_ebsp
from vvc_analysis.audio import analyze_band_switch


ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "samples"


class AnnexBTests(unittest.TestCase):
    def test_three_and_four_byte_start_codes_preserve_boundaries(self) -> None:
        first = b"\x00\x00\x01\x00\xd1payload-a"
        second = b"\x00\x00\x00\x01\x00\x79payload-b"
        nals = list(iter_nal_units(first + second))
        self.assertEqual(len(nals), 2)
        self.assertEqual(nals[0].start_offset, 0)
        self.assertEqual(nals[0].prefix_length, 3)
        self.assertEqual(nals[0].nal_type, 26)
        self.assertEqual(nals[1].start_offset, len(first))
        self.assertEqual(nals[1].prefix_length, 4)
        self.assertEqual(nals[1].nal_type, 15)
        self.assertEqual(nals[0].raw + nals[1].raw, first[3:] + second[4:])

    def test_emulation_prevention_removal(self) -> None:
        self.assertEqual(
            rbsp_from_ebsp(b"\x00\x00\x03\x01\x00\x00\x03\x03"),
            b"\x00\x00\x01\x00\x00\x03",
        )


class ParameterSetTests(unittest.TestCase):
    def test_conformance_stream_has_vps_sps_pps(self) -> None:
        report = analyze_stream(
            (SAMPLES / "streams" / "vps_sps_pps_multires.vvc").read_bytes()
        )
        by_key = {
            (item["kind"], item["id"]): item for item in report["parameter_sets"]
        }
        self.assertEqual(by_key[("VPS", 1)]["first_start_offset"], 7)
        self.assertEqual(by_key[("VPS", 1)]["values"]["vps_max_layers_minus1"], 1)
        self.assertEqual(by_key[("SPS", 0)]["values"]["resolution"], "208x120")
        self.assertEqual(by_key[("SPS", 1)]["values"]["resolution"], "832x480")
        self.assertEqual(by_key[("SPS", 1)]["values"]["bit_depth"], 10)
        self.assertEqual(by_key[("SPS", 1)]["values"]["chroma_format"], "4:2:0")
        self.assertEqual(by_key[("PPS", 0)]["first_start_offset"], 139)
        self.assertEqual(by_key[("PPS", 1)]["first_start_offset"], 1454)

    def test_switch_stream_resolves_pps_to_correct_sps(self) -> None:
        report = analyze_stream(
            (SAMPLES / "streams" / "switch_low_high_low.vvc").read_bytes()
        )
        self.assertEqual(len(report["sps_events"]), 3)
        self.assertEqual(report["sps_events"][1]["before"]["resolution"], "96x64")
        self.assertEqual(report["sps_events"][1]["after"]["resolution"], "192x128")
        self.assertEqual(report["sps_events"][1]["after"]["bit_depth"], 10)
        self.assertEqual(
            [(item["sps_resolution"], item["sps_bit_depth"]) for item in report["pps_links"]],
            [("96x64", 8), ("192x128", 10), ("96x64", 8)],
        )


class UnknownNalTests(unittest.TestCase):
    def test_unknown_nals_are_written_verbatim(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            report = analyze_stream(
                (SAMPLES / "streams" / "switch_with_unknown.vvc").read_bytes(),
                unknown_dir=temp,
            )
            self.assertEqual(
                [item["nal_type"] for item in report["unknown_nals"]],
                [26, 27, 29, 31],
            )
            self.assertEqual(
                [item["total_bytes"] for item in report["unknown_nal_distribution"]],
                [27, 27, 23, 23],
            )
            for item in report["unknown_nals"]:
                saved = Path(item["file"]).read_bytes()
                self.assertEqual(saved, bytes.fromhex(item["raw_hex"]))
                self.assertEqual(hashlib.sha256(saved).hexdigest(), item["sha256"])


class ValidationArtifactTests(unittest.TestCase):
    def test_decoder_switch_has_no_frame_loss(self) -> None:
        low = (SAMPLES / "decoded" / "low_96x64_8bit_ref.yuv").read_bytes()
        high = (SAMPLES / "decoded" / "high_192x128_10bit_ref.yuv").read_bytes()
        switch_output = (
            SAMPLES / "decoded" / "switch_low_high_low.yuv"
        ).read_bytes()
        self.assertEqual(switch_output, low + high + low)
        report = json.loads(
            (SAMPLES / "report" / "validation.json").read_text(encoding="utf-8")
        )
        switch = report["switch_stream"]
        self.assertTrue(switch["no_frame_loss"])
        self.assertEqual(switch["expected_frames"], 36)
        self.assertEqual(switch["actual_frames"], 36)
        self.assertTrue(
            all(item["byte_exact_match"] for item in switch["segment_comparisons"])
        )

    def test_band_transition_is_continuous(self) -> None:
        analyzed = analyze_band_switch(
            SAMPLES / "audio" / "band_switch_smooth.wav",
            0.45,
            120.0,
            SAMPLES / "audio" / "band_switch_hard_control.wav",
        )
        self.assertTrue(analyzed["continuous_transition_pass"])
        self.assertLess(analyzed["max_high_share_step"], 0.05)
        report = json.loads(
            (SAMPLES / "report" / "validation.json").read_text(encoding="utf-8")
        )
        band = report["band_switch"]
        self.assertTrue(band["continuous_transition_pass"])
        self.assertEqual(band["transition_count"], 10)
        self.assertLess(band["max_high_share_step"], 0.05)
        self.assertLess(
            band["max_high_share_step"],
            band["hard_control_max_high_share_step"],
        )


if __name__ == "__main__":
    unittest.main()
