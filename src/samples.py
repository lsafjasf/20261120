from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from scipy.signal import butter, sosfilt

from audio_utils import (
    SAMPLE_RATE,
    integrated_loudness,
    normalize_loudness,
    rms,
    snr_db,
    write_wav,
)

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "samples"
SEED_SPEECH = 20261120
SEED_NOISE = 20261121
SEED_MUSIC = 20261122


def smooth_gate(t: np.ndarray, start: float, stop: float, edge: float = 0.015) -> np.ndarray:
    return 0.5 * (np.tanh((t - start) / edge) - np.tanh((t - stop) / edge))


def synth_speech(duration: float = 6.0) -> np.ndarray:
    rng = np.random.default_rng(SEED_SPEECH)
    t = np.arange(int(duration * SAMPLE_RATE), dtype=np.float64) / SAMPLE_RATE
    f0 = 118.0 + 18.0 * np.sin(2.0 * np.pi * 0.61 * t)
    f0 += 7.0 * np.sin(2.0 * np.pi * 2.3 * t + 0.4)
    phase = 2.0 * np.pi * np.cumsum(f0) / SAMPLE_RATE
    voiced = np.zeros_like(t)
    for harmonic in range(1, 45):
        frequency = harmonic * f0
        formants = (
            1.0 * np.exp(-((frequency - 520.0) / 130.0) ** 2)
            + 0.55 * np.exp(-((frequency - 1_480.0) / 220.0) ** 2)
            + 0.30 * np.exp(-((frequency - 2_650.0) / 360.0) ** 2)
        )
        weight = (0.10 + formants) / harmonic ** 0.82
        voiced += weight * np.sin(harmonic * phase)

    syllables = [
        (0.50, 0.84), (0.92, 1.23), (1.31, 1.72),
        (1.92, 2.20), (2.28, 2.68),
        (3.02, 3.30), (3.37, 3.77),
        (4.05, 4.41), (4.49, 4.86),
        (5.13, 5.50), (5.57, 5.84),
    ]
    gate = sum(smooth_gate(t, start, stop) for start, stop in syllables)
    gate *= 0.68 + 0.32 * np.sin(2.0 * np.pi * 4.2 * t) ** 2
    voiced *= gate
    voiced += 0.018 * rng.standard_normal(len(t)) * gate

    hiss = butter(4, [3_200.0, 6_800.0], btype="bandpass", fs=SAMPLE_RATE, output="sos")
    fricatives = np.zeros_like(t)
    for start, stop in [(0.84, 0.92), (1.23, 1.31), (2.68, 2.78), (3.77, 3.88), (4.86, 4.96)]:
        window = smooth_gate(t, start, stop, edge=0.008)
        fricatives += sosfilt(hiss, rng.standard_normal(len(t))) * window
    voiced += 0.10 * fricatives / max(rms(fricatives), 1e-12) * rms(voiced[gate > 0.2])
    return voiced / max(np.max(np.abs(voiced)), 1e-12) * 0.55


def synth_noise(duration: float = 6.0) -> np.ndarray:
    rng = np.random.default_rng(SEED_NOISE)
    t = np.arange(int(duration * SAMPLE_RATE), dtype=np.float64) / SAMPLE_RATE
    white = rng.standard_normal(len(t))
    pink = sosfilt(butter(3, 4_500.0, btype="lowpass", fs=SAMPLE_RATE, output="sos"), white)
    pink /= max(rms(pink), 1e-12)
    hum = sum(np.sin(2.0 * np.pi * 50.0 * h * t) / h for h in range(1, 5))
    hiss = sosfilt(
        butter(3, 6_500.0, btype="highpass", fs=SAMPLE_RATE, output="sos"),
        rng.standard_normal(len(t)),
    )
    noise = 0.70 * pink + 0.12 * hum / max(rms(hum), 1e-12)
    noise += 0.08 * hiss / max(rms(hiss), 1e-12)
    return noise / max(rms(noise), 1e-12)


def synth_music(duration: float = 7.0) -> np.ndarray:
    rng = np.random.default_rng(SEED_MUSIC)
    t = np.arange(int(duration * SAMPLE_RATE), dtype=np.float64) / SAMPLE_RATE
    music = np.zeros_like(t)

    chords = [
        (0.00, 220.00, [1.0, 1.26, 1.50, 2.00]),
        (1.75, 246.94, [1.0, 1.26, 1.50, 2.00]),
        (3.50, 196.00, [1.0, 1.26, 1.50, 2.00]),
        (5.25, 220.00, [1.0, 1.26, 1.50, 2.00]),
    ]
    for start, root, ratios in chords:
        local = np.clip(t - start, 0.0, None)
        envelope = np.where(t >= start, np.minimum(1.0, local / 0.08), 0.0)
        envelope *= np.exp(-0.30 * local)
        for note_index, ratio in enumerate(ratios):
            frequency = root * ratio
            for harmonic in range(1, 18):
                if frequency * harmonic >= 7_000.0:
                    break
                weight = 0.030 / harmonic ** 0.9
                music += weight * envelope * np.sin(
                    2.0 * np.pi * frequency * harmonic * t + 0.17 * note_index
                )

    melody = [(220.0, 0.55), (329.63, 0.55), (440.0, 0.55), (493.88, 0.55)]
    for index in range(12):
        start = 0.15 + 0.55 * index
        frequency = melody[index % len(melody)][0] * (2.0 if index % 4 == 3 else 1.0)
        local = t - start
        envelope = np.where((local >= 0.0) & (local < 0.52), np.exp(-4.0 * np.maximum(local, 0.0)), 0.0)
        music += 0.055 * envelope * np.sin(2.0 * np.pi * frequency * t)

    for index, frequency in enumerate([9_000.0, 11_200.0, 13_400.0, 15_800.0]):
        vibrato = np.sin(2.0 * np.pi * (5.0 + index) * t)
        envelope = 0.65 + 0.35 * np.sin(2.0 * np.pi * (0.20 + 0.03 * index) * t + index)
        music += (0.0045 + 0.0015 * index) * envelope * np.sin(
            2.0 * np.pi * frequency * t + 0.02 * vibrato
        )

    cymbal_filter = butter(4, [6_000.0, 20_000.0], btype="bandpass", fs=SAMPLE_RATE, output="sos")
    cymbal_source = sosfilt(cymbal_filter, rng.standard_normal(len(t)))
    cymbal_source *= 0.042 / max(np.max(np.abs(cymbal_source)), 1e-12)
    for start in np.arange(0.22, duration, 0.74):
        local = t - start
        envelope = np.where(local >= 0.0, np.exp(-28.0 * local), 0.0)
        music += cymbal_source * envelope
    music /= max(np.max(np.abs(music)), 1e-12)
    music *= np.clip((t - 0.30) / 0.10, 0.0, 1.0)
    return music * 0.72


def generate() -> dict:
    SAMPLES.mkdir(parents=True, exist_ok=True)
    clean = synth_speech()
    noise = synth_noise()
    target_snr = 5.0
    noise_gain = rms(clean) / (rms(noise) * 10.0 ** (target_snr / 20.0))
    noisy = clean + noise_gain * noise
    raw_lufs = integrated_loudness(noisy)
    common_gain = 10.0 ** ((-23.0 - raw_lufs) / 20.0)
    clean = clean * common_gain
    noise = noise * noise_gain * common_gain
    noisy = clean + noise
    noisy_lufs = integrated_loudness(noisy)

    music = synth_music()
    music, _, _ = normalize_loudness(music, -23.0)
    write_wav(SAMPLES / "clean_speech.wav", clean)
    write_wav(SAMPLES / "noise.wav", noise)
    write_wav(SAMPLES / "noisy_speech.wav", noisy)
    write_wav(SAMPLES / "clean_music.wav", music)

    manifest = {
        "sample_rate_hz": SAMPLE_RATE,
        "frame_samples": 480,
        "frame_ms": 10.0,
        "seeds": {"speech": SEED_SPEECH, "noise": SEED_NOISE, "music": SEED_MUSIC},
        "speech": {
            "duration_s": len(clean) / SAMPLE_RATE,
            "noise_only_lead_in_s": 0.45,
            "mix_target_snr_db": target_snr,
            "measured_input_snr_db": snr_db(clean, noisy),
            "loudness_lufs": noisy_lufs,
            "files": ["clean_speech.wav", "noise.wav", "noisy_speech.wav"],
        },
        "music": {
            "duration_s": len(music) / SAMPLE_RATE,
            "contains_speech": False,
            "calibration_lead_in_s": 0.30,
            "high_frequency_components_hz": [9000, 11200, 13400, 15800],
            "percussive_hf_band_hz": [6000, 20000],
            "loudness_lufs": integrated_loudness(music),
            "files": ["clean_music.wav"],
        },
    }
    with (SAMPLES / "manifest.json").open("w", encoding="utf-8") as handle:
        json.dump(manifest, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    return manifest


if __name__ == "__main__":
    print(json.dumps(generate(), ensure_ascii=False, indent=2))
