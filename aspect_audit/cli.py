from __future__ import annotations

import argparse
import json
from pathlib import Path

from .compare import compare_all
from .config import AuditConfig
from .detect import analyze_video
from .generate import generate_materials
from .render import render_review
from .report import write_reports


def _config_from_args(args: argparse.Namespace) -> AuditConfig:
    defaults = AuditConfig()
    return AuditConfig(
        analysis_width=args.analysis_width,
        dark_level=args.dark_level,
        aspect_tolerance=args.aspect_tolerance,
        boundary_tolerance_px=args.boundary_tolerance,
        min_layout_frames=args.min_layout_frames,
        subtitle_min_bright_fraction=defaults.subtitle_min_bright_fraction,
    )


def _audit(
    video: str,
    output_dir: str,
    schedule: str | None,
    truth: str | None,
    review: str | None,
    cfg: AuditConfig,
    stem: str | None = None,
) -> dict:
    analysis = analyze_video(video, cfg)
    comparison = compare_all(
        analysis, cfg=cfg, schedule_path=schedule, truth_path=truth
    )
    stem = stem or Path(video).stem.replace("_master", "")
    paths = write_reports(output_dir, stem, analysis, comparison)
    review_path = None
    if review:
        review_path = render_review(video, analysis, review, comparison)
    summary = {
        "source": video,
        "stem": stem,
        "video": analysis["video"],
        "segments": [item.as_dict() for item in analysis["segments"]],
        "switches": analysis["switches"],
        "events": {
            kind: [item.as_dict() for item in events]
            for kind, events in analysis["events"].items()
        },
        "mismatches": comparison.get("mismatches", []),
        "truth_validation": comparison.get("truth_validation"),
        "reports": paths,
        "review_video": review_path,
    }
    return summary


def _command_generate(args: argparse.Namespace) -> int:
    manifest = generate_materials(args.output_dir)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


def _command_audit(args: argparse.Namespace) -> int:
    cfg = _config_from_args(args)
    summary = _audit(
        args.video, args.output_dir, args.schedule, args.truth,
        args.review, cfg, args.stem,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def _command_demo(args: argparse.Namespace) -> int:
    root = Path(args.output_dir)
    materials_dir = root / "materials"
    reports_dir = root / "reports"
    review_dir = root / "review"
    manifest = generate_materials(materials_dir)
    cfg = _config_from_args(args)
    results: dict = {"materials": manifest, "audits": {}}
    for name in ("long", "short"):
        clip = manifest["clips"][name]
        results["audits"][name] = _audit(
            clip["path"],
            str(reports_dir),
            clip["declared_schedule"],
            clip["truth"],
            str(review_dir / f"{name}_review.mp4"),
            cfg,
            stem=name,
        )
    summary_path = root / "audit_summary.json"
    summary_path.write_text(
        json.dumps(results, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    lines = [
        "# 画幅审计交付摘要",
        "",
        "| 版本 | 检测切换 | 真值误差 | 声明不一致 | 误报 | 审片视频 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for name, summary in results["audits"].items():
        truth = summary.get("truth_validation") or {}
        checks = truth.get("switch_checks", [])
        errors = ", ".join(
            str(item["error_frames"]) for item in checks
            if item["error_frames"] is not None
        ) or "—"
        false_positives = len(truth.get("false_positive_intervals", []))
        detected = ", ".join(
            f"F{item['frame']}" for item in summary["switches"]
        )
        lines.append(
            f"| {name} | {detected} | {errors} 帧 | "
            f"{len(summary['mismatches'])} 段 | {false_positives} 段 | "
            f"`{summary['review_video']}` |"
        )
    lines.extend(
        [
            "",
            "完整报告位于 `reports/`，逐帧结果位于各报告的 `_frames.csv`。",
            "",
        ]
    )
    (root / "audit_summary.md").write_text(
        "\n".join(lines), encoding="utf-8"
    )
    print(json.dumps(results["audits"], ensure_ascii=False, indent=2))
    return 0


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--analysis-width", type=int, default=480)
    parser.add_argument("--dark-level", type=int, default=28)
    parser.add_argument("--aspect-tolerance", type=float, default=0.035)
    parser.add_argument("--boundary-tolerance", type=int, default=8)
    parser.add_argument("--min-layout-frames", type=int, default=3)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="aspect-audit",
        description="TV master active-area and aspect-ratio audit",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    generate = subparsers.add_parser(
        "generate", help="generate long/short audit fixtures"
    )
    generate.add_argument(
        "--output-dir", default="artifacts/materials"
    )
    generate.set_defaults(handler=_command_generate)

    audit = subparsers.add_parser("audit", help="audit one local video")
    audit.add_argument("video")
    audit.add_argument("--output-dir", default="artifacts/reports")
    audit.add_argument("--schedule")
    audit.add_argument("--truth")
    audit.add_argument("--review")
    audit.add_argument("--stem")
    _add_common(audit)
    audit.set_defaults(handler=_command_audit)

    demo = subparsers.add_parser(
        "demo", help="generate fixtures, audit, compare, and render reviews"
    )
    demo.add_argument("--output-dir", default="artifacts")
    _add_common(demo)
    demo.set_defaults(handler=_command_demo)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.handler(args)
