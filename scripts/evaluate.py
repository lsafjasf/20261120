#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from audio_utils import read_wav, write_json
from buffer_model import simulate_watermark_recovery
from evaluation import evaluate, public_results
from reporting import (
    plot_buffer,
    plot_music_spectrograms,
    plot_speech_spectrograms,
    render_report,
)
from samples import generate


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the denoising acceptance suite")
    parser.add_argument(
        "--regenerate",
        action="store_true",
        help="regenerate deterministic built-in samples before evaluating",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    samples_dir = ROOT / "samples"
    required = [
        samples_dir / "clean_speech.wav",
        samples_dir / "noisy_speech.wav",
        samples_dir / "clean_music.wav",
    ]
    if args.regenerate or not all(path.exists() for path in required):
        generate()
    if not (ROOT / "bin" / "webrtc_denoise").exists():
        print("Missing bin/webrtc_denoise; run scripts/setup.sh first.", file=sys.stderr)
        return 2

    artifacts = ROOT / "artifacts"
    figures = artifacts / "figures"
    results = evaluate()
    noisy = read_wav(samples_dir / "noisy_speech.wav")
    music = read_wav(samples_dir / "clean_music.wav")

    plot_speech_spectrograms(results, noisy, figures / "speech_spectrograms.png")
    plot_music_spectrograms(results, music, figures / "music_hf_spectrograms.png")
    buffer = simulate_watermark_recovery()
    plot_buffer(buffer, buffer["levels"], figures / "buffer_watermark.png")

    write_json(artifacts / "results.json", public_results(results))
    with (artifacts / "buffer_watermark.csv").open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["tick", "time_ms", "level_frames", "level_samples"])
        for tick, level in enumerate(buffer["levels"]):
            writer.writerow([tick, tick * 10.0, level, level * 480])
    (artifacts / "report.md").write_text(render_report(results), encoding="utf-8")

    checks = results["checks"]
    print(f"Overall: {'PASS' if checks['all'] else 'FAIL'}")
    for name, item in results["algorithms"].items():
        timing = item["timing"]
        print(
            f"{name:22s} SNR +{item['speech']['snr_improvement_db']:5.2f} dB  "
            f"HF {item['music']['hf_band_energy_change_db']:+5.2f} dB  "
            f"{timing['mean_us']:6.2f} us/frame  {timing['realtime_factor_x']:6.1f}x realtime"
        )
    print(f"Report: {artifacts / 'report.md'}")
    print(f"Results: {artifacts / 'results.json'}")
    return 0 if checks["all"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
