from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from .core import StabilizationConfig, stabilize_video
from .metrics import (
    border_zero_ratio,
    measure_video_motion,
    perspective_and_shear_error,
    solid_black_border_lines,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Feature-trajectory video stabilizer")
    parser.add_argument("input", help="input video")
    parser.add_argument("-o", "--output", required=True, help="stabilized video")
    parser.add_argument(
        "-m",
        "--matrices",
        default=None,
        help="matrix output prefix (creates .csv and .npz)",
    )
    parser.add_argument("--smoothing-sigma", type=float, default=6.0)
    parser.add_argument("--crop", type=float, default=0.88)
    parser.add_argument("--jitter-sigma", type=float, default=3.0)
    parser.add_argument("--no-measure", action="store_true")
    args = parser.parse_args()

    matrix_prefix = args.matrices or str(Path(args.output).with_suffix("")) + "_matrices"
    config = StabilizationConfig(
        smoothing_sigma=args.smoothing_sigma,
        target_crop_linear=args.crop,
    )
    result = stabilize_video(args.input, args.output, matrix_prefix, config)
    shear, perspective = perspective_and_shear_error(result.output_matrices)
    black_bands = solid_black_border_lines(result.output_path)
    report = {
        "input": args.input,
        "output": result.output_path,
        "matrices_csv": str(Path(matrix_prefix).with_suffix(".csv")),
        "matrices_npz": str(Path(matrix_prefix).with_suffix(".npz")),
        "frames": result.frame_count,
        "fps": result.fps,
        "crop_linear_ratio": result.crop_linear_ratio,
        "crop_area_ratio": result.crop_area_ratio,
        "crop_size": [result.crop_width, result.crop_height],
        "valid_mask_min": float(np.min(result.valid_mask_min)),
        "geometric_black_ratio_max": float(np.max(result.black_border_ratios)),
        "border_zero_pixel_ratio_max": border_zero_ratio(result.output_path),
        "solid_black_border_frames": int(black_bands["affected_frames"]),
        "no_black_border": bool(
            np.min(result.valid_mask_min) >= 255 and black_bands["affected_frames"] == 0
        ),
        "shear_error_max": shear,
        "perspective_error_max": perspective,
        "first_output_matrix": result.output_matrices[0].tolist(),
        "middle_output_matrix": result.output_matrices[result.frame_count // 2].tolist(),
    }
    if not args.no_measure:
        before = measure_video_motion(args.input, args.jitter_sigma)
        after = measure_video_motion(result.output_path, args.jitter_sigma)
        report["jitter"] = {
            "before_rms_px": before.jitter_rms_px,
            "after_rms_px": after.jitter_rms_px,
            "before_rms_deg": before.jitter_rms_deg,
            "after_rms_deg": after.jitter_rms_deg,
        }
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
