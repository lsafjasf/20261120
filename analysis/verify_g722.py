#!/usr/bin/env python3
"""Fast invariants and known-vector checks for the G.722 analysis."""

from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np

from g722_analysis import (
    OUT,
    decode_payload,
    encode_64k,
    mode_payload,
    read_wav,
)
from run_g722_analysis import BIT_ERROR_CASES, ERROR_FRAME, bit_error_metrics

ROOT = Path(__file__).resolve().parents[1]
INPUT = ROOT / "artifacts" / "g722" / "audio" / "speech_hf_16k.wav"

EXPECTED_INPUT_SHA256 = "a60ae97c2dc4c6ae0c1724f5392ddfe075eb5f655c7577b3d2a7d92438fb972c"
EXPECTED_PAYLOAD_SHA256 = "aaf2bc020e7a78e8ce8d691d1b9c080ea03a9a6da7b39404482cf5d5576394e0"
EXPECTED_PCM_SHA256 = "b197261b9545172fbd22b81ef29dfc4488bac8fc5b68d74763ba4db7e05da3eb"
MODE_VECTORS = {
    2: (
        "27ff067c7ee1b9e985a1625c52cde8cc2a73f3f684a4070c3d4ad4bc56028a41",
        "6452eba4c8008b2c3caff6116b4844415375213221fcfad04b28b8a5c49004f8",
    ),
    3: (
        "7290c619adff73966b88b2074847199382503ff0231dc06c0d98162add778d65",
        "808046f2c37fa11baa6367e8d05e37f0f7cb4356f84cdf5e131433b0f98ec85b",
    ),
}


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> None:
    rate, samples = read_wav(INPUT)
    assert rate == 16000
    assert digest(samples.tobytes()) == EXPECTED_INPUT_SHA256
    payload, ref_low, ref_high, _ = encode_64k(samples)
    assert len(payload) == 16000
    assert digest(payload) == EXPECTED_PAYLOAD_SHA256
    clean = decode_payload(payload, 1, ref_low, ref_high)
    assert len(clean["pcm"]) == 32000
    assert digest(clean["pcm"].tobytes()) == EXPECTED_PCM_SHA256

    for mode in (1, 2, 3):
        mode_bytes = mode_payload(payload, mode)
        decoded = decode_payload(mode_bytes, mode, ref_low, ref_high)
        assert len(decoded["low"]) == 16000
        assert len(decoded["pcm"]) == 32000
        assert np.isfinite(decoded["pcm"]).all()
        if mode in MODE_VECTORS:
            expected_payload, expected_pcm = MODE_VECTORS[mode]
            assert digest(mode_bytes) == expected_payload
            assert digest(decoded["pcm"].tobytes()) == expected_pcm

    by_name = {
        name: (mode, bit, band, role)
        for mode, bit, name, band, role in BIT_ERROR_CASES
    }
    for name in ("mode1_low_fine_bit0", "mode1_low_fine_bit1", "mode2_low_fine_bit0"):
        mode, bit, band, role = by_name[name]
        row = bit_error_metrics(
            name,
            mode,
            bit,
            band,
            role,
            mode_payload(payload, mode),
            clean if mode == 1 else decode_payload(
                mode_payload(payload, mode), mode, ref_low, ref_high
            ),
            ref_low,
            ref_high,
            samples,
        )
        assert row["band_error_span"]["count"] == 1
        assert row["band_error_span"]["first"] == ERROR_FRAME
        assert row["band_tail_frames_after_error"] == 0

    core = bit_error_metrics(
        "mode1_low_core_sign_bit5",
        1,
        5,
        "low",
        "core",
        payload,
        clean,
        ref_low,
        ref_high,
        samples,
    )
    assert core["band_tail_frames_after_error"] > 1000
    assert core["delta_low_rms"] > 100.0
    print(f"verification passed; outputs under {OUT}")


if __name__ == "__main__":
    main()
