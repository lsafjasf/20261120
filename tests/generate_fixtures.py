#!/usr/bin/env python3
"""Generate deterministic ABX validation pairs (PCM16 WAV)."""

from __future__ import annotations

import argparse
import json
import math
import struct
import wave
from pathlib import Path


def synthesise(duration: float, sample_rate: int) -> list[float]:
    count = round(duration * sample_rate)
    samples: list[float] = []
    for index in range(count):
        time = index / sample_rate
        envelope = min(1.0, time / 0.03, (duration - time) / 0.08)
        envelope = max(0.0, envelope)
        chirp_phase = 2.0 * math.pi * (180.0 * time + 22.0 * time * time)
        value = (
            0.38 * math.sin(2.0 * math.pi * 220.0 * time)
            + 0.22 * math.sin(2.0 * math.pi * 440.0 * time)
            + 0.12 * math.sin(2.0 * math.pi * 880.0 * time)
            + 0.16 * math.sin(chirp_phase)
        )
        # A deterministic, non-musical texture prevents one frequency from
        # dominating the correlation peak.
        value += 0.035 * math.sin(2.0 * math.pi * 37.0 * time)
        samples.append(max(-0.98, min(0.98, value * envelope)))
    return samples


def write_wav(path: Path, samples: list[float], sample_rate: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        payload = bytearray()
        for sample in samples:
            value = round(max(-1.0, min(1.0, sample)) * 32767.0)
            payload += struct.pack("<h", value)
        output.writeframes(payload)


def delayed(samples: list[float], delay: int) -> list[float]:
    return [0.0] * delay + samples


def local_distortion(samples: list[float], sample_rate: int) -> list[float]:
    output = list(samples)
    start = round(1.20 * sample_rate)
    end = round(1.40 * sample_rate)
    for index in range(start, end):
        value = samples[index]
        crushed = round(value * 24.0) / 24.0
        output[index] = max(-0.82, min(0.82, crushed * 0.92))
    return output


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", nargs="?", default=str(Path(__file__).parent / "fixtures"))
    args = parser.parse_args()

    root = Path(args.output)
    sample_rate = 48_000
    master = synthesise(3.0, sample_rate)
    manifest = {
        "sampleRate": sample_rate,
        "cases": [
            {
                "name": "known_delay",
                "master": "case_delay_master.wav",
                "encoded": "case_delay_encoded.wav",
                "expectedLagSamples": 733,
                "expectedCommonFrames": len(master),
            },
            {
                "name": "identical_content",
                "master": "case_same_master.wav",
                "encoded": "case_same_encoded.wav",
                "expectedLagSamples": 0,
                "expectedCommonFrames": len(master),
            },
            {
                "name": "local_distortion",
                "master": "case_distortion_master.wav",
                "encoded": "case_distortion_encoded.wav",
                "expectedLagSamples": 0,
                "expectedCommonFrames": len(master),
                "distortionStart": 1.20,
                "distortionEnd": 1.40,
            },
        ],
    }

    write_wav(root / "case_delay_master.wav", master, sample_rate)
    write_wav(root / "case_delay_encoded.wav", delayed(master, 733), sample_rate)
    write_wav(root / "case_same_master.wav", master, sample_rate)
    write_wav(root / "case_same_encoded.wav", master, sample_rate)
    write_wav(root / "case_distortion_master.wav", master, sample_rate)
    write_wav(root / "case_distortion_encoded.wav",
              local_distortion(master, sample_rate), sample_rate)

    (root / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n",
                                        encoding="utf-8")


if __name__ == "__main__":
    main()
