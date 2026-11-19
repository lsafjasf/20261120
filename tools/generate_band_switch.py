"""Generate smooth and hard-switch narrowband/wideband WAV fixtures."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vvc_analysis.audio import (
    analyze_band_switch,
    synthesize,
    write_spectrum_csv,
    write_spectrum_svg,
    write_wav,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=Path("samples/audio"))
    parser.add_argument("--duration", type=float, default=4.95)
    parser.add_argument("--segment-duration", type=float, default=0.45)
    parser.add_argument("--transition-ms", type=float, default=120.0)
    args = parser.parse_args()

    smooth_path = args.output_dir / "band_switch_smooth.wav"
    hard_path = args.output_dir / "band_switch_hard_control.wav"
    smooth = synthesize(
        args.duration, args.segment_duration, args.transition_ms, smooth=True
    )
    hard = synthesize(
        args.duration, args.segment_duration, args.transition_ms, smooth=False
    )
    write_wav(smooth_path, smooth)
    write_wav(hard_path, hard)

    result = analyze_band_switch(
        smooth_path,
        args.segment_duration,
        args.transition_ms,
        hard_control_path=hard_path,
    )
    frames = result.pop("frames")
    result.pop("samples")
    write_spectrum_csv(frames, args.output_dir / "band_switch_spectrum.csv")
    write_spectrum_svg(frames, args.output_dir / "band_switch_spectrum.svg", args.duration)
    (args.output_dir / "band_switch_metrics.json").write_text(
        json.dumps(result, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
