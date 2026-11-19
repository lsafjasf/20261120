"""端到端与单元测试（纯标准库 unittest）。

运行：python3 -B -m unittest discover -s tests -v
"""
from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from flv import parse_flv, TAG_AUDIO, TAG_VIDEO, AVC_SEQ_HEADER, AAC_SEQ_HEADER
from timeline import analyze, _media_tags
from mp4_probe import probe_mp4
from compare import compare
from aac import padding_drift_test, parse_adts_stream, build_adts_frame
from rtp import (reconnect_test_scenario, build_rtp, parse_rtp,
                 u32_diff, u16_diff, Segment, reconnect_continuity)

FLV = os.path.join(ROOT, "fixtures", "sample.flv")
MP4 = os.path.join(ROOT, "out", "test_sample.mp4")


class FlvParseTests(unittest.TestCase):
    def setUp(self):
        self.flv = parse_flv(FLV)

    def test_header(self):
        self.assertTrue(self.flv.header.has_audio)
        self.assertTrue(self.flv.header.has_video)
        self.assertEqual(self.flv.header.version, 1)

    def test_script_metadata(self):
        self.assertEqual(self.flv.metadata.get("framerate"), 25.0)
        self.assertEqual(self.flv.metadata.get("audiosamplerate"), 44100.0)
        self.assertEqual(self.flv.metadata.get("width"), 320.0)

    def test_sequence_headers_present(self):
        vseq = [t for t in self.flv.tags
                if t.tag_type == TAG_VIDEO and t.is_sequence_header]
        aseq = [t for t in self.flv.tags
                if t.tag_type == TAG_AUDIO and t.is_sequence_header]
        self.assertEqual(len(vseq), 1)
        self.assertEqual(len(aseq), 1)
        self.assertEqual(vseq[0].body[1], AVC_SEQ_HEADER)
        self.assertEqual(aseq[0].body[1], AAC_SEQ_HEADER)

    def test_first_frame_offsets(self):
        v = _media_tags(self.flv.tags, TAG_VIDEO)
        a = _media_tags(self.flv.tags, TAG_AUDIO)
        self.assertEqual(a[0].timestamp_ms, 0)
        self.assertEqual(v[0].timestamp_ms, 23)

    def test_interleaving(self):
        report = analyze(self.flv)
        self.assertTrue(report.interleave_ok)

    def test_keyframe_interval(self):
        report = analyze(self.flv)
        self.assertEqual(report.keyframe_interval_stats["max_ms"], 2000)
        self.assertEqual(report.metadata_fps, 25.0)


class Mp4RemuxTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from remux import remux_flv_to_mp4
        remux_flv_to_mp4(FLV, MP4)
        cls.flv = parse_flv(FLV)
        cls.tracks = probe_mp4(MP4)

    def test_avcc_retained(self):
        v = next(t for t in self.tracks if t.handler == "vide")
        self.assertTrue(v.has_avcC)

    def test_sample_counts(self):
        v = next(t for t in self.tracks if t.handler == "vide")
        a = next(t for t in self.tracks if t.handler == "soun")
        self.assertEqual(v.sample_count,
                         len(_media_tags(self.flv.tags, TAG_VIDEO)))
        self.assertEqual(a.sample_count,
                         len(_media_tags(self.flv.tags, TAG_AUDIO)))

    def test_presentation_offset_preserved(self):
        v = next(t for t in self.tracks if t.handler == "vide")
        self.assertEqual(v.presentation_start_ms, 23)
        self.assertIn(-1, v.edit_offsets_ms)

    def test_keyframes_in_mp4(self):
        v = next(t for t in self.tracks if t.handler == "vide")
        kf = [v.dts_ms[i - 1] for i in v.sync_samples]
        intervals = [b - a for a, b in zip(kf, kf[1:])]
        self.assertTrue(all(d == 2000 for d in intervals))

    def test_comparison(self):
        result = compare(self.flv, self.tracks)
        self.assertTrue(
            result["avc_sequence_header"]["flv_avc_seq_header"])
        self.assertTrue(
            result["avc_sequence_header"]["mp4_avcC_present"])
        self.assertLessEqual(result["dts_match"]["video_max_abs_ms"], 1)
        self.assertLessEqual(result["dts_match"]["audio_max_abs_ms"], 1)
        self.assertEqual(
            result["first_frame_offset"]["flv"],
            result["first_frame_offset"]["mp4"])


class AacPaddingTests(unittest.TestCase):
    def test_no_accumulated_drift(self):
        result = padding_drift_test()
        self.assertTrue(result["correct_parser_no_drift"])
        self.assertEqual(result["mixed_header_lengths"], [7, 9])
        # 朴素固定 7 字节解析必须被证明会累积偏移
        self.assertGreater(result["naive_fixed7_max_drift_bytes"], 0)

    def test_round_trip(self):
        frame = build_adts_frame(50, 44100, 2, protection_absent=0)
        headers = parse_adts_stream(frame)
        self.assertEqual(headers[0].header_length, 9)
        self.assertEqual(headers[0].frame_length, 59)
        frame7 = build_adts_frame(50, 44100, 2, protection_absent=1)
        headers7 = parse_adts_stream(frame7)
        self.assertEqual(headers7[0].header_length, 7)


class RtpReconnectTests(unittest.TestCase):
    def test_wraparound_continuity(self):
        result = reconnect_test_scenario()
        self.assertTrue(result["segment2_wrapped_32bit"])
        self.assertTrue(result["continuity"]["ok"])
        self.assertTrue(result["continuity"]["ssrc_changed"])
        self.assertTrue(
            result["continuity"]["raw_rtp_timestamp_rewound_at_join"])

    def test_sequence_wrap_safe(self):
        self.assertEqual(u16_diff(0, 0xFFFF), 1)
        self.assertEqual(u32_diff(100, 0xFFFFFFF0), 116)

    def test_joined_timeline_steps_uniform(self):
        result = reconnect_test_scenario()
        c = result["continuity"]
        self.assertEqual(c["max_step_ms"], 40)
        self.assertTrue(c["monotonic"])

    def test_packet_parse(self):
        pkt = parse_rtp(build_rtp(5, 1000, 42, payload=b"xyz"))
        self.assertEqual(pkt.sequence, 5)
        self.assertEqual(pkt.timestamp, 1000)
        self.assertEqual(pkt.ssrc, 42)
        self.assertEqual(pkt.payload, b"xyz")


class BFrameFixtureTests(unittest.TestCase):
    """含 B 帧（非零 CTS）的第二个素材，验证 PTS 口径的偏移对比。"""
    FLV2 = os.path.join(ROOT, "fixtures", "sample2.flv")
    MP4_2 = os.path.join(ROOT, "out", "test_sample2.mp4")

    @classmethod
    def setUpClass(cls):
        from remux import remux_flv_to_mp4
        cls.flv = parse_flv(cls.FLV2)
        remux_flv_to_mp4(cls.FLV2, cls.MP4_2)
        cls.tracks = probe_mp4(cls.MP4_2)

    def test_nonzero_cts_exists(self):
        cts_values = {t.cts_ms for t in self.flv.tags if t.cts_ms != 0}
        self.assertTrue(cts_values)

    def test_mp4_ctts_populated(self):
        v = next(t for t in self.tracks if t.handler == "vide")
        self.assertTrue(any(p != d for p, d in zip(v.pts_ms, v.dts_ms)))

    def test_presentation_offset_preserved(self):
        result = compare(self.flv, self.tracks)
        off = result["first_frame_offset"]
        self.assertEqual(off["flv"]["video_ms"], off["mp4"]["video_ms"])
        self.assertEqual(off["flv"]["audio_ms"], off["mp4"]["audio_ms"])

    def test_avcc_retained(self):
        v = next(t for t in self.tracks if t.handler == "vide")
        self.assertTrue(v.has_avcC)


if __name__ == "__main__":
    unittest.main(verbosity=2)
