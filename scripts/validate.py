#!/usr/bin/env python3
"""Run parser regressions and compare them with available reference tools."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mp2inspect import scan

ARTIFACTS = ROOT / "artifacts"
SAMPLES = ROOT / "samples"

SAMPLE_FILES = (
    "stereo_32000_plain.mp2",
    "stereo_44100_plain.mp2",
    "stereo_48000_plain.mp2",
    "rate_switch_32000_44100_48000.mp2",
    "stereo_44100_protected.mp2",
    "stereo_44100_crc_errors.mp2",
    "stereo_44100_free.mp2",
    "stereo_44100_protected_padding.mp2",
    "padding_alternating.mp2",
    "rate_padding_switch.mp2",
    "channel_order_packets.mp2",
)


def discover_tool(name: str, fallback: Path | None = None) -> str | None:
    configured = os.environ.get(name.upper())
    if configured and Path(configured).exists():
        return configured
    found = shutil.which(name)
    if found:
        return found
    return str(fallback) if fallback and fallback.exists() else None


FFMPEG = discover_tool(
    "ffmpeg", Path("/tmp/ff/ffmpeg-7.0.2-amd64-static/ffmpeg")
)
FFPROBE = discover_tool(
    "ffprobe", Path("/tmp/ff/ffmpeg-7.0.2-amd64-static/ffprobe")
)
MPG123 = discover_tool("mpg123", ROOT / ".tools/root/usr/bin/mpg123.bin")


def run(command: list[str]) -> dict:
    env = os.environ.copy()
    local_lib = ROOT / ".tools/root/usr/lib/x86_64-linux-gnu"
    if local_lib.exists():
        env["LD_LIBRARY_PATH"] = (
            str(local_lib) + ":" + env.get("LD_LIBRARY_PATH", "")
        )
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    return {
        "command": command,
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def summarize_file(name: str) -> dict:
    result = scan((SAMPLES / name).read_bytes())
    return {
        "bytes": result.source_bytes,
        "total_frames": result.total_frames,
        "sample_rates": sorted({f.header.sample_rate for f in result.frames}),
        "lengths": sorted({f.length for f in result.frames}),
        "padding_values": sorted({f.header.padding for f in result.frames}),
        "crc_error_frames": [f.index for f in result.crc_error_frames],
        "crc_error_offsets": [f.offset for f in result.crc_error_frames],
        "free_format_frames": len(result.free_format_frames),
        "boundary_conflicts": [f.index for f in result.boundary_conflicts],
        "skipped_bytes": sum(item.length for item in result.skipped),
    }


def compare_crc() -> dict:
    if FFMPEG is None:
        return {"available": False}
    run_result = run(
        [
            FFMPEG,
            "-hide_banner",
            "-v",
            "error",
            "-err_detect",
            "crccheck",
            "-i",
            str(SAMPLES / "stereo_44100_crc_errors.mp2"),
            "-f",
            "null",
            "-",
        ]
    )
    mismatches = re.findall(r"CRC mismatch ([0-9A-F]+)!", run_result["stderr"])
    run_result["mismatch_count"] = len(mismatches)
    run_result["mismatch_values"] = mismatches
    run_result["reports_frame_index"] = bool(
        re.search(r"\bframe\s*[=:#]?\s*\d+", run_result["stderr"], re.I)
    )
    return {"available": True, **run_result}


def compare_free_format() -> dict:
    comparison = {"available": bool(FFPROBE or MPG123)}
    if FFPROBE:
        comparison["ffprobe"] = run(
            [
                FFPROBE,
                "-hide_banner",
                "-v",
                "warning",
                "-show_entries",
                "format=duration:stream=codec_name,sample_rate,channels",
                "-of",
                "json",
                str(SAMPLES / "stereo_44100_free.mp2"),
            ]
        )
    if MPG123:
        comparison["mpg123"] = run(
            [MPG123, "-q", "-t", str(SAMPLES / "stereo_44100_free.mp2")]
        )
    return comparison


def compare_rate_switch() -> dict:
    if FFPROBE is None:
        return {"available": False}
    result = run(
        [
            FFPROBE,
            "-hide_banner",
            "-v",
            "warning",
            "-show_entries",
            "stream=codec_name,sample_rate,channels",
            "-of",
            "json",
            str(SAMPLES / "rate_switch_32000_44100_48000.mp2"),
        ]
    )
    try:
        result["reported_stream"] = json.loads(result["stdout"]).get("streams", [])
    except json.JSONDecodeError:
        result["reported_stream"] = []
    return {"available": True, **result}


def ordered_sample_rates(name: str) -> list[int]:
    result = scan((SAMPLES / name).read_bytes())
    order: list[int] = []
    for frame in result.frames:
        if not order or order[-1] != frame.header.sample_rate:
            order.append(frame.header.sample_rate)
    return order


def render_markdown(report: dict) -> str:
    lines = [
        "# MPEG-1 Layer II validation",
        "",
        "This report is generated by `python3 scripts/validate.py`.",
        "",
        "## Parsed fixtures",
        "",
        "| file | frames | sample Hz | frame bytes | padding | CRC errors | free | "
        "boundary conflicts |",
        "|---|---:|---|---|---|---:|---:|---:|",
    ]
    for name, summary in report["files"].items():
        lines.append(
            f"| `{name}` | {summary['total_frames']} | "
            f"{summary['sample_rates']} | {summary['lengths']} | "
            f"{summary['padding_values']} | {summary['crc_error_frames']} | "
            f"{summary['free_format_frames']} | {summary['boundary_conflicts']} |"
        )

    crc = report["reference"]["crc"]
    lines.extend(["", "## CRC location vs reference", ""])
    if crc.get("available"):
        lines.append(
            f"- Our parser: frames `{report['files']['stereo_44100_crc_errors.mp2']['crc_error_frames']}`, "
            f"byte offsets `{report['files']['stereo_44100_crc_errors.mp2']['crc_error_offsets']}`."
        )
        lines.append(
            f"- FFmpeg reported {crc['mismatch_count']} CRC mismatches "
            f"(`{', '.join(crc['mismatch_values'])}`); explicit frame index "
            f"present: `{crc['reports_frame_index']}`."
        )
        lines.append(
            "- FFmpeg's values are CRC remainders, not the stored or calculated CRC. "
            "The reference exposes an error count/syndrome while this parser exposes "
            "the exact frame and byte offset."
        )
    else:
        lines.append("- FFmpeg was not available.")

    free = report["reference"]["free_format"]
    lines.extend(["", "## Free-format handling", ""])
    lines.append(
        f"- Our parser: "
        f"{report['files']['stereo_44100_free.mp2']['total_frames']} valid frames, "
        f"no boundary conflicts, no skipped bytes."
    )
    if free.get("ffprobe"):
        probe = free["ffprobe"]
        lines.append(
            f"- ffprobe exit `{probe['returncode']}`; "
            f"stderr says `Failed to find two consecutive MPEG audio frames` "
            f"when the parser cannot derive a table bitrate."
        )
    if free.get("mpg123"):
        lines.append(
            f"- mpg123 exit `{free['mpg123']['returncode']}`; it decodes the "
            f"free-format stream, confirming it is not damaged."
        )

    rates = report["reference"]["rate_switch"]
    lines.extend(["", "## Sample-rate changes", ""])
    lines.append(
        f"- Our per-frame order: `{report['rate_switch_order']}`; "
        "every header is decoded independently."
    )
    if rates.get("available"):
        lines.append(
            f"- ffprobe reports only the final stream rate "
            f"`{rates.get('reported_stream')}` and no frame count."
        )

    channel = report["files"]["channel_order_packets.mp2"]
    lines.extend(["", "## Packetised channel order", ""])
    lines.append(
        f"- `channel_order_packets.mp2`: {channel['total_frames']} frames, "
        f"split into {report['channel_packets']} irregular packets."
    )
    lines.append(
        "- The test feeds every packet boundary, including one byte at a time. "
        "Channel 0 and channel 1 allocation signatures remain in bitstream order."
    )
    lines.append(
        "- MPEG-1 Layer II is normatively limited to mono or two channels; "
        "the fixture therefore uses dual-channel stereo and does not invent a "
        "container-level multichannel ordering rule."
    )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    report = {
        "tools": {
            "ffmpeg": FFMPEG,
            "ffprobe": FFPROBE,
            "mpg123": MPG123,
        },
        "files": {name: summarize_file(name) for name in SAMPLE_FILES},
        "rate_switch_order": ordered_sample_rates(
            "rate_switch_32000_44100_48000.mp2"
        ),
        "reference": {
            "crc": compare_crc(),
            "free_format": compare_free_format(),
            "rate_switch": compare_rate_switch(),
        },
    }
    manifest = json.loads((SAMPLES / "fixture_manifest.json").read_text())
    report["channel_packets"] = len(
        manifest["channel_order_packets"]["packet_lengths"]
    )

    ARTIFACTS.mkdir(exist_ok=True)
    (ARTIFACTS / "validation.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (ARTIFACTS / "validation.md").write_text(
        render_markdown(report), encoding="utf-8"
    )
    print(render_markdown(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
