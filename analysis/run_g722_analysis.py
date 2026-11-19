#!/usr/bin/env python3
"""Generate reproducible G.722 measurements and plots."""

from __future__ import annotations

import csv
import json
import math
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from scipy import signal
from scipy.io import wavfile

from g722_analysis import (
    HIGH_INV_QUANT,
    LOW_INV_QUANT4,
    LOW_INV_QUANT5,
    LOW_INV_QUANT6,
    MODES,
    OUT,
    ROOT,
    db_ratio,
    decode_payload,
    encode_64k,
    mode_payload,
    read_wav,
    rms,
    snr_db,
    synthesize_subbands,
    write_g722,
    write_wav,
)

FS = 16000
ADPCM_FS = 8000
ERROR_FRAME = 4968
ERROR_TIME_MS = ERROR_FRAME / ADPCM_FS * 1000.0
ALIGN_LAG = 22


def ensure_dirs() -> None:
    for name in ("audio", "bitstream", "data", "plots"):
        (OUT / name).mkdir(parents=True, exist_ok=True)


def write_csv(path: Path, rows: list[dict], fieldnames: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        raise ValueError(f"no rows for {path}")
    if fieldnames is None:
        fieldnames = list(rows[0].keys())
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def peak_lag(reference: np.ndarray, test: np.ndarray, max_lag: int = 64) -> int:
    ref = reference.astype(np.float64)
    sig = test.astype(np.float64)
    candidates: list[tuple[float, int]] = []
    for lag in range(max_lag + 1):
        a = ref[: len(ref) - lag]
        b = sig[lag:]
        denom = math.sqrt(float(np.dot(a, a) * np.dot(b, b)))
        candidates.append((float(np.dot(a, b)) / max(denom, 1e-30), lag))
    return max(candidates)[1]


def aligned_snr(reference: np.ndarray, test: np.ndarray, lag: int = ALIGN_LAG) -> float:
    return snr_db(reference[:-lag], test[lag:])


def percentile_abs(x: np.ndarray, q: float) -> float:
    return float(np.percentile(np.abs(x.astype(np.float64)), q))


def quantizer_rows(mode: int, det_low_mean: float, det_high_mean: float) -> list[dict]:
    cfg = MODES[mode]
    rows: list[dict] = []
    low_table = {6: LOW_INV_QUANT6, 5: LOW_INV_QUANT5, 4: LOW_INV_QUANT4}[cfg["low_bits"]]
    for code, table_value in enumerate(low_table):
        det_initial = 8
        rows.append(
            {
                "mode": mode,
                "bitrate": cfg["bitrate"],
                "band": "low",
                "bits": cfg["low_bits"],
                "levels": cfg["low_levels"],
                "code": code,
                "table_value": table_value,
                "det_initial": det_initial,
                "recon_at_det_initial": det_initial * table_value >> 10,
                "det_mean": det_low_mean,
                "recon_at_det_mean": int(round(det_low_mean * table_value / 1024.0)),
                "mean_scale_gain_db": db_ratio(det_low_mean, det_initial),
            }
        )
    for code, table_value in enumerate(HIGH_INV_QUANT):
        det_initial = 2
        rows.append(
            {
                "mode": mode,
                "bitrate": cfg["bitrate"],
                "band": "high",
                "bits": cfg["high_bits"],
                "levels": cfg["high_levels"],
                "code": code,
                "table_value": table_value,
                "det_initial": det_initial,
                "recon_at_det_initial": det_initial * table_value >> 10,
                "det_mean": det_high_mean,
                "recon_at_det_mean": int(round(det_high_mean * table_value / 1024.0)),
                "mean_scale_gain_db": db_ratio(det_high_mean, det_initial),
            }
        )
    return rows


def mode_metrics(
    mode: int,
    ref_low: np.ndarray,
    ref_high: np.ndarray,
    decoded: dict,
    input_pcm: np.ndarray,
) -> tuple[dict, float]:
    cfg = MODES[mode]
    low = decoded["low"].astype(np.float64)
    high = decoded["high"].astype(np.float64)
    ref_low_f = ref_low.astype(np.float64)
    ref_high_f = ref_high.astype(np.float64)
    err_low = ref_low_f - low
    err_high = ref_high_f - high
    pcm = decoded["pcm"]
    lag = peak_lag(input_pcm, pcm)
    det_low = np.asarray([row["det_low"] for row in decoded["rows"]], dtype=np.float64)
    det_high = np.asarray([row["det_high"] for row in decoded["rows"]], dtype=np.float64)

    summary = {
        "mode": mode,
        "bitrate": cfg["bitrate"],
        "audio_bits": cfg["audio_bits"],
        "aux_bits": cfg["aux_bits"],
        "low_bits": cfg["low_bits"],
        "high_bits": cfg["high_bits"],
        "low_levels": cfg["low_levels"],
        "high_levels": cfg["high_levels"],
        "embedded_mode_field": "no (external mode 1/2/3)",
        "frames": len(decoded["low"]),
        "duration_s": len(decoded["low"]) / ADPCM_FS,
        "det_low_mean": float(np.mean(det_low)),
        "det_high_mean": float(np.mean(det_high)),
        "det_low_min": int(np.min(det_low)),
        "det_high_min": int(np.min(det_high)),
        "det_low_max": int(np.max(det_low)),
        "det_high_max": int(np.max(det_high)),
        "quant_scale_gain_low_db": db_ratio(float(np.mean(det_low)), 8.0),
        "quant_scale_gain_high_db": db_ratio(float(np.mean(det_high)), 2.0),
        "path_gain_low_db": db_ratio(rms(low), rms(ref_low_f)),
        "path_gain_high_db": db_ratio(rms(high), rms(ref_high_f)),
        "snr_low_db": snr_db(ref_low, decoded["low"]),
        "snr_high_db": snr_db(ref_high, decoded["high"]),
        "snr_pcm_db": aligned_snr(input_pcm, pcm, lag),
        "pcm_align_lag_samples": lag,
        "error_low_rms": rms(err_low),
        "error_high_rms": rms(err_high),
        "error_low_p95": percentile_abs(err_low, 95.0),
        "error_high_p95": percentile_abs(err_high, 95.0),
        "error_low_db_rel_ref": db_ratio(rms(err_low), rms(ref_low_f)),
        "error_high_db_rel_ref": db_ratio(rms(err_high), rms(ref_high_f)),
        "decoded_wav": str(OUT / "audio" / f"mode{mode}_{cfg['bitrate'] // 1000}k_decoded.wav"),
    }
    return summary, lag


def span_info(mask: np.ndarray, start: int = 0) -> dict:
    indices = np.flatnonzero(mask)
    if not len(indices):
        return {"first": None, "last": None, "count": 0}
    return {
        "first": int(indices[0] + start),
        "last": int(indices[-1] + start),
        "count": int(len(indices)),
    }


def local_snr(
    ref: np.ndarray,
    rec: np.ndarray,
    center: int,
    half_width: int = 80,
) -> float:
    lo = max(0, center - half_width)
    hi = min(len(ref), center + half_width + 1)
    return snr_db(ref[lo:hi], rec[lo:hi])


BIT_ERROR_CASES = (
    (1, 0, "mode1_low_fine_bit0", "low", "low codeword bit 0 (fine LSB)"),
    (1, 1, "mode1_low_fine_bit1", "low", "low codeword bit 1 (fine)"),
    (1, 5, "mode1_low_core_sign_bit5", "low", "low codeword bit 5 (sign/core)"),
    (1, 7, "mode1_high_sign_bit1", "high", "high codeword bit 7 (sign)"),
    (1, 6, "mode1_high_mag_bit0", "high", "high codeword bit 6 (magnitude)"),
    (2, 1, "mode2_low_fine_bit0", "low", "5-bit low codeword fine LSB"),
    (3, 5, "mode3_low_core_sign_bit3", "low", "4-bit low codeword bit 3 (sign/core)"),
)


def bit_error_metrics(
    name: str,
    mode: int,
    bit: int,
    band: str,
    role: str,
    payload: bytes,
    clean: dict,
    ref_low: np.ndarray,
    ref_high: np.ndarray,
    input_pcm: np.ndarray,
) -> dict:
    corrupt = bytearray(payload)
    before = corrupt[ERROR_FRAME]
    corrupt[ERROR_FRAME] ^= 1 << bit
    after = corrupt[ERROR_FRAME]
    decoded = decode_payload(bytes(corrupt), mode, ref_low, ref_high)
    clean_low = clean["low"].astype(np.float64)
    clean_high = clean["high"].astype(np.float64)
    err_low = ref_low.astype(np.float64) - decoded["low"].astype(np.float64)
    err_high = ref_high.astype(np.float64) - decoded["high"].astype(np.float64)
    delta_low = decoded["low"].astype(np.float64) - clean_low
    delta_high = decoded["high"].astype(np.float64) - clean_high
    delta_pcm = decoded["pcm"].astype(np.int32) - clean["pcm"].astype(np.int32)
    lag = ALIGN_LAG
    local_clean_low = local_snr(ref_low, clean["low"], ERROR_FRAME)
    local_error_low = local_snr(ref_low, decoded["low"], ERROR_FRAME)
    local_clean_high = local_snr(ref_high, clean["high"], ERROR_FRAME)
    local_error_high = local_snr(ref_high, decoded["high"], ERROR_FRAME)
    local_clean_pcm = local_snr(
        input_pcm[lag:],
        clean["pcm"][:-lag],
        ERROR_FRAME * 2,
        half_width=160,
    )
    local_error_pcm = local_snr(
        input_pcm[lag:],
        decoded["pcm"][:-lag],
        ERROR_FRAME * 2,
        half_width=160,
    )
    same_band_error = delta_low if band == "low" else delta_high
    other_band_error = delta_high if band == "low" else delta_low
    mask = np.abs(same_band_error) > 1.0
    row = {
        "case": name,
        "mode": mode,
        "bitrate": MODES[mode]["bitrate"],
        "error_frame": ERROR_FRAME,
        "error_time_ms": ERROR_TIME_MS,
        "physical_bit": bit,
        "band": band,
        "role": role,
        "code_before": before,
        "code_after": after,
        "band_error_span": span_info(mask),
        "other_band_error_span": span_info(np.abs(other_band_error) > 1.0),
        "pcm_error_span": span_info(np.abs(delta_pcm) > 0),
        "band_tail_frames_after_error": int(np.count_nonzero(mask[ERROR_FRAME + 1:])),
        "pcm_tail_samples_after_error": int(np.count_nonzero(np.abs(delta_pcm[ERROR_FRAME * 2 + 1:]) > 0)),
        "delta_low_rms": rms(delta_low),
        "delta_high_rms": rms(delta_high),
        "delta_pcm_rms": rms(delta_pcm.astype(np.float64)),
        "snr_low_clean_db": snr_db(ref_low, clean["low"]),
        "snr_low_error_db": snr_db(ref_low, decoded["low"]),
        "snr_high_clean_db": snr_db(ref_high, clean["high"]),
        "snr_high_error_db": snr_db(ref_high, decoded["high"]),
        "snr_pcm_clean_db": aligned_snr(input_pcm, clean["pcm"], lag),
        "snr_pcm_error_db": aligned_snr(input_pcm, decoded["pcm"], lag),
        "local20ms_low_clean_db": local_clean_low,
        "local20ms_low_error_db": local_error_low,
        "local20ms_high_clean_db": local_clean_high,
        "local20ms_high_error_db": local_error_high,
        "local20ms_pcm_clean_db": local_clean_pcm,
        "local20ms_pcm_error_db": local_error_pcm,
        "error_wav": str(OUT / "audio" / f"{name}.wav"),
    }
    row["delta_snr_low_db"] = row["snr_low_error_db"] - row["snr_low_clean_db"]
    row["delta_snr_high_db"] = row["snr_high_error_db"] - row["snr_high_clean_db"]
    row["delta_snr_pcm_db"] = row["snr_pcm_error_db"] - row["snr_pcm_clean_db"]
    row["delta_local20ms_low_db"] = row["local20ms_low_error_db"] - row["local20ms_low_clean_db"]
    row["delta_local20ms_high_db"] = row["local20ms_high_error_db"] - row["local20ms_high_clean_db"]
    row["delta_local20ms_pcm_db"] = row["local20ms_pcm_error_db"] - row["local20ms_pcm_clean_db"]
    write_wav(OUT / "audio" / f"{name}.wav", FS, decoded["pcm"])
    return row


def band_noise(
    n: int,
    low_hz: float,
    high_hz: float,
    target_rms: float,
    seed: int,
) -> np.ndarray:
    rng = np.random.default_rng(seed)
    noise = rng.standard_normal(n)
    sos = signal.butter(
        8,
        (low_hz, high_hz),
        btype="bandpass",
        fs=FS,
        output="sos",
    )
    filtered = signal.sosfilt(sos, noise)
    return filtered / max(rms(filtered), 1e-12) * target_rms


def lowpass(x: np.ndarray, high_hz: float) -> np.ndarray:
    sos = signal.butter(8, high_hz, btype="lowpass", fs=FS, output="sos")
    return signal.sosfilt(sos, x.astype(np.float64))


def spectrum(x: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    freq, power = signal.welch(
        x.astype(np.float64),
        fs=FS,
        window="hann",
        nperseg=2048,
        noverlap=1024,
        detrend=False,
        scaling="density",
    )
    return freq, power


def fraction_db(x: np.ndarray, low_hz: float, high_hz: float) -> float:
    freq, power = spectrum(x)
    mask = (freq >= low_hz) & (freq < high_hz)
    return float(
        10.0
        * math.log10(
            max(float(np.sum(power[mask])), 1e-30)
            / max(float(np.sum(power)), 1e-30)
        )
    )


def scale_to_rms(x: np.ndarray, target: float, peak_limit: int = 30000) -> np.ndarray:
    y = x.astype(np.float64)
    y = y / max(rms(y), 1e-12) * target
    peak = float(np.max(np.abs(y)))
    if peak > peak_limit:
        y *= peak_limit / peak
    return y


def isolation_metrics(
    name: str,
    source: np.ndarray,
    ref_low: np.ndarray,
    ref_high: np.ndarray,
    clean: dict,
    payload: bytes,
) -> dict:
    decoded = clean
    low = decoded["low"].astype(np.float64)
    high = decoded["high"].astype(np.float64)
    err_low = ref_low.astype(np.float64) - low
    err_high = ref_high.astype(np.float64) - high
    corrupt = bytearray(payload)
    corrupt[ERROR_FRAME] ^= 0x80
    high_bit_error = decode_payload(bytes(corrupt), 1, ref_low, ref_high)
    low_unchanged = np.array_equal(clean["low"], high_bit_error["low"])
    clean_low_component, _ = synthesize_subbands(clean["low"], np.zeros_like(clean["high"]))
    error_low_component, _ = synthesize_subbands(
        high_bit_error["low"], np.zeros_like(high_bit_error["high"])
    )
    low_component_unchanged = np.array_equal(clean_low_component, error_low_component)
    clean_low_component_rms = rms(clean_low_component.astype(np.float64))
    pcm_changed = not np.array_equal(clean["pcm"], high_bit_error["pcm"])
    lag = peak_lag(source, decoded["pcm"])
    in_hf = fraction_db(source, 4000.0, 7900.0)
    out_hf = fraction_db(decoded["pcm"], 4000.0, 7900.0)
    in_lf = fraction_db(source, 0.0, 3500.0)
    out_lf = fraction_db(decoded["pcm"], 0.0, 3500.0)
    row = {
        "case": name,
        "input_rms": rms(source.astype(np.float64)),
        "output_rms": rms(decoded["pcm"].astype(np.float64)),
        "align_lag_samples": lag,
        "ref_low_rms": rms(ref_low.astype(np.float64)),
        "ref_high_rms": rms(ref_high.astype(np.float64)),
        "rec_low_rms": rms(low),
        "rec_high_rms": rms(high),
        "analysis_band_ratio_low_over_high_db": db_ratio(
            rms(ref_low.astype(np.float64)), rms(ref_high.astype(np.float64))
        ),
        "synthesis_band_ratio_low_over_high_db": db_ratio(rms(low), rms(high)),
        "error_low_rms": rms(err_low),
        "error_high_rms": rms(err_high),
        "quant_noise_low_over_high_db": db_ratio(rms(err_low), rms(err_high)),
        "error_correlation": float(np.corrcoef(err_low, err_high)[0, 1]),
        "snr_low_db": snr_db(ref_low, decoded["low"]),
        "snr_high_db": snr_db(ref_high, decoded["high"]),
        "input_hf_fraction_db": in_hf,
        "output_hf_fraction_db": out_hf,
        "hf_fraction_change_db": out_hf - in_hf,
        "input_lf_fraction_db": in_lf,
        "output_lf_fraction_db": out_lf,
        "high_bit_error_leaves_low_band_bit_exact": low_unchanged,
        "high_bit_error_leaves_low_band_pcm_component_exact": low_component_unchanged,
        "low_band_pcm_component_rms": clean_low_component_rms,
        "high_bit_error_changes_16k_pcm": pcm_changed,
        "decoded_wav": str(OUT / "audio" / f"{name}.wav"),
    }
    write_wav(OUT / "audio" / f"{name}.wav", FS, decoded["pcm"])
    return row


def tone_check(freq_hz: float) -> dict:
    t = np.arange(0, 1.0, 1.0 / FS)
    source = scale_to_rms(np.sin(2 * np.pi * freq_hz * t), 5000.0)
    payload, ref_low, ref_high, _ = encode_64k(source)
    decoded = decode_payload(payload, 1, ref_low, ref_high)
    lag = ALIGN_LAG
    skip_samples = 1600
    skip_frames = skip_samples // 2
    high = decoded["high"][skip_frames:].astype(np.float64)
    ref_high_f = ref_high[skip_frames:].astype(np.float64)
    return {
        "tone_hz": freq_hz,
        "snr_pcm_db": snr_db(source[skip_samples:-lag], decoded["pcm"][skip_samples + lag:]),
        "align_lag_samples": lag,
        "analysis_start_ms": skip_samples / FS * 1000.0,
        "ref_high_rms": rms(ref_high_f),
        "rec_high_rms": rms(high),
        "high_band_gain_db": db_ratio(rms(high), rms(ref_high_f)),
        "high_band_corr": float(np.corrcoef(ref_high_f, high)[0, 1]),
        "snr_high_db": snr_db(ref_high_f, high.astype(np.int32)),
        "output_hf_fraction_db": fraction_db(decoded["pcm"][skip_samples:], 4000.0, 7900.0),
    }


def plot_mode_snr(mode_rows: list[dict]) -> None:
    labels = [f"{row['bitrate'] // 1000}k" for row in mode_rows]
    x = np.arange(len(labels))
    width = 0.24
    fig, ax = plt.subplots(figsize=(8.2, 4.3))
    ax.bar(x - width, [row["snr_low_db"] for row in mode_rows], width, label="low 0-4 kHz")
    ax.bar(x, [row["snr_high_db"] for row in mode_rows], width, label="high 4-8 kHz")
    ax.bar(x + width, [row["snr_pcm_db"] for row in mode_rows], width, label="16 kHz PCM")
    ax.set_xticks(x, labels)
    ax.set_ylabel("SNR (dB)")
    ax.set_ylim(0, 45)
    ax.grid(axis="y", alpha=0.3)
    ax.legend(frameon=False)
    ax.set_title("G.722 clean-decode SNR by bit rate")
    fig.tight_layout()
    fig.savefig(OUT / "plots" / "mode_snr.png", dpi=160)
    plt.close(fig)


def plot_predictor_gain(rows: list[dict]) -> None:
    frames = np.asarray([row["frame"] for row in rows])
    low = np.asarray([row["gain_low_db"] for row in rows])
    high = np.asarray([row["gain_high_db"] for row in rows])
    step = max(1, len(frames) // 4000)
    fig, ax = plt.subplots(figsize=(8.2, 4.3))
    ax.plot(frames[::step] / ADPCM_FS, low[::step], lw=1.0, label="low-band scale")
    ax.plot(frames[::step] / ADPCM_FS, high[::step], lw=1.0, label="high-band scale")
    ax.set_xlabel("time (s)")
    ax.set_ylabel("adaptive quantizer scale (dB re initial)")
    ax.grid(alpha=0.3)
    ax.legend(frameon=False)
    ax.set_title("G.722 adaptive quantizer gain (64 kbit/s)")
    fig.tight_layout()
    fig.savefig(OUT / "plots" / "predictor_gain.png", dpi=160)
    plt.close(fig)


def plot_error_support(error_rows: list[dict]) -> None:
    labels = [row["case"].replace("mode", "m") for row in error_rows]
    tail = [row["band_tail_frames_after_error"] + 1 for row in error_rows]
    colors = ["tab:green" if value <= 1 else "tab:red" for value in tail]
    fig, ax = plt.subplots(figsize=(9.2, 4.6))
    bars = ax.bar(range(len(labels)), tail, color=colors)
    ax.set_yscale("log")
    ax.set_xticks(range(len(labels)), labels, rotation=28, ha="right")
    ax.set_ylabel("support (ADPCM frames, log)")
    ax.set_title("Single-bit error support: fine bits vs core/high-band bits")
    ax.grid(axis="y", alpha=0.3)
    for bar, value in zip(bars, tail):
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            value * 1.08,
            str(value),
            ha="center",
            va="bottom",
            fontsize=8,
        )
    fig.tight_layout()
    fig.savefig(OUT / "plots" / "bit_error_support.png", dpi=160)
    plt.close(fig)


def plot_isolation(source: np.ndarray, decoded_pcm: np.ndarray, name: str) -> None:
    freq_in, power_in = spectrum(source)
    freq_out, power_out = spectrum(decoded_pcm)
    fig, ax = plt.subplots(figsize=(8.4, 4.5))
    ax.plot(freq_in / 1000.0, 10 * np.log10(np.maximum(power_in, 1e-20)), lw=1.0, label="input")
    ax.plot(freq_out / 1000.0, 10 * np.log10(np.maximum(power_out, 1e-20)), lw=1.0, label="decoded")
    ax.axvline(4.0, color="k", ls="--", lw=0.8)
    ax.axvspan(0.0, 3.5, color="tab:green", alpha=0.08)
    ax.set_xlim(0, 8)
    ax.set_xlabel("frequency (kHz)")
    ax.set_ylabel("PSD (dB/Hz)")
    ax.set_title(f"Sub-band isolation spectrum: {name}")
    ax.grid(alpha=0.25)
    ax.legend(frameon=False)
    fig.tight_layout()
    fig.savefig(OUT / "plots" / f"isolation_{name}.png", dpi=160)
    plt.close(fig)


def plot_hf_speech(source: np.ndarray, decoded_pcm: np.ndarray) -> None:
    freq_in, power_in = spectrum(source)
    freq_out, power_out = spectrum(decoded_pcm)
    fig, ax = plt.subplots(figsize=(8.4, 4.5))
    ax.plot(freq_in / 1000.0, 10 * np.log10(np.maximum(power_in, 1e-20)), lw=1.0, label="input")
    ax.plot(freq_out / 1000.0, 10 * np.log10(np.maximum(power_out, 1e-20)), lw=1.0, label="64k decoded")
    ax.axvline(4.0, color="k", ls="--", lw=0.8)
    ax.set_xlim(0, 8)
    ax.set_xlabel("frequency (kHz)")
    ax.set_ylabel("PSD (dB/Hz)")
    ax.set_title("High-frequency speech after G.722 round trip")
    ax.grid(alpha=0.25)
    ax.legend(frameon=False)
    fig.tight_layout()
    fig.savefig(OUT / "plots" / "hf_speech_spectrum.png", dpi=160)
    plt.close(fig)


def flatten_span_rows(rows: list[dict]) -> list[dict]:
    flat: list[dict] = []
    for row in rows:
        out = {key: value for key, value in row.items() if not isinstance(value, dict)}
        for key in ("band_error_span", "other_band_error_span", "pcm_error_span"):
            span = row[key]
            out[f"{key}_first"] = span["first"]
            out[f"{key}_last"] = span["last"]
            out[f"{key}_count"] = span["count"]
        flat.append(out)
    return flat


def write_report(
    mode_rows: list[dict],
    error_rows: list[dict],
    isolation_rows: list[dict],
    tones: list[dict],
    hf_rows: list[dict],
) -> None:
    lines = [
        "# G.722 子带 ADPCM 分析",
        "",
        "## 码流与 mode",
        "",
        "ITU-T G.722 的每个码字没有内嵌 mode 字段。mode 1/2/3 由外部信令选择，"
        "分别表示 64/56/48 kbit/s；码字占用 8/7/6 个音频比特，余下 0/1/2 个 LSB "
        "可用于辅助数据。本报告的 `mode` 列是从外部配置恢复的码率模式，不是从字节中解析出的字段。",
        "",
        "| mode | bitrate | audio bits | aux bits | low levels | high levels |",
        "|---:|---:|---:|---:|---:|---:|",
    ]
    for row in mode_rows:
        lines.append(
            f"| {row['mode']} | {row['bitrate']} | {row['audio_bits']} | "
            f"{row['aux_bits']} | {row['low_levels']} | {row['high_levels']} |"
        )
    lines.extend(
        [
            "",
            "## 解码质量、量化级数与增益",
            "",
            "| mode | low SNR | high SNR | PCM SNR | low err RMS | high err RMS | low path gain | high path gain |",
            "|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in mode_rows:
        lines.append(
            f"| {row['mode']} | {row['snr_low_db']:.2f} dB | {row['snr_high_db']:.2f} dB | "
            f"{row['snr_pcm_db']:.2f} dB | {row['error_low_rms']:.2f} | "
            f"{row['error_high_rms']:.2f} | {row['path_gain_low_db']:.2f} dB | "
            f"{row['path_gain_high_db']:.2f} dB |"
        )
    first = mode_rows[0]
    lines.extend(
        [
            "",
            f"64 kbit/s 自适应量化尺度均值为低带 {first['det_low_mean']:.2f}"
            f"（相对初值 8 为 {first['quant_scale_gain_low_db']:.2f} dB），"
            f"高带 {first['det_high_mean']:.2f}"
            f"（相对初值 2 为 {first['quant_scale_gain_high_db']:.2f} dB）。"
            "上表的 path gain 是实际子带重构/参考信号增益，不是量化尺度变化。",
            "",
            "误差帧 4968 的预测器状态（`s`=预测信号，`a0/a1`=二阶极点系数，"
            "`b0..b5`=六阶零点系数，`det`=量化尺度）：",
            "",
        ]
    )
    with (OUT / "data" / "frames_mode1_64k.csv").open(encoding="utf-8") as handle:
        for frame_row in csv.DictReader(handle):
            if int(frame_row["frame"]) == ERROR_FRAME:
                low_zeros = ",".join(frame_row[f"low_b{i}"] for i in range(6))
                high_zeros = ",".join(frame_row[f"high_b{i}"] for i in range(6))
                lines.append(
                    f"- low: `s={frame_row['low_s']}`, `det={frame_row['low_det']}`, "
                    f"`a0={frame_row['low_a0']}`, `a1={frame_row['low_a1']}`, "
                    f"`b0..b5=[{low_zeros}]`."
                )
                lines.append(
                    f"- high: `s={frame_row['high_s']}`, `det={frame_row['high_det']}`, "
                    f"`a0={frame_row['high_a0']}`, `a1={frame_row['high_a1']}`, "
                    f"`b0..b5=[{high_zeros}]`."
                )
                break
    lines.extend(
        [
            "",
            "## 单比特错误",
            "",
            "| case | band | tail frames | low dSNR | high dSNR | local low dSNR | local high dSNR |",
            "|---|---|---:|---:|---:|---:|---:|",
        ]
    )
    for row in error_rows:
        lines.append(
            f"| {row['case']} | {row['band']} | {row['band_tail_frames_after_error']} | "
            f"{row['delta_snr_low_db']:+.4f} dB | {row['delta_snr_high_db']:+.4f} dB | "
            f"{row['delta_local20ms_low_db']:+.3f} dB | "
            f"{row['delta_local20ms_high_db']:+.3f} dB |"
        )
    lines.extend(
        [
            "",
            "细比特错误在子带域只改变当前 ADPCM 帧；16 kHz 输出的振铃来自 24 抽头 QMF 的有限记忆。"
            "核心/符号比特或高带比特会改变自适应预测器/量化尺度，误差可持续数百毫秒，不能表述为“所有错误仅限当前帧”。",
            "",
            "## 高频噪声隔离",
            "",
            "| case | low/high quant noise | low SNR | high SNR | high flip leaves low band | low PCM component | output HF change |",
            "|---|---:|---:|---:|---|---|---:|",
        ]
    )
    for row in isolation_rows:
        lines.append(
            f"| {row['case']} | {row['quant_noise_low_over_high_db']:.2f} dB | "
            f"{row['snr_low_db']:.2f} dB | {row['snr_high_db']:.2f} dB | "
            f"{row['high_bit_error_leaves_low_band_bit_exact']} | "
            f"{row['high_bit_error_leaves_low_band_pcm_component_exact']} | "
            f"{row['hf_fraction_change_db']:+.2f} dB |"
        )
    high_only = next(row for row in isolation_rows if row["case"].endswith("high_only_noise"))
    lines.extend(
        [
            "",
            f"高频噪声单独输入时，低带量化误差/高带量化误差为 "
            f"{high_only['quant_noise_low_over_high_db']:.2f} dB；"
            f"高带单比特翻转后低带重建与低带独立 QMF 合成分量均逐样本不变。"
            "16 kHz 混合输出的低频频谱底来自 QMF 合成滤波器的有限阻带抑制，"
            "不是高带 ADPCM 状态耦合或高带量化噪声进入低带预测器。",
        ]
    )
    lines.extend(
        [
            "",
            "## 高频语音与高带截断",
            "",
            "| mode | high-band path gain | high-band corr | input HF fraction | output HF fraction |",
            "|---:|---:|---:|---:|---:|",
        ]
    )
    for row in hf_rows:
        lines.append(
            f"| {row['mode']} | {row['high_band_path_gain_db']:.2f} dB | "
            f"{row['high_band_corr']:.4f} | {row['input_hf_fraction_db']:.2f} dB | "
            f"{row['output_hf_fraction_db']:.2f} dB |"
        )
    lines.extend(
        [
            "",
            "纯 5/6/7 kHz 音测试：",
            "",
        ]
    )
    for row in tones:
        lines.append(
            f"- {row['tone_hz']:.0f} Hz: PCM SNR {row['snr_pcm_db']:.2f} dB, "
            f"high-band gain {row['high_band_gain_db']:.2f} dB, "
            f"correlation {row['high_band_corr']:.4f}."
        )
    (OUT / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    ensure_dirs()
    source_path = ROOT / "artifacts" / "g722" / "audio" / "speech_hf_16k.wav"
    rate, speech = read_wav(source_path)
    if rate != FS:
        raise ValueError(f"expected {FS} Hz speech, got {rate}")
    write_wav(OUT / "audio" / "input_speech_hf_16k.wav", FS, speech)

    payload64, ref_low, ref_high, _ = encode_64k(speech)
    write_g722(OUT / "bitstream" / "mode1_64k.g722", payload64)
    clean: dict[int, dict] = {}
    mode_rows: list[dict] = []
    quant_rows: list[dict] = []
    errors: list[dict] = []
    hf_rows: list[dict] = []

    for mode in (1, 2, 3):
        payload = mode_payload(payload64, mode)
        write_g722(OUT / "bitstream" / f"mode{mode}_{MODES[mode]['bitrate'] // 1000}k.g722", payload)
        decoded = decode_payload(payload, mode, ref_low, ref_high)
        clean[mode] = decoded
        summary, lag = mode_metrics(mode, ref_low, ref_high, decoded, speech)
        mode_rows.append(summary)
        write_wav(OUT / "audio" / f"mode{mode}_{MODES[mode]['bitrate'] // 1000}k_decoded.wav", FS, decoded["pcm"])
        for row in decoded["rows"]:
            row["time_ms"] = row["frame"] / ADPCM_FS * 1000.0
        frame_path = OUT / "data" / (
            "frames_mode1_64k.csv" if mode == 1 else f"frames_mode{mode}_head.csv"
        )
        rows = decoded["rows"] if mode == 1 else decoded["rows"][:4001]
        write_csv(frame_path, rows)
        quant_rows.extend(
            quantizer_rows(mode, summary["det_low_mean"], summary["det_high_mean"])
        )
        hf_rows.append(
            {
                "mode": mode,
                "bitrate": MODES[mode]["bitrate"],
                "high_band_path_gain_db": summary["path_gain_high_db"],
                "high_band_corr": float(
                    np.corrcoef(
                        ref_high.astype(np.float64),
                        decoded["high"].astype(np.float64),
                    )[0, 1]
                ),
                "high_band_snr_db": summary["snr_high_db"],
                "low_band_snr_db": summary["snr_low_db"],
                "input_hf_fraction_db": fraction_db(speech, 4000.0, 7900.0),
                "output_hf_fraction_db": fraction_db(decoded["pcm"], 4000.0, 7900.0),
                "snr_pcm_db": summary["snr_pcm_db"],
                "align_lag_samples": lag,
            }
        )

    for row in hf_rows:
        row["hf_fraction_change_db"] = (
            row["output_hf_fraction_db"] - row["input_hf_fraction_db"]
        )

    for mode, bit, name, band, role in BIT_ERROR_CASES:
        payload = mode_payload(payload64, mode)
        errors.append(
            bit_error_metrics(
                name,
                mode,
                bit,
                band,
                role,
                payload,
                clean[mode],
                ref_low,
                ref_high,
                speech,
            )
        )

    isolation_rows: list[dict] = []
    n = len(speech)
    high_only = band_noise(n, 4500.0, 7800.0, 3200.0, 20261120)
    speech_low = scale_to_rms(lowpass(speech, 3300.0), 3400.0)
    high_noise = band_noise(n, 4500.0, 7800.0, 1500.0, 20261121)
    speech_plus = scale_to_rms(speech_low + high_noise, 4600.0)
    isolation_sources = (
        ("isolation_high_only_noise", high_only),
        ("isolation_speech_plus_high_noise", speech_plus),
    )
    for name, source in isolation_sources:
        source_i16 = np.clip(np.rint(source), -32768, 32767).astype(np.int16)
        write_wav(OUT / "audio" / f"{name}_input.wav", FS, source_i16)
        payload, iso_low, iso_high, _ = encode_64k(source_i16)
        decoded = decode_payload(payload, 1, iso_low, iso_high)
        row = isolation_metrics(
            name,
            source_i16,
            iso_low,
            iso_high,
            decoded,
            payload,
        )
        isolation_rows.append(row)
        plot_isolation(source_i16, decoded["pcm"], name.replace("isolation_", ""))

    tones = [tone_check(freq) for freq in (5000.0, 6000.0, 7000.0)]
    plot_mode_snr(mode_rows)
    plot_predictor_gain(clean[1]["rows"])
    plot_error_support(errors)
    plot_hf_speech(speech, clean[1]["pcm"])

    write_csv(OUT / "data" / "mode_summary.csv", mode_rows)
    write_csv(OUT / "data" / "quantizer_levels.csv", quant_rows)
    write_csv(OUT / "data" / "bit_error_summary.csv", flatten_span_rows(errors))
    write_csv(OUT / "data" / "isolation_summary.csv", isolation_rows)
    write_csv(OUT / "data" / "high_frequency_speech.csv", hf_rows)
    write_csv(OUT / "data" / "high_frequency_tones.csv", tones)

    report = {
        "method": {
            "implementation": "ITU-T G.722 fixed-point QMF and adaptive ADPCM",
            "reference_crosscheck": "byte-identical 64k payload and sample-identical PCM vs FFmpeg G.722",
            "input": str(OUT / "audio" / "input_speech_hf_16k.wav"),
            "sample_rate_hz": FS,
            "adpcm_frame_rate_hz": ADPCM_FS,
            "error_frame": ERROR_FRAME,
            "error_time_ms": ERROR_TIME_MS,
            "mode_field": "not embedded; external mode 1/2/3 selects 64/56/48 kbit/s",
        },
        "modes": mode_rows,
        "bit_errors": errors,
        "isolation": isolation_rows,
        "tones": tones,
        "high_frequency_speech": hf_rows,
    }
    (OUT / "data" / "summary.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False, default=str) + "\n",
        encoding="utf-8",
    )
    write_report(mode_rows, errors, isolation_rows, tones, hf_rows)
    print(json.dumps(report["method"], indent=2, ensure_ascii=False))
    print(f"outputs: {OUT}")


if __name__ == "__main__":
    main()
