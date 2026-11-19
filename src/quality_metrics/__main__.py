"""Command line entry point: ``python -m quality_metrics demo --out ...``."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time

from .demo_data import DemoSpec, encode_split_bitrate, generate_reference
from .pipeline import (
    analyse_alignment,
    analyse_boundary,
    analyse_high_noise,
    compute_frame_metrics,
    detect_noise_frames,
    list_reference_frames,
)
from .plotting import (
    plot_alignment_regions,
    plot_heatmaps,
    plot_high_noise,
    plot_metric_curves,
    plot_segment_means,
)
from .pssh import (
    CLEARKEY_SYSTEM_ID,
    DEFAULT_KID,
    WIDEVINE_SYSTEM_ID,
    build_pssh,
    bytes_to_kid,
    kid_to_bytes,
    parse_pssh,
    rewrite_pssh,
)
from .reporting import (
    measure_segment_bitrates,
    segment_means,
    write_csv,
    write_json,
    write_per_frame_csv,
)
from .report_writer import write_report

SAMPLE_FRAMES = (100, 120, 140)


def _run_pssh(out_dir: str) -> dict:
    """Build original/rewritten Widevine + ClearKey boxes and cenc initData."""
    old_kid = kid_to_bytes("00112233-4455-6677-8899-aabbccddeeff")
    new_kid = kid_to_bytes(DEFAULT_KID)

    def box(system_id: bytes, data: bytes) -> bytes:
        payload = (
            bytes([1]) + (0).to_bytes(3, "big") + system_id
            + (1).to_bytes(4, "big") + old_kid
            + len(data).to_bytes(4, "big") + data
        )
        return (8 + len(payload)).to_bytes(4, "big") + b"pssh" + payload

    # Minimal Widevine protobuf: field1 varint, field2 key_id, field3/4 strings.
    wv_data = (
        b"\x08\x01"
        + b"\x12\x10" + old_kid
        + b"\x1a\x0fquality-metrics"
        + b"\x22\x12frame-quality-demo"
    )
    wv_original = box(WIDEVINE_SYSTEM_ID, wv_data)
    ck_original = box(CLEARKEY_SYSTEM_ID, b"")

    wv_rewritten, wv_report = rewrite_pssh(wv_original)
    ck_rewritten, ck_report = rewrite_pssh(ck_original)
    init_data = wv_rewritten + ck_rewritten

    files = {
        "pssh_widevine_original.bin": wv_original,
        "pssh_widevine_rewritten.bin": wv_rewritten,
        "pssh_clearkey_original.bin": ck_original,
        "pssh_clearkey_rewritten.bin": ck_rewritten,
        "pssh_eme_initdata.bin": init_data,
    }
    for name, blob in files.items():
        with open(os.path.join(out_dir, name), "wb") as fh:
            fh.write(blob)

    report = {
        "default_kid": DEFAULT_KID,
        "widevine": {
            "original_box_kids": wv_report["original_box_kids"],
            "original_proto_kids": wv_report["original_proto_kids"],
            "rewritten_box_kids": wv_report["rewritten_box_kids"],
            "rewritten_proto_kids": wv_report["rewritten_proto_kids"],
            "preserved_fields": wv_report["preserved_fields"],
        },
        "clearkey": {
            "original_box_kids": ck_report["original_box_kids"],
            "rewritten_box_kids": ck_report["rewritten_box_kids"],
        },
        "checks": {
            "widevine_kids_rewritten": wv_report["rewritten_box_kids"]
            == [DEFAULT_KID]
            and wv_report["rewritten_proto_kids"] == [DEFAULT_KID],
            "clearkey_kid_rewritten": ck_report["rewritten_box_kids"]
            == [DEFAULT_KID],
        },
        "init_data_sha256": hashlib.sha256(init_data).hexdigest(),
    }
    write_json(os.path.join(out_dir, "pssh_report.json"), report)
    return report


def run_demo(out_dir: str, reuse: bool = False) -> dict:
    os.makedirs(out_dir, exist_ok=True)
    spec = DemoSpec()
    started = time.perf_counter()
    frame_dir = os.path.join(out_dir, "reference_frames")
    video_path = os.path.join(out_dir, "encoded.mp4")
    if not reuse or not os.path.isdir(frame_dir) or not os.path.exists(video_path):
        generate_reference(out_dir, spec)
        encode_split_bitrate(out_dir, spec)

    frame_paths = list_reference_frames(frame_dir)
    region_dir = os.path.join(out_dir, "region_maps")
    result = compute_frame_metrics(
        frame_paths, video_path, spec.fps, spec.boundary_frame,
        SAMPLE_FRAMES, region_dir,
    )
    rows = result["rows"]
    sample_frames = result["sample_frames"]

    noise_flags, noise_scores = detect_noise_frames(
        frame_paths, spec.boundary_frame
    )
    boundary = analyse_boundary(rows, spec.boundary_frame)
    high_noise = analyse_high_noise(
        rows, noise_flags, spec.boundary_frame
    )
    alignment = analyse_alignment(region_dir, sample_frames)
    bitrate = measure_segment_bitrates(
        video_path, spec.fps, spec.boundary_frame
    )
    segments = segment_means(rows, spec.boundary_frame)

    write_per_frame_csv(
        os.path.join(out_dir, "per_frame_metrics.csv"), rows, noise_flags
    )
    write_csv(os.path.join(out_dir, "boundary_report.csv"), boundary)
    write_csv(os.path.join(out_dir, "high_noise_comparison.csv"), high_noise)
    write_csv(os.path.join(out_dir, "alignment_report.csv"), alignment["details"])
    plot_metric_curves(
        rows, spec.boundary_frame, noise_flags, spec.fps,
        os.path.join(out_dir, "metrics_curves.png"),
    )
    heatmaps = plot_heatmaps(
        region_dir, os.path.join(out_dir, "heatmaps")
    )
    plot_alignment_regions(
        region_dir, sample_frames,
        os.path.join(out_dir, "alignment_regions.png"),
    )
    plot_high_noise(
        high_noise, os.path.join(out_dir, "high_noise_comparison.png")
    )
    plot_segment_means(
        segments, os.path.join(out_dir, "segment_means.png")
    )
    pssh = _run_pssh(out_dir)
    eme = _run_eme_validation(out_dir)

    summary = {
        "reference": os.path.abspath(frame_dir),
        "distorted": os.path.abspath(video_path),
        "frame_count": len(rows),
        "fps": float(spec.fps),
        "elapsed_seconds": time.perf_counter() - started,
        "acceleration": "OpenCV (GaussianBlur/resize/video IO); numpy vector math",
        "metric_definition": {
            "color": "BT.601 luma from BGR",
            "psnr": "global MSE; local map is log1p(11x11 Gaussian local MSE)",
            "ssim": "11x11 Gaussian window, C1=(0.01*255)^2, C2=(0.03*255)^2",
            "msssim": "five scales, weights 0.0448/0.2856/0.3001/0.2363/0.1333",
        },
        "segments": segments,
        "boundary_checks": boundary,
        "high_noise_comparison": high_noise,
        "alignment": alignment,
        "encoding": {
            "codec": "libx264", "preset": "medium", "pix_fmt": "yuv420p",
            "high_crf": spec.high_crf, "low_crf": spec.low_crf,
            "bitrate_check": bitrate,
        },
        "checks": {
            "all_metrics_computed": len(rows) == spec.frame_count,
            "boundary_clear": all(item["clear"] for item in boundary),
            "high_noise_compared": all(
                item["noise_frames"] > 0 for item in high_noise
            ),
            "regions_aligned": alignment["passed"],
            "pssh_kid_rewritten": all(pssh["checks"].values()),
            "eme_parsed": eme.get("passed", False),
            "eme_kid_matches": eme.get("result", {}).get(
                "matchesDefault", False
            ),
        },
        "artifacts": {
            "per_frame_csv": "per_frame_metrics.csv",
            "curves": "metrics_curves.png",
            "heatmaps": [os.path.relpath(path, out_dir) for path in heatmaps],
            "alignment_figure": "alignment_regions.png",
            "high_noise_figure": "high_noise_comparison.png",
        },
        "pssh": pssh,
        "eme": eme,
    }
    write_json(os.path.join(out_dir, "summary.json"), summary)
    write_report(summary, os.path.join(out_dir, "REPORT.md"))
    return summary


def _run_eme_validation(out_dir: str) -> dict:
    """Invoke the Playwright EME check; tolerate environments without a CDM."""
    script = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
        "scripts", "validate_eme.mjs",
    )
    project_root = os.path.dirname(os.path.dirname(script))
    report_path = os.path.join(out_dir, "eme_validation.json")
    command = [
        "node", script,
        "--pssh", os.path.join(out_dir, "pssh_eme_initdata.bin"),
        "--default-kid", DEFAULT_KID,
        "--out", report_path,
    ]
    completed = subprocess.run(
        command, capture_output=True, text=True, cwd=project_root
    )
    if completed.returncode != 0:
        return {
            "passed": False,
            "error": completed.stderr.strip() or completed.stdout.strip(),
        }
    with open(report_path) as fh:
        return json.load(fh)


def main() -> None:
    parser = argparse.ArgumentParser(description="Frame quality metrics demo")
    sub = parser.add_subparsers(dest="command", required=True)
    demo = sub.add_parser("demo")
    demo.add_argument("--out", required=True)
    demo.add_argument(
        "--reuse", action="store_true",
        help="reuse an existing encoded.mp4 / reference_frames if present",
    )
    args = parser.parse_args()
    if args.command == "demo":
        summary = run_demo(args.out, reuse=args.reuse)
        print(json.dumps(summary["checks"], indent=2))


if __name__ == "__main__":
    main()
