"""主入口：FLV 解析 -> MP4 重封装 -> 全量校验，输出 JSON 报告。"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from flv import parse_flv, TAG_AUDIO, TAG_VIDEO
from timeline import analyze, report_to_dict, _media_tags
from remux import remux_flv_to_mp4
from mp4_probe import probe_mp4
from compare import compare
from aac import padding_drift_test
from rtp import reconnect_test_scenario


def run(flv_path: str, mp4_path: str) -> dict:
    flv = parse_flv(flv_path)
    timeline = report_to_dict(analyze(flv))

    remux_flv_to_mp4(flv_path, mp4_path)
    mp4_tracks = probe_mp4(mp4_path)
    mp4_summary = [
        {
            "handler": t.handler,
            "timescale": t.timescale,
            "sample_count": t.sample_count,
            "first_dts_ms": t.first_dts_ms,
            "edit_offsets_ms": t.edit_offsets_ms,
            "empty_edit_delay_ms": t.empty_edit_delay_ms,
            "presentation_start_ms": t.presentation_start_ms,
            "has_avcC": t.has_avcC,
            "sync_sample_count": len(t.sync_samples),
        }
        for t in mp4_tracks
    ]
    comparison = compare(flv, mp4_tracks)

    # 帧率与关键帧间隔（题目要求显式输出）
    fps_section = {
        "metadata_framerate": flv.metadata.get("framerate"),
        "measured_fps": timeline["measured_fps"],
        "keyframe_intervals_ms": timeline["keyframe_intervals_ms"],
        "keyframe_stats": timeline["keyframe_interval_stats"],
    }

    report = {
        "inputs": {"flv": flv_path, "mp4": mp4_path},
        "flv_header": {
            "version": flv.header.version,
            "has_audio": flv.header.has_audio,
            "has_video": flv.header.has_video,
        },
        "tag_counts": {
            "script": sum(1 for t in flv.tags if t.kind == "script"),
            "audio": sum(1 for t in flv.tags if t.tag_type == TAG_AUDIO),
            "video": sum(1 for t in flv.tags if t.tag_type == TAG_VIDEO),
            "audio_sequence_header": sum(
                1 for t in flv.tags
                if t.tag_type == TAG_AUDIO and t.is_sequence_header),
            "video_sequence_header": sum(
                1 for t in flv.tags
                if t.tag_type == TAG_VIDEO and t.is_sequence_header),
        },
        "metadata": flv.metadata,
        "timeline": timeline,
        "fps_and_keyframe": fps_section,
        "mp4_tracks": mp4_summary,
        "comparison": comparison,
        "aac_padding_test": padding_drift_test(),
        "rtp_reconnect_test": reconnect_test_scenario(),
    }
    report["checks_passed"] = _evaluate_checks(report)
    return report


def _evaluate_checks(report: dict) -> dict:
    cmp_ = report["comparison"]
    rtp_ok = report["rtp_reconnect_test"]["continuity"]["ok"]
    aac_ok = report["aac_padding_test"]["correct_parser_no_drift"]
    avc_ok = (cmp_["avc_sequence_header"]["flv_avc_seq_header"]
              and cmp_["avc_sequence_header"]["mp4_avcC_present"])
    dts_video = cmp_["dts_match"].get("video_max_abs_ms", 0)
    dts_audio = cmp_["dts_match"].get("audio_max_abs_ms", 0)
    av_delta = cmp_["av_sync"].get("max_abs_delta_ms", 0)
    counts = cmp_["sample_counts"]
    return {
        "avc_sequence_header_retained": avc_ok,
        "first_frame_offset_preserved": (
            cmp_["first_frame_offset"]["flv"]["video_ms"]
            == cmp_["first_frame_offset"]["mp4"]["video_ms"]
            and cmp_["first_frame_offset"]["flv"]["audio_ms"]
            == cmp_["first_frame_offset"]["mp4"]["audio_ms"]
        ),
        "sample_counts_match": (
            counts["flv_video"] == counts["mp4_video"]
            and counts["flv_audio"] == counts["mp4_audio"]
        ),
        "dts_video_within_1ms": dts_video <= 1,
        "dts_audio_within_1ms": dts_audio <= 1,
        "av_sync_error_preserved": av_delta <= 1,
        "aac_padding_no_accumulated_drift": aac_ok,
        "rtp_timestamp_continuous_after_reconnect": rtp_ok,
        "interleave_ok": report["timeline"]["interleave_ok"],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="FLV 解析与 MP4 重封装校验工具")
    ap.add_argument("flv", help="输入 FLV 文件")
    ap.add_argument("-o", "--output", required=True, help="输出 MP4 路径")
    ap.add_argument("--json", help="同时把报告写入该 JSON 文件")
    args = ap.parse_args()

    report = run(args.flv, args.output)
    text = json.dumps(report, indent=2, ensure_ascii=False)
    print(text)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            fh.write(text)
    return 0 if all(report["checks_passed"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
