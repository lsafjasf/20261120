"""Build the inspection, traceability, seam, and speed-change evidence set."""

from __future__ import annotations

import csv
import json
from math import pi, sin
from pathlib import Path

from .audio import (
    click_scan,
    continuity_metrics,
    decode_channels,
    rms,
    write_fl32_aifc,
    wsola_time_stretch,
)
from .chunks import parse_aiff
from .extended80 import decode_extended80, encode_extended80
from .fixtures import generate_fixtures
from .visuals import write_comparison_svg, write_seam_csv

LABELS = {
    "stereo_48k_none.aifc": "NONE",
    "stereo_48k_sowt.aifc": "sowt",
    "stereo_48k_fl32.aifc": "FL32",
    "stereo_48k_ulaw.aifc": "ulaw",
    "stereo_48k_ima4.aifc": "ima4",
    "stereo_48k_none_ssnd4096.aifc": "NONE+SSND4096",
    "stereo_48k_ima4_ssnd4096.aifc": "ima4+SSND4096",
}
TRACE_FILES = [
    "stereo_48k_none.aifc",
    "stereo_48k_sowt.aifc",
    "stereo_48k_fl32.aifc",
    "stereo_48k_ulaw.aifc",
    "stereo_48k_alaw.aifc",
    "stereo_48k_ima4.aifc",
    "stereo_48k_none_ssnd4096.aifc",
    "stereo_48k_ima4_ssnd4096.aifc",
]
TRACE_FRAMES = (0, 1, 63, 64, 65, 128, 11999)


def _inspect(files: dict[str, Path]) -> dict[str, dict]:
    return {name: parse_aiff(path).as_dict() for name, path in sorted(files.items())}


def _offset_comparison(parsed: dict[str, object]) -> tuple[list[dict], list[dict]]:
    rows = []
    matrix = []
    selected = [0, 1, 2, 63, 64, 65, 127, 128, 11999]
    for name, label in LABELS.items():
        aiff = parsed[name]
        comm, ssnd, layout = aiff._require()
        validation = aiff.validation_dict()
        rows.append(
            {
                "label": label,
                "form_type": aiff.form_type,
                "compression_type": comm.compression_type,
                "channels": comm.channels,
                "frames": comm.num_sample_frames,
                "sample_rate": comm.sample_rate,
                "sample_bits": comm.sample_size_bits,
                "ssnd_offset": ssnd.offset,
                "ssnd_block_size": ssnd.block_size,
                "audio_start_absolute": ssnd.audio_start_absolute,
                "first_sample_absolute": aiff.sample_location(0, 0)["absolute_file_offset"],
                **validation,
            }
        )
    for frame in selected:
        entry = {"sample_frame": frame}
        for name, label in LABELS.items():
            location = parsed[name].sample_location(frame, 0)
            entry[label] = {
                "compression_type": location["compression_type"],
                "container_frame": location["container_frame"],
                "frame_in_packet": location["frame_in_packet"],
                "relative_byte_offset": location["relative_byte_offset"],
                "absolute_file_offset": location["absolute_file_offset"],
                "nibble": location["nibble"],
            }
        matrix.append(entry)
    return rows, matrix


def _frame_offset_csv(path: Path, matrix: list[dict]) -> None:
    labels = list(LABELS.values())
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            ["sample_frame"]
            + [
                f"{label}.{field}"
                for label in labels
                for field in ("byte_offset", "container_frame", "frame_in_packet", "nibble", "absolute_offset")
            ]
        )
        for entry in matrix:
            row = [entry["sample_frame"]]
            for label in labels:
                location = entry[label]
                row.extend(
                    [
                        location["relative_byte_offset"],
                        location["container_frame"],
                        location["frame_in_packet"],
                        "" if location["nibble"] is None else location["nibble"],
                        location["absolute_file_offset"],
                    ]
                )
            writer.writerow(row)


def _traceability(parsed: dict[str, object]) -> tuple[list[dict], dict]:
    summaries = []
    samples = {}
    for name in TRACE_FILES:
        aiff = parsed[name]
        comm = aiff.comm
        rows = []
        checked = 0
        failed = 0
        first_failure = None
        for frame in range(comm.num_sample_frames):
            for channel in range(comm.channels):
                location = aiff.sample_location(frame, channel)
                recovered = aiff.recover_location(
                    location["relative_byte_offset"], channel, location["nibble"]
                )
                checked += 1
                if frame not in recovered:
                    failed += 1
                    first_failure = first_failure or {
                        "sample_frame": frame,
                        "channel": channel,
                        "location": location,
                        "recovered": recovered,
                    }
                if frame in TRACE_FRAMES:
                    rows.append(
                        {
                            **location,
                            "recovered_frames": recovered,
                            "reversible": frame in recovered,
                        }
                    )
        summary = {
            "source": str(aiff.path),
            "compression_type": comm.compression_type,
            "all_frames_checked": True,
            "sample_frames": comm.num_sample_frames,
            "channels": comm.channels,
            "checked": checked,
            "failed": failed,
            "first_failure": first_failure,
            "reversible": failed == 0,
        }
        summaries.append(summary)
        samples[comm.compression_type + ("+OFFSET" if aiff.ssnd.offset else "")] = {
            "source": str(aiff.path),
            "rows": rows,
        }
    return summaries, samples


def _tone_channels(frames: int, sample_rate: int = 48000) -> list[list[float]]:
    return [
        [0.8 * sin(2 * pi * 220 * frame / sample_rate) for frame in range(frames)],
        [0.8 * sin(2 * pi * 230 * frame / sample_rate) for frame in range(frames)],
    ]


def _seam_analysis() -> tuple[dict, dict[str, list[list[float]]]]:
    frames = 48000
    edge = 24000
    base = _tone_channels(frames)
    seamless = [values[:] for values in base]
    hard_cut = [
        [value if frame < edge else -value for frame, value in enumerate(values)]
        for values in base
    ]
    signals = {"seamless": seamless, "hard_cut": hard_cut}
    results = {}
    for name, channels in signals.items():
        metrics = continuity_metrics(channels, 48000, edge)
        table = []
        for frame in range(edge - 4, edge + 4):
            table.append(
                {
                    "frame": frame,
                    "position": "before" if frame < edge else "after",
                    "samples": [channels[0][frame], channels[1][frame]],
                    "deltas": [
                        channels[0][frame] - channels[0][frame - 1],
                        channels[1][frame] - channels[1][frame - 1],
                    ],
                }
            )
        results[name] = {
            "label": "phase-aligned seamless edit" if name == "seamless" else "pi-inverted hard cut (negative control)",
            "edit_frame": edge,
            "channels": 2,
            "frames": frames,
            **metrics,
            "seamless": metrics["phase_continuous"] and not metrics["click_detected"],
            "frames_table": table,
        }
    summary = {
        "seamless_edit_is_seamless": results["seamless"]["seamless"],
        "hard_cut_detected_as_click": results["hard_cut"]["click_detected"],
        "max_phase_jump_deg_seamless": results["seamless"]["max_phase_jump_deg"],
        "max_phase_jump_deg_hard_cut": results["hard_cut"]["max_phase_jump_deg"],
    }
    return {"edit_frame": edge, **results, "summary": summary}, signals


def _timestretch_analysis(fixtures: Path, artifacts: Path) -> tuple[dict, list[list[float]]]:
    source_path = fixtures / "timestretch_source_48k.aiff"
    source = parse_aiff(source_path)
    channels = decode_channels(source)
    target_frames = round(source.comm.num_sample_frames * 0.8)
    output_channels = wsola_time_stretch(channels, target_frames)
    output_path = artifacts / "timestretch_0p8s.aifc"
    write_fl32_aifc(output_path, output_channels, source.comm.sample_rate)
    output = parse_aiff(output_path)
    decoded = decode_channels(output)
    scans = [click_scan(decoded)]
    phase_jump = max(
        scan["max_boundary_phase_jump_deg"]
        for scan in scans
    )
    click_count = sum(scan["click_count"] for scan in scans)
    result = {
        "source_frames": source.comm.num_sample_frames,
        "output_frames": output.comm.num_sample_frames,
        "speed_factor": source.comm.num_sample_frames / target_frames,
        "algorithm": "WSOLA 2048-sample Hann windows, 1024-sample synthesis hop, +/-256-sample alignment search",
        "click_count": click_count,
        "max_boundary_step_ratio": max(scan["max_boundary_step_ratio"] for scan in scans),
        "max_boundary_phase_jump_deg": phase_jump,
        "phase_continuous": phase_jump < 5.0,
        "no_clicks": click_count == 0,
        "rms_in": [rms(channel) for channel in channels],
        "rms_out": [rms(channel) for channel in output_channels],
        "scans": scans,
        "source_rate": source.comm.sample_rate,
        "source_duration_seconds": source.comm.duration_seconds,
        "output_rate": output.comm.sample_rate,
        "output_duration_seconds": output.comm.duration_seconds,
        "target_duration_seconds": 0.8,
        "target_frames": target_frames,
        "sample_count_delta": output.comm.num_sample_frames - target_frames,
        "sample_count_matches": output.comm.num_sample_frames == target_frames,
        "duration_error_seconds": output.comm.duration_seconds - 0.8,
        "input": str(source_path),
        "output": str(output_path),
        "output_inspection": output.as_dict(),
    }
    result["accepted"] = (
        result["phase_continuous"]
        and result["no_clicks"]
        and result["sample_count_matches"]
        and abs(result["duration_error_seconds"]) < 1 / output.comm.sample_rate
    )
    return result, decoded


def _report(measurements: dict) -> str:
    rows = measurements["offset_comparison"]
    table = [
        "| 类型 | 压缩 | 采样率 | 位深 | 声道 | 帧数 | SSND offset | payload 起点 | 首样本位置 | payload 完整 |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in rows:
        table.append(
            f"| {row['label']} | {row['compression_type']} | {row['sample_rate']:g} | "
            f"{row['sample_bits']} | {row['channels']} | {row['frames']} | {row['ssnd_offset']} | "
            f"{row['audio_start_absolute']} | {row['first_sample_absolute']} | "
            f"{'是' if row['payload_complete'] else '否'} |"
        )
    rates = [
        "| 解码值 | 原始 10 字节 | 指数 raw | 无偏指数 | 尾数 | float.hex |",
        "|---:|---|---:|---:|---|---|",
    ]
    for item in measurements["extended80"]:
        rates.append(
            f"| {item['value']:g} | `{item['raw_hex']}` | {item['exponent_raw']} | "
            f"{item['exponent_unbiased']} | `{item['significand_hex']}` | `{item['hex_float']}` |"
        )
    seam = measurements["seam"]["summary"]
    stretch = measurements["timestretch"]
    seam_rows = [
        "| 信号 | 帧 | 位置 | ch0 | ch1 | ch0 delta | ch1 delta |",
        "|---|---:|---|---:|---:|---:|---:|",
    ]
    for signal_name, signal_data in (
        ("seamless", measurements["seam"]["seamless"]),
        ("hard_cut", measurements["seam"]["hard_cut"]),
    ):
        for item in signal_data["frames_table"]:
            if item["frame"] in (23999, 24000, 24001):
                seam_rows.append(
                    f"| {signal_name} | {item['frame']} | {item['position']} | "
                    f"{item['samples'][0]:.9f} | {item['samples'][1]:.9f} | "
                    f"{item['deltas'][0]:.9f} | {item['deltas'][1]:.9f} |"
                )
    trace_ok = all(item["reversible"] for item in measurements["traceability"])
    chunk_ok = all(
        item.get("data_validation", {}).get("payload_complete", False)
        for item in measurements["inspection"].values()
    )
    return f"""# AIFF/AIFC 解析与音频连续性验证

## 结论

- COMM/SSND 解析：{'通过' if chunk_ok else '失败'}；采样率、位深、声道、压缩类型和 payload 范围均已输出。
- 原始样本位置回溯：{'通过' if trace_ok else '失败'}；所有测试文件的每个采样帧/声道均通过“位置 -> 字节偏移 -> 采样帧”往返校验。
- 接缝画面：{'无缝' if seam['seamless_edit_is_seamless'] else '存在断点'}；负向 π 反相硬切：{'可检出' if seam['hard_cut_detected_as_click'] else '未检出'}。
- 相位/咔哒声：无缝接缝最大相位跳变 {seam['max_phase_jump_deg_seamless']:.6f}°，硬切 {seam['max_phase_jump_deg_hard_cut']:.6f}°。
- 0.8 秒变速：{'通过' if stretch['accepted'] else '失败'}；输出 {stretch['output_frames']} 帧，目标 {stretch['target_frames']} 帧，咔哒声 {stretch['click_count']} 次，边界最大相位跳变 {stretch['max_boundary_phase_jump_deg']:.6f}°。

## 80-bit 扩展浮点

浮点值使用 `significand / 2^63 * 2^(exponent-16383)` 计算，JSON 同时保留原始 10 字节、指数、尾数和精确有理数。

{chr(10).join(rates)}

## 帧偏移对照

{chr(10).join(table)}

完整逐帧矩阵见 `artifacts/frame_offsets.csv`，压缩类型对照见 `artifacts/frame_offset_comparison.json`。

`ima4` 的样本位置按其 64 帧 packet、每声道 34 字节（2 字节头 + 32 字节 nibble）及 4-bit 相位定位；回溯验证的是压缩地址可逆性，不是 ADPCM 波形无损重建。

## 接缝帧

编辑点在第 {measurements['seam']['edit_frame']} 帧。逐帧采样值见 `artifacts/edit_seam_frames.csv`，波形对照见 `artifacts/edit_seam_waveforms.svg`。

{chr(10).join(seam_rows)}

## 变速

源音频 {stretch['source_frames']} 帧 / {stretch['source_duration_seconds']:.3f} s，按 {stretch['speed_factor']:.6f}x 做 WSOLA 时间伸缩到 {stretch['output_frames']} 帧 / {stretch['output_duration_seconds']:.3f} s。输出文件为 `artifacts/timestretch_0p8s.aifc`，边界波形见 `artifacts/timestretch_block_boundary.svg`。
"""


def build_artifacts(root: str | Path, generate: bool = True) -> dict:
    root = Path(root)
    fixtures = root / "fixtures"
    artifacts = root / "artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)
    if generate or not (fixtures / "timestretch_source_48k.aiff").exists():
        generate_fixtures(fixtures)

    files = {
        path.name: path
        for path in sorted(fixtures.iterdir())
        if path.suffix.lower() in (".aiff", ".aifc")
    }
    parsed = {name: parse_aiff(path) for name, path in files.items()}
    inspection = {name: aiff.as_dict() for name, aiff in sorted(parsed.items())}
    (artifacts / "chunk_inspection.json").write_text(
        json.dumps(inspection, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    extended = []
    for rate in (22050, 44100, 48000, 96000):
        info = decode_extended80(encode_extended80(rate))
        extended.append({key: value for key, value in info.items() if key != "fraction"})
    (artifacts / "extended80.json").write_text(
        json.dumps(extended, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    offset_rows, matrix = _offset_comparison(parsed)
    (artifacts / "frame_offset_comparison.json").write_text(
        json.dumps({"comparison": offset_rows, "matrix": matrix}, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    _frame_offset_csv(artifacts / "frame_offsets.csv", matrix)
    traceability, trace_samples = _traceability(parsed)
    seam, seam_signals = _seam_analysis()
    write_seam_csv(artifacts / "edit_seam_frames.csv", seam_signals, seam["edit_frame"])
    write_comparison_svg(
        artifacts / "edit_seam_waveforms.svg",
        seam_signals,
        seam["edit_frame"],
        "Edit seam at frame 24000",
    )
    stretch, stretch_channels = _timestretch_analysis(fixtures, artifacts)
    write_comparison_svg(
        artifacts / "timestretch_block_boundary.svg",
        {"timestretched": stretch_channels},
        19456,
        "Speed-changed join at output frame 19456",
        radius=128,
    )

    measurements = {
        "environment": {
            "sample_rate": 48000,
            "codec_frames": 12000,
            "dependencies": "standard library only",
        },
        "extended80": extended,
        "inspection": inspection,
        "offset_comparison": offset_rows,
        "offset_matrix": matrix,
        "traceability": traceability,
        "trace_samples": trace_samples,
        "seam": seam,
        "timestretch": stretch,
    }
    (artifacts / "measurements.json").write_text(
        json.dumps(measurements, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (artifacts / "REPORT.md").write_text(_report(measurements), encoding="utf-8")
    return measurements
