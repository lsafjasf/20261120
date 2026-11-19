from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ogg_timeline import PredictorConfig, analyze_file, parse_pages, predict_stall
from ogg_timeline.ogg import ogg_crc
from ogg_timeline.report import (
    build_report_payload,
    codec_semantics_comparison,
    stall_prediction_report,
)


FIXTURES = ROOT / "fixtures"


def make_page(
    *,
    serial: int,
    sequence: int,
    granule: int,
    segments: list[int],
    payload: bytes,
    header_type: int = 0,
) -> bytes:
    header = bytearray(27)
    header[0:4] = b"OggS"
    header[4] = 0
    header[5] = header_type
    header[6:14] = granule.to_bytes(8, "little", signed=True)
    header[14:18] = serial.to_bytes(4, "little")
    header[18:22] = sequence.to_bytes(4, "little")
    header[26] = len(segments)
    page = header + bytes(segments) + payload
    checksum = ogg_crc(bytes(page))
    page[22:26] = checksum.to_bytes(4, "little")
    return bytes(page)


def analyze(name: str):
    streams = analyze_file(FIXTURES / name)
    if len(streams) != 1:
        raise AssertionError(f"expected one logical stream, got {len(streams)}")
    return streams[0]


class OggPageTests(unittest.TestCase):
    def test_crc_and_page_sequences(self):
        for name in (
            "continuous.vorbis.ogg",
            "discontinuous.vorbis.ogg",
            "continuous.opus.ogg",
            "discontinuous.opus.ogg",
        ):
            with self.subTest(name=name):
                parsed = parse_pages((FIXTURES / name).read_bytes())
                self.assertEqual(len(parsed.streams), 1)
                self.assertTrue(all(page.crc_ok for page in parsed.pages))
                self.assertEqual(
                    [page.sequence for page in parsed.pages],
                    list(range(len(parsed.pages))),
                )

    def test_vorbis_headers_and_packet_clock(self):
        result = analyze("continuous.vorbis.ogg")
        self.assertEqual(result.codec, "vorbis")
        self.assertEqual(result.codec_info.sample_rate, 44_100)
        self.assertEqual(result.codec_info.blocksize_small, 256)
        self.assertEqual(result.codec_info.blocksize_large, 2048)
        self.assertEqual(result.codec_info.modes, (False, True))
        self.assertEqual(result.audio_packet_count, 158)
        self.assertEqual(result.pages[2].granule_position, 22_208)
        self.assertEqual(result.pages[2].sample_start, -128)
        self.assertAlmostEqual(result.pages[2].time_end_s, 22_208 / 44_100)

    def test_spanning_packet_uses_minus_one_granule(self):
        first_payload = bytes(range(255))
        data = make_page(
            serial=99,
            sequence=0,
            granule=-1,
            segments=[255],
            payload=first_payload,
        ) + make_page(
            serial=99,
            sequence=1,
            granule=0,
            segments=[8],
            payload=b"taildata",
            header_type=1,
        )
        parsed = parse_pages(data)
        stream = parsed.streams[99]
        self.assertEqual(len(stream.packets), 1)
        self.assertEqual(stream.packets[0].data, first_payload + b"taildata")
        self.assertEqual(parsed.pages[0].granule_position, -1)
        self.assertEqual(parsed.pages[0].packet_fragment_count, 1)
        self.assertEqual(parsed.pages[1].completed_packet_indices, [0])

    def test_opus_pre_skip_and_fixed_48k_clock(self):
        result = analyze("continuous.opus.ogg")
        self.assertEqual(result.codec, "opus")
        self.assertEqual(result.codec_info.pre_skip, 312)
        self.assertEqual(result.codec_info.sample_rate, 48_000)
        self.assertEqual(result.audio_packet_count, 40)
        first_audio = result.pages[2]
        self.assertEqual(first_audio.granule_position, 4_152)
        self.assertEqual(first_audio.pcm_sample_end, 4_152 - 312)
        self.assertAlmostEqual(first_audio.pcm_time_end_s, 3_840 / 48_000)
        deltas = [
            page.granule_position - page.previous_granule_position
            for page in result.pages
            if page.kind == "audio" and page.previous_granule_position is not None
        ]
        self.assertEqual(deltas, [4_152, *([3_840] * 9)])


class DiscontinuityTests(unittest.TestCase):
    def assert_single_gap(
        self, name: str, page_index: int, before: int, current: int, after: int
    ):
        result = analyze(name)
        timeline = [
            item for item in result.discontinuities
            if item.kind.startswith("timeline_")
        ]
        self.assertEqual(len(timeline), 1)
        finding = timeline[0]
        self.assertEqual(finding.kind, "timeline_granule_gap")
        self.assertEqual(finding.page_index, page_index)
        self.assertEqual(finding.previous_granule_position, before)
        self.assertEqual(finding.granule_position, current)
        self.assertEqual(finding.next_granule_position, after)
        self.assertGreater(finding.signed_error_samples, 0)
        self.assertTrue(result.mapping.mapping_continuous)
        self.assertTrue(result.mapping.no_backward_jump)
        self.assertFalse(result.mapping.timeline_continuous)
        return result, finding

    def test_vorbis_gap_page_and_neighbors(self):
        result, finding = self.assert_single_gap(
            "discontinuous.vorbis.ogg", 5, 67_264, 90_304, 112_832
        )
        self.assertEqual(finding.expected_delta_samples, 22_528)
        self.assertEqual(finding.actual_delta_samples, 23_040)
        self.assertEqual(finding.signed_error_samples, 512)
        self.assertTrue(result.mapping.non_decreasing_granules)

    def test_opus_gap_page_and_neighbors(self):
        result, finding = self.assert_single_gap(
            "discontinuous.opus.ogg", 6, 15_672, 20_472, 24_312
        )
        self.assertEqual(finding.expected_delta_samples, 3_840)
        self.assertEqual(finding.actual_delta_samples, 4_800)
        self.assertEqual(finding.signed_error_samples, 960)
        self.assertTrue(result.mapping.non_decreasing_granules)

    def test_continuous_fixtures_have_no_discontinuity(self):
        for name in ("continuous.vorbis.ogg", "continuous.opus.ogg"):
            with self.subTest(name=name):
                result = analyze(name)
                self.assertEqual(result.discontinuities, [])
                self.assertTrue(result.mapping.mapping_continuous)
                self.assertTrue(result.mapping.timeline_continuous)


class PredictorTests(unittest.TestCase):
    def test_low_buffer_threshold_is_more_conservative(self):
        config = PredictorConfig()
        self.assertGreater(
            config.low_buffer_threshold_ms, config.normal_threshold_ms
        )
        at_180 = predict_stall(180.0, config)
        self.assertEqual(at_180.tier, "low_buffer")
        self.assertEqual(at_180.threshold_ms, 240.0)
        self.assertTrue(at_180.predicted_stall)
        at_320 = predict_stall(320.0, config)
        self.assertEqual(at_320.tier, "normal")
        self.assertFalse(at_320.predicted_stall)

    def test_report_threshold(self):
        report = stall_prediction_report()
        self.assertTrue(report["low_buffer_is_more_conservative"])
        self.assertEqual(report["prediction_threshold_ms"], 240.0)
        self.assertTrue(report["confirmed"])


class ReportTests(unittest.TestCase):
    def test_semantics_and_acceptance(self):
        names = [
            "continuous.vorbis.ogg",
            "discontinuous.vorbis.ogg",
            "continuous.opus.ogg",
            "discontinuous.opus.ogg",
        ]
        payload = build_report_payload(FIXTURES / name for name in names)
        semantics = payload["codec_semantics"]
        self.assertTrue(semantics["timestamp_semantics_differ"])
        self.assertFalse(semantics["raw_increment_equals_elapsed_time"])
        self.assertEqual(semantics["opus"]["clock_hz"], 48_000)
        self.assertEqual(semantics["opus"]["pre_skip"], 312)
        self.assertEqual(semantics["vorbis"]["clock_hz"], 44_100)
        acceptance = payload["acceptance"]
        self.assertTrue(acceptance["granule_mapping_continuous"])
        self.assertTrue(acceptance["no_backward_time_jump"])
        self.assertTrue(acceptance["normalized_mapping_continuous"])
        self.assertEqual(len(acceptance["discontinuity_page_locations"]), 2)

    def test_cli_writes_all_formats(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            completed = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "ogg_granule.py"),
                    "analyze",
                    str(FIXTURES / "discontinuous.opus.ogg"),
                    "--json",
                    str(output / "report.json"),
                    "--md",
                    str(output / "report.md"),
                    "--csv",
                    str(output / "pages.csv"),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertTrue(json.loads((output / "report.json").read_text()))
            self.assertIn(
                "timeline_granule_gap",
                (output / "report.json").read_text(),
            )
            self.assertIn("discontinuous.opus.ogg", (output / "report.md").read_text())
            self.assertGreater(len((output / "pages.csv").read_text().splitlines()), 2)

    def test_codec_comparison_can_be_called_directly(self):
        vorbis = analyze("continuous.vorbis.ogg")
        opus = analyze("continuous.opus.ogg")
        comparison = codec_semantics_comparison(vorbis, opus)
        self.assertTrue(comparison["shared_rules"]["spanning_packet_marks_-1"])
        self.assertEqual(comparison["vorbis"]["clock_rule"], "native stream sample rate per channel")
        self.assertEqual(comparison["opus"]["playback_time"], "(granule_position - pre_skip) / 48000")


if __name__ == "__main__":
    unittest.main()
