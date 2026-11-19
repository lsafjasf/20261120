"""Generate deterministic fixtures and a complete acceptance report."""

from __future__ import annotations

from pathlib import Path
import json
import struct

from .caf import CAFFile
from .errors import InvalidEndianFlagError
from .extended import decode_extended_hex, encode_extended
from .fixtures import (
    SAMPLE_FRAMES,
    build_invalid_endian_flag,
    build_little_endian,
    build_misordered_big_endian,
)
from .timebase import (
    boundary_values,
    conversion_boundaries,
    drop_frame_checks,
)

EXTENDED_REFERENCES = {
    8000.0: "400bfa00000000000000",
    11025.0: "400cac44000000000000",
    22050.0: "400dac44000000000000",
    32000.0: "400dfa00000000000000",
    44100.0: "400eac44000000000000",
    48000.0: "400ebb80000000000000",
    96000.0: "400fbb80000000000000",
    192000.0: "4010bb80000000000000",
}


def _sample_evidence(caf: CAFFile) -> list[dict]:
    rows = []
    for frame in caf.read_frames():
        if caf.description.is_big_endian:
            raw = struct.pack(">hh", *frame)
            wrong = struct.unpack("<hh", raw)
        else:
            raw = struct.pack("<hh", *frame)
            wrong = struct.unpack(">hh", raw)
        rows.append(
            {
                "raw_hex": raw.hex(),
                "decoded": list(frame),
                "wrong_byte_order": list(wrong),
                "match": list(frame) == list(SAMPLE_FRAMES[len(rows)]),
            }
        )
    return rows


def _extended_evidence() -> list[dict]:
    rows = []
    for value, expected_hex in EXTENDED_REFERENCES.items():
        encoded = encode_extended(value)
        decoded = decode_extended_hex(expected_hex)
        rows.append(
            {
                "value": value,
                "reference_hex": expected_hex,
                "encoded_hex": encoded.hex(),
                "decoded": decoded,
                "match": encoded.hex() == expected_hex and decoded == value,
            }
        )
    return rows


def run(out_dir: str | Path = "artifacts") -> dict:
    output = Path(out_dir)
    output.mkdir(parents=True, exist_ok=True)

    big_path = output / "big_endian_misordered.caf"
    little_path = output / "little_endian_control.caf"
    invalid_path = output / "invalid_endian_flag.caf"
    big_path.write_bytes(build_misordered_big_endian())
    little_path.write_bytes(build_little_endian())
    invalid_path.write_bytes(build_invalid_endian_flag())

    big = CAFFile(big_path)
    little = CAFFile(little_path)
    sample_evidence = _sample_evidence(big)
    expected = [list(frame) for frame in SAMPLE_FRAMES]
    decoded = [row["decoded"] for row in sample_evidence]

    invalid_error: dict
    try:
        CAFFile(invalid_path)
    except InvalidEndianFlagError as exc:
        invalid_error = {"type": type(exc).__name__, "message": str(exc)}
    else:
        invalid_error = {"type": "none", "message": "no error was raised"}

    timecode_rows = boundary_values()
    conversion_rows = conversion_boundaries()
    drop_checks = drop_frame_checks()
    extended_rows = _extended_evidence()

    checks = {
        "big_endian_decoded_exactly": decoded == expected,
        "big_endian_not_byte_swapped": all(
            row["decoded"] != row["wrong_byte_order"] for row in sample_evidence
        ),
        "little_endian_control_matches": (
            [list(frame) for frame in little.read_frames()] == expected
        ),
        "channel_layout_stereo": (
            big.channel_layout.channel_names == ("Left", "Right")
        ),
        "chunk_order_anomaly_handled": (
            [chunk.chunk_type for chunk in big.chunks]
            == ["chan", "free", "data", "pakt", "desc"]
        ),
        "invalid_flag_message_clear": (
            invalid_error["type"] == "InvalidEndianFlagError"
            and "big-endian flag" in invalid_error["message"]
        ),
        "drop_frame_rules": (
            drop_checks["before_first_drop"]["frame"] == 1799
            and drop_checks["after_first_drop"]["frame"] == 1800
            and drop_checks["tenth_minute_boundary"]["frame"] == 17982
            and drop_checks["hour_boundary"]["frame"] == 107892
        ),
        "extended_float_matches_reference": all(
            row["match"] for row in extended_rows
        ),
    }

    report = {
        "title": "CAF 大端样本、时间基与扩展浮点验收",
        "checks": checks,
        "all_passed": all(checks.values()),
        "caf": big.analysis(sample_limit=len(SAMPLE_FRAMES)),
        "little_endian_control": little.analysis(sample_limit=len(SAMPLE_FRAMES)),
        "sample_evidence": sample_evidence,
        "invalid_endian_flag": {
            "fixture": str(invalid_path),
            **invalid_error,
        },
        "timebase_boundaries": timecode_rows,
        "conversion_boundaries": conversion_rows,
        "drop_frame_checks": drop_checks,
        "extended_float_references": extended_rows,
    }
    (output / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (output / "report.md").write_text(_markdown(report), encoding="utf-8")
    return report


def _markdown(report: dict) -> str:
    caf = report["caf"]
    desc = caf["audio_description"]
    layout = caf["channel_layout"]
    lines = [
        "# CAF 大端样本、时间基与扩展浮点验收报告",
        "",
        "## 结论",
        "",
        f"- 全部检查通过: **{report['all_passed']}**",
        f"- CAF 块顺序: `{' -> '.join(caf['chunk_order'])}`",
        f"- 音频格式: `{desc['format_id']} / {desc['sample_kind']}`",
        f"- 帧数: data 容器 {caf['container_frame_count']}，pakt 有效帧 {caf['frame_count']}",
        f"- 大端标志: `{desc['format_flags']}`，bit 1 = "
        f"`{desc['little_endian_flag']}`，因此按 **{desc['byte_order']}-endian** 读取",
        f"- 声道布局: **{layout['layout_name']}** "
        f"({', '.join(layout['channels'])})",
        f"- 音频数据起点: `{caf['audio_data']['payload_offset']}` "
        f"(跳过 edit count `{caf['audio_data']['edit_count']}`)",
        "- 顺序异常提示: "
        + "；".join(caf["warnings"] if caf["warnings"] else ["无"]),
        "",
        "## 复现",
        "",
        "```bash",
        "python3 -m unittest discover -s tests -v",
        "python3 -m caf_audio selftest --out artifacts",
        "python3 -m caf_audio inspect artifacts/big_endian_misordered.caf",
        "```",
        "",
        "## CAF 块扫描与样本帧偏移",
        "",
        "| frame | raw bytes | 正确解码 | 错误小端解释 | file offset | channel offsets |",
        "|---:|---|---|---|---:|---|",
    ]
    for row, sample in zip(report["sample_evidence"], caf["sample_frames"]):
        channels = ", ".join(
            f"{item['name']}={item['file_offset']}"
            for item in sample["offset"]["channels"]
        )
        lines.append(
            f"| {sample['frame']} | `{row['raw_hex']}` | {row['decoded']} | "
            f"{row['wrong_byte_order']} | {sample['offset']['file_offset']} | "
            f"{channels} |"
        )
    lines.extend(
        [
            "",
            "说明：样本帧偏移是文件内绝对字节偏移；首个样本帧位于 data 块 "
            "payload 的 edit-count 字段之后。",
            "",
            "## 大端标志错误提示",
            "",
            f"- 错误类型: `{report['invalid_endian_flag']['type']}`",
            f"- 错误信息: `{report['invalid_endian_flag']['message']}`",
            "",
            "## 四种时间基边界",
            "",
            "| timebase | boundary | frame | timecode | seconds |",
            "|---|---|---:|---|---:|",
        ]
    )
    for row in report["timebase_boundaries"]:
        lines.append(
            f"| {row['timebase']} | {row['name']} | {row['frame']} | "
            f"`{row['timecode']}` | {row['seconds']:.6f} |"
        )
    checks = report["drop_frame_checks"]
    lines.extend(
        [
            "",
            "## Drop-frame 规则",
            "",
            f"- 第一次跳号前: frame {checks['before_first_drop']['frame']} = "
            f"`{checks['before_first_drop']['timecode']}`",
            f"- 第一次跳号后: frame {checks['after_first_drop']['frame']} = "
            f"`{checks['after_first_drop']['timecode']}`",
            f"- 不存在的标签: `{', '.join(checks['invalid_labels'])}` (已拒绝)",
            f"- 十分钟边界: frame {checks['tenth_minute_boundary']['frame']} = "
            f"`{checks['tenth_minute_boundary']['timecode']}`",
            f"- 一小时边界: frame {checks['hour_boundary']['frame']} = "
            f"`{checks['hour_boundary']['timecode']}`",
            f"- 每小时丢弃 108 个标签；每天 {checks['frames_per_day']} 帧",
            "",
            "## 四类时间基转换边界",
            "",
            "| source | seconds | source frame | source TC | 24 | 25 | 30 | 30-drop |",
            "|---|---:|---:|---|---|---|---|---|",
        ]
    )
    for row in report["conversion_boundaries"]:
        targets = row["targets"]
        values = " | ".join(
            f"{targets[name]['frame']} `{targets[name]['timecode']}`"
            for name in ("24", "25", "30", "30-drop")
        )
        lines.append(
            f"| {row['source']} | {row['seconds']} | {row['source_frame']} | "
            f"`{row['source_timecode']}` | {values} |"
        )
    lines.extend(
        [
            "",
            "## 80 位 IEEE 扩展浮点参考",
            "",
            "| value | reference | encoded | decoded | match |",
            "|---:|---|---|---:|---|",
        ]
    )
    for row in report["extended_float_references"]:
        lines.append(
            f"| {row['value']:g} | `{row['reference_hex']}` | "
            f"`{row['encoded_hex']}` | {row['decoded']:g} | {row['match']} |"
        )
    lines.append("")
    return "\n".join(lines)
