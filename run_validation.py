from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from video_stabilizer.core import StabilizationConfig, stabilize_video
from video_stabilizer.metrics import (
    border_zero_ratio,
    measure_video_motion,
    perspective_and_shear_error,
    region_line_metrics,
    solid_black_border_lines,
)
from video_stabilizer.synth import generate_synthetic_suite
from video_stabilizer.visualize import (
    before_after_still,
    comparison_video,
    fine_line_panel,
    jitter_plot,
    representative_index,
)


def _json_ready(value):
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, (np.floating, np.integer)):
        return value.item()
    if isinstance(value, dict):
        return {key: _json_ready(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_ready(item) for item in value]
    return value


def _case_summary(
    name: str, result, before, after, border_zero: float, black_bands: dict
) -> dict:
    shear, perspective = perspective_and_shear_error(result.output_matrices)
    return {
        "name": name,
        "input": result.metadata["input"],
        "output": result.output_path,
        "matrices_npz": result.matrices_path,
        "matrices_csv": str(Path(result.matrices_path).with_suffix(".csv")),
        "frames": result.frame_count,
        "fps": result.fps,
        "input_size": [result.width, result.height],
        "crop_size": [result.crop_width, result.crop_height],
        "output_size": [result.width, result.height],
        "crop_linear_ratio": result.crop_linear_ratio,
        "crop_area_ratio": result.crop_area_ratio,
        "crop_linear_percent": 100.0 * result.crop_linear_ratio,
        "crop_area_percent": 100.0 * result.crop_area_ratio,
        "all_frames_same_crop": bool(np.allclose(result.crop_linear_ratios, result.crop_linear_ratio)),
        "valid_mask_min": float(np.min(result.valid_mask_min)),
        "geometric_black_ratio_max": float(np.max(result.black_border_ratios)),
        "border_zero_pixel_ratio_max": border_zero,
        "solid_black_border_line_fraction_max": black_bands[
            "solid_black_line_fraction_max"
        ],
        "solid_black_border_frames": int(black_bands["affected_frames"]),
        "no_black_border": bool(
            np.min(result.valid_mask_min) >= 255
            and np.max(result.black_border_ratios) <= 0.0
            and black_bands["affected_frames"] == 0
        ),
        "jitter_before_rms_px": before.jitter_rms_px,
        "jitter_after_rms_px": after.jitter_rms_px,
        "jitter_reduction_percent_px": 100.0
        * (1.0 - after.jitter_rms_px / max(before.jitter_rms_px, 1e-12)),
        "jitter_before_rms_deg": before.jitter_rms_deg,
        "jitter_after_rms_deg": after.jitter_rms_deg,
        "jitter_reduction_percent_deg": 100.0
        * (1.0 - after.jitter_rms_deg / max(before.jitter_rms_deg, 1e-12)),
        "motion_before_rms_px": before.motion_rms_px,
        "motion_after_rms_px": after.motion_rms_px,
        "shear_error_max": shear,
        "perspective_error_max": perspective,
        "no_perspective_distortion": bool(shear <= 1e-9 and perspective <= 1e-9),
        "mean_tracked_points": result.metadata["mean_tracked_points"],
        "mean_inlier_ratio": result.metadata["mean_inlier_ratio"],
        "fallback_frames": result.metadata["fallback_frames"],
        "matrix_examples": {
            "first_output_matrix": result.output_matrices[0].tolist(),
            "middle_output_matrix": result.output_matrices[
                result.frame_count // 2
            ].tolist(),
            "middle_stabilization_matrix": result.stabilization_matrices[
                result.frame_count // 2
            ].tolist(),
        },
    }


def _report(summary: dict) -> str:
    lines = [
        "# 视频稳定验证报告",
        "",
        "## 算法",
        "",
        "- 特征点：Shi-Tomasi 角点 + 金字塔 LK 光流 + 前后向一致性检查。",
        "- 帧间运动：RANSAC 拟合 4 自由度相似变换（平移、旋转、等比缩放），不使用单应矩阵。",
        "- 轨迹平滑：累计中心位移、旋转角与缩放，用零相位高斯低通平滑；逐帧补偿到平滑轨迹。",
        "- 裁切：对全片所有帧求共同安全内接矩形，统一裁切后缩放到原分辨率；每帧用覆盖掩码确认无黑边。",
        "- 抖动指标：帧间中心位移/旋转角减去其高斯低通分量后的 RMS 残差，前后使用同一指标。",
        "",
        "## 定量结果",
        "",
        "| 素材 | 裁切(线性/面积) | 抖动 RMS 前 -> 后 (px) | 旋转 RMS 前 -> 后 (deg) | 黑边 | 透视/剪切误差 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for case in summary["cases"].values():
        lines.append(
            "| {name} | {linear:.2f}% / {area:.2f}% | {jb:.4f} -> {ja:.4f} | "
            "{rb:.4f} -> {ra:.4f} | {black} | {shear:.2e} / {perspective:.2e} |".format(
                name=case["name"],
                linear=case["crop_linear_percent"],
                area=case["crop_area_percent"],
                jb=case["jitter_before_rms_px"],
                ja=case["jitter_after_rms_px"],
                rb=case["jitter_before_rms_deg"],
                ra=case["jitter_after_rms_deg"],
                black="无" if case["no_black_border"] else "有",
                shear=case["shear_error_max"],
                perspective=case["perspective_error_max"],
            )
        )
    fine = summary["fine_line"]
    lines += [
        "",
        "## 结论",
        "",
        f"- 旋转素材：稳定后最大剪切误差 `{summary['cases']['rotation']['shear_error_max']:.3e}`，"
        f"最大透视误差 `{summary['cases']['rotation']['perspective_error_max']:.3e}`，无透视失真。",
        f"- 快速移动素材：几何掩码最小覆盖值 `{summary['cases']['fast_motion']['valid_mask_min']:.1f}`，"
        f"实心黑边行/列比例 `{summary['cases']['fast_motion']['solid_black_border_line_fraction_max']:.3e}`，无黑边。",
        f"- 细线素材：线区对比度 `{fine['before']['contrast_p99_p01']:.2f}` -> "
        f"`{fine['after']['contrast_p99_p01']:.2f}`（保留率 `{100.0 * fine['contrast_retention']:.2f}%`），"
        f"边缘像素比例 `{fine['before']['edge_pixel_ratio']:.4f}` -> `{fine['after']['edge_pixel_ratio']:.4f}`"
        f"（保留率 `{100.0 * fine['edge_retention']:.2f}%`），细线未被抹掉。",
        f"- 裁切比例按素材自适应：rotation `{summary['cases']['rotation']['crop_linear_percent']:.2f}%`，"
        f"fast_motion `{summary['cases']['fast_motion']['crop_linear_percent']:.2f}%`，"
        f"fine_lines `{summary['cases']['fine_lines']['crop_linear_percent']:.2f}%`（均为线性比例）；"
        "同片内所有帧使用同一裁切窗口，且均无黑边。",
        "",
        "## 输出文件",
        "",
        "- `videos/`：合成原片、稳定视频、左右对比视频。",
        "- `matrices/`：每帧 `motion`、`camera_pose`、`stabilization`、`output` 3x3 矩阵及裁切/覆盖字段（CSV/NPZ）。",
        "- `comparisons/`：稳定前后画面、抖动曲线、细线区域 3 倍局部对比。",
        "- `summary.json`：完整机器可读验证指标。",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="artifacts/validation")
    parser.add_argument("--smoothing-sigma", type=float, default=6.0)
    parser.add_argument("--crop", type=float, default=0.88)
    parser.add_argument("--jitter-sigma", type=float, default=3.0)
    args = parser.parse_args()

    root = Path(args.output)
    synthetic_dir = root / "synthetic"
    stabilized_dir = root / "videos"
    matrices_dir = root / "matrices"
    comparisons_dir = root / "comparisons"
    for directory in (stabilized_dir, matrices_dir, comparisons_dir):
        directory.mkdir(parents=True, exist_ok=True)

    manifest = generate_synthetic_suite(synthetic_dir)
    config = StabilizationConfig(
        smoothing_sigma=args.smoothing_sigma,
        target_crop_linear=args.crop,
    )
    cases = {}
    measurements = {}
    for name, info in manifest["videos"].items():
        print(f"[{name}] stabilizing {info['frames']} frames", flush=True)
        input_path = info["video"]
        output_path = stabilized_dir / f"{name}_stabilized.avi"
        matrices_path = matrices_dir / name
        result = stabilize_video(input_path, output_path, matrices_path, config)
        before = measure_video_motion(input_path, args.jitter_sigma)
        after = measure_video_motion(output_path, args.jitter_sigma)
        measurements[name] = (before, after)
        border_zero = border_zero_ratio(output_path)
        black_bands = solid_black_border_lines(output_path)
        case = _case_summary(name, result, before, after, border_zero, black_bands)
        if not case["no_black_border"]:
            raise AssertionError(f"{name}: black border detected")
        if case["jitter_after_rms_px"] >= case["jitter_before_rms_px"]:
            raise AssertionError(f"{name}: translation jitter did not decrease")
        cases[name] = case

        index = representative_index(before)
        before_after_still(
            input_path,
            output_path,
            comparisons_dir / f"{name}_before_after.png",
            index,
        )
        jitter_plot(
            before,
            after,
            comparisons_dir / f"{name}_jitter.png",
        )
        comparison_video(
            input_path,
            output_path,
            comparisons_dir / f"{name}_comparison.mp4",
        )

    rotation = cases["rotation"]
    if not rotation["no_perspective_distortion"]:
        raise AssertionError("rotation case has shear/perspective distortion")
    if not cases["fast_motion"]["no_black_border"]:
        raise AssertionError("fast-motion case has a black border")

    fine_before, _ = measurements["fine_lines"]
    fine_index = representative_index(fine_before)
    fine_roi = (180.0, 101.0, 280.0, 158.0)
    from video_stabilizer.core import _read_video

    before_frames, _, _, _ = _read_video(manifest["videos"]["fine_lines"]["video"])
    after_frames, _, _, _ = _read_video(cases["fine_lines"]["output"])
    before_metrics = region_line_metrics(
        before_frames[min(fine_index, len(before_frames) - 1)], fine_roi
    )
    after_metrics = region_line_metrics(
        after_frames[min(fine_index, len(after_frames) - 1)], fine_roi
    )
    contrast_retention = after_metrics["contrast_p99_p01"] / max(
        before_metrics["contrast_p99_p01"], 1e-12
    )
    edge_retention = after_metrics["edge_pixel_ratio"] / max(
        before_metrics["edge_pixel_ratio"], 1e-12
    )
    dark_retention = after_metrics["dark_pixel_ratio"] / max(
        before_metrics["dark_pixel_ratio"], 1e-12
    )
    fine = {
        "frame_index": fine_index,
        "roi_xywh": list(fine_roi),
        "before": before_metrics,
        "after": after_metrics,
        "contrast_retention": float(contrast_retention),
        "edge_retention": float(edge_retention),
        "dark_retention": float(dark_retention),
        "line_not_erased": bool(
            contrast_retention >= 0.95
            and edge_retention >= 0.95
            and dark_retention >= 0.95
        ),
    }
    if not fine["line_not_erased"]:
        raise AssertionError("fine lines lost too much contrast")
    fine_line_panel(
        manifest["videos"]["fine_lines"]["video"],
        cases["fine_lines"]["output"],
        comparisons_dir / "fine_line_region_comparison.png",
        fine_index,
        before_metrics,
        after_metrics,
    )

    summary = {
        "config": {
            "smoothing_sigma_frames": args.smoothing_sigma,
            "target_crop_linear": args.crop,
            "jitter_sigma_frames": args.jitter_sigma,
            "feature_estimator": "Shi-Tomasi + pyramidal LK + forward-backward check",
            "motion_model": "4-DOF similarity via RANSAC (no homography)",
            "interpolation": "Lanczos4",
        },
        "synthetic_manifest": manifest,
        "cases": cases,
        "fine_line": fine,
        "assertions": {
            "all_outputs_have_no_black_border": all(
                case["no_black_border"] for case in cases.values()
            ),
            "rotation_has_no_perspective_distortion": rotation[
                "no_perspective_distortion"
            ],
            "fast_motion_has_no_black_border": cases["fast_motion"][
                "no_black_border"
            ],
            "fine_lines_not_erased": fine["line_not_erased"],
        },
    }
    summary = _json_ready(summary)
    (root / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (root / "REPORT.md").write_text(_report(summary), encoding="utf-8")
    print(json.dumps(summary["cases"], indent=2, ensure_ascii=False), flush=True)
    print(f"wrote {root / 'summary.json'}", flush=True)


if __name__ == "__main__":
    main()
