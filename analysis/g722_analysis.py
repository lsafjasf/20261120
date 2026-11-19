#!/usr/bin/env python3
"""Bit-exact G.722 SB-ADPCM instrumentation and measurement harness.

The QMF and adaptive-predictor arithmetic below follows the integer operations
used by ITU-T G.722 and the bit-exact FFmpeg/libavcodec implementation.  Python
does not have C's fixed-width overflow rules, so explicit wrapping is used for
the int8/int16/int32 state fields.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import struct
import wave
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np
from scipy import signal
from scipy.io import wavfile


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts" / "g722_verified"

MODES = {
    1: {
        "bitrate": 64000,
        "audio_bits": 8,
        "low_bits": 6,
        "high_bits": 2,
        "aux_bits": 0,
        "low_levels": 64,
        "high_levels": 4,
    },
    2: {
        "bitrate": 56000,
        "audio_bits": 7,
        "low_bits": 5,
        "high_bits": 2,
        "aux_bits": 1,
        "low_levels": 32,
        "high_levels": 4,
    },
    3: {
        "bitrate": 48000,
        "audio_bits": 6,
        "low_bits": 4,
        "high_bits": 2,
        "aux_bits": 2,
        "low_levels": 16,
        "high_levels": 4,
    },
}


def wrap(x: int, bits: int) -> int:
    mask = (1 << bits) - 1
    x &= mask
    sign = 1 << (bits - 1)
    return x - (1 << bits) if x & sign else x


def s8(x: int) -> int:
    return wrap(x, 8)


def s16(x: int) -> int:
    return wrap(x, 16)


def s32(x: int) -> int:
    return wrap(x, 32)


def clip(x: int, lo: int, hi: int) -> int:
    return lo if x < lo else hi if x > hi else x


def clip_intp2(x: int, p: int) -> int:
    return clip(x, -(1 << p), (1 << p) - 1)


def rms(x: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(x, dtype=np.float64))))


def db_ratio(num: float, den: float) -> float:
    return float(20.0 * math.log10(max(num, 1e-30) / max(den, 1e-30)))


def snr_db(ref: np.ndarray, test: np.ndarray) -> float:
    err = ref.astype(np.float64) - test.astype(np.float64)
    return float(
        10.0
        * math.log10(
            max(float(np.sum(np.square(ref, dtype=np.float64))), 1e-30)
            / max(float(np.sum(np.square(err))), 1e-30)
        )
    )


def read_wav(path: Path) -> tuple[int, np.ndarray]:
    rate, data = wavfile.read(path)
    if data.ndim > 1:
        data = data[:, 0]
    if data.dtype == np.int16:
        samples = data
    elif np.issubdtype(data.dtype, np.floating):
        samples = np.clip(np.rint(data * 32768.0), -32768, 32767).astype(np.int16)
    else:
        raise ValueError(f"unsupported WAV dtype {data.dtype}")
    return int(rate), samples


def write_wav(path: Path, rate: int, samples: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    wavfile.write(path, rate, np.clip(samples, -32768, 32767).astype(np.int16))


def write_g722(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


INV_LOG2 = (
    2048, 2093, 2139, 2186, 2233, 2282, 2332, 2383,
    2435, 2489, 2543, 2599, 2656, 2714, 2774, 2834,
    2896, 2960, 3025, 3091, 3158, 3228, 3298, 3371,
    3444, 3520, 3597, 3676, 3756, 3838, 3922, 4008,
)

HIGH_LOG_STEP = (798, -214)
HIGH_INV_QUANT = (-926, -202, 926, 202)
LOW_LOG_STEP = (
    -60, 3042, 1198, 538, 334, 172, 58, -30,
    3042, 1198, 538, 334, 172, 58, -30, -60,
)
LOW_INV_QUANT4 = (
    0, -2557, -1612, -1121, -786, -530, -323, -150,
    2557, 1612, 1121, 786, 530, 323, 150, 0,
)
LOW_INV_QUANT5 = (
    -35, -35, -2919, -2195, -1765, -1458, -1219, -1023,
    -858, -714, -587, -473, -370, -276, -190, -110,
    2919, 2195, 1765, 1458, 1219, 1023, 858, 714,
    587, 473, 370, 276, 190, 110, 35, -35,
)
LOW_INV_QUANT6 = (
    -17, -17, -17, -17, -3101, -2738, -2376, -2088,
    -1873, -1689, -1535, -1399, -1279, -1170, -1072, -982,
    -899, -822, -750, -682, -618, -558, -501, -447,
    -396, -347, -300, -254, -211, -170, -130, -91,
    3101, 2738, 2376, 2088, 1873, 1689, 1535, 1399,
    1279, 1170, 1072, 982, 899, 822, 750, 682,
    618, 558, 501, 447, 396, 347, 300, 254,
    211, 170, 130, 91, 54, 17, -54, -17,
)

LOW_QUANT_SEARCH = (
    35, 72, 110, 150, 190, 233, 276, 323, 370, 422,
    473, 530, 587, 650, 714, 786, 858, 940, 1023, 1121,
    1219, 1339, 1458, 1612, 1765, 1980, 2195, 2557, 2919,
)

QMF_XOUT1 = (3, -11, 12, 32, -210, 951, 3876, -805, 362, -156, 53, -11)
QMF_XOUT0 = (-11, 53, -156, 362, -805, 3876, 951, -210, 32, 12, -11, 3)


def apply_qmf(prev_samples: np.ndarray | list[int], pos: int) -> tuple[int, int]:
    """Return (xout0, xout1) for the 24-sample window ending before pos."""
    xout0 = 0
    xout1 = 0
    base = pos - 24
    for i in range(12):
        xout1 += int(prev_samples[base + 2 * i]) * QMF_XOUT1[i]
        xout0 += int(prev_samples[base + 2 * i + 1]) * QMF_XOUT0[i]
    return s32(xout0), s32(xout1)


@dataclass
class BandState:
    s_predictor: int = 0
    s_zero: int = 0
    part_reconst_mem: list[int] = field(default_factory=lambda: [0, 0])
    prev_qtzd_reconst: int = 0
    pole_mem: list[int] = field(default_factory=lambda: [0, 0])
    diff_mem: list[int] = field(default_factory=lambda: [0] * 6)
    zero_mem: list[int] = field(default_factory=lambda: [0] * 6)
    log_factor: int = 0
    scale_factor: int = 0

    def clone(self) -> "BandState":
        return BandState(
            s_predictor=self.s_predictor,
            s_zero=self.s_zero,
            part_reconst_mem=self.part_reconst_mem.copy(),
            prev_qtzd_reconst=self.prev_qtzd_reconst,
            pole_mem=self.pole_mem.copy(),
            diff_mem=self.diff_mem.copy(),
            zero_mem=self.zero_mem.copy(),
            log_factor=self.log_factor,
            scale_factor=self.scale_factor,
        )


def linear_scale_factor(log_factor: int) -> int:
    wd1 = INV_LOG2[(log_factor >> 6) & 31]
    shift = log_factor >> 11
    return s16(wd1 >> -shift if shift < 0 else wd1 << shift)


def update_zero_predictor(cur_diff: int, band: BandState) -> None:
    s_zero = 0
    for k in range(5, -1, -1):
        tmp = band.diff_mem[k - 1] if k else s32(cur_diff * 2)
        sign = 0 if cur_diff == 0 else (
            -128 if ((band.diff_mem[k] ^ cur_diff) < 0) else 128
        )
        band.zero_mem[k] = s16(((band.zero_mem[k] * 255) >> 8) + sign)
        band.diff_mem[k] = s32(tmp)
        s_zero = s32(s_zero + ((tmp * band.zero_mem[k]) >> 15))
    band.s_zero = s32(s_zero)


def adaptive_prediction(band: BandState, cur_diff: int) -> None:
    cur_part_reconst = int(band.s_zero + cur_diff < 0)
    sg0 = 1 if (cur_part_reconst != band.part_reconst_mem[0]) else -1
    sg1 = 1 if (cur_part_reconst == band.part_reconst_mem[1]) else -1
    band.part_reconst_mem[1] = band.part_reconst_mem[0]
    band.part_reconst_mem[0] = cur_part_reconst

    pole1 = s16(
        (sg0 * clip(band.pole_mem[0], -8191, 8191) >> 5)
        + (sg1 * 128)
        + (band.pole_mem[1] * 127 >> 7)
    )
    band.pole_mem[1] = clip(pole1, -12288, 12288)

    limit = 15360 - band.pole_mem[1]
    pole0 = -192 * sg0 + (band.pole_mem[0] * 255 >> 8)
    band.pole_mem[0] = clip(pole0, -limit, limit)

    update_zero_predictor(cur_diff, band)
    cur_qtzd_reconst = s16(clip(s16(band.s_predictor + cur_diff) * 2, -32768, 32767))
    band.s_predictor = s16(
        clip(
            band.s_zero
            + (band.pole_mem[0] * cur_qtzd_reconst >> 15)
            + (band.pole_mem[1] * band.prev_qtzd_reconst >> 15),
            -32768,
            32767,
        )
    )
    band.prev_qtzd_reconst = cur_qtzd_reconst


def update_low_predictor(band: BandState, ilow4: int) -> None:
    cur_diff = s32(band.scale_factor * LOW_INV_QUANT4[ilow4] >> 10)
    adaptive_prediction(band, cur_diff)
    band.log_factor = clip(
        (band.log_factor * 127 >> 7) + LOW_LOG_STEP[ilow4], 0, 18432
    )
    band.scale_factor = linear_scale_factor(band.log_factor - (8 << 11))


def update_high_predictor(band: BandState, dhigh: int, ihigh: int) -> None:
    adaptive_prediction(band, dhigh)
    band.log_factor = clip(
        (band.log_factor * 127 >> 7) + HIGH_LOG_STEP[ihigh & 1], 0, 22528
    )
    band.scale_factor = linear_scale_factor(band.log_factor - (10 << 11))


def encode_low(band: BandState, xlow: int) -> int:
    diff = clip(s16(xlow - band.s_predictor), -32768, 32767)
    limit = diff if diff >= 0 else -(diff + 1)
    limit = (limit + 1) << 10
    i = 9 if limit > LOW_QUANT_SEARCH[8] * band.scale_factor else 0
    while i < 29 and limit > LOW_QUANT_SEARCH[i] * band.scale_factor:
        i += 1
    return (63 if i < 2 else 33) - i if diff < 0 else 61 - i


def encode_high(band: BandState, xhigh: int) -> int:
    diff = clip(s16(xhigh - band.s_predictor), -32768, 32767)
    pred = 141 * band.scale_factor >> 8
    magnitude = diff if diff >= 0 else -diff - 1
    return int(magnitude < pred) + 2 * int(diff >= 0)


def state_fields(prefix: str, state: BandState) -> dict[str, int]:
    return {
        f"{prefix}_s": state.s_predictor,
        f"{prefix}_sz": state.s_zero,
        f"{prefix}_prev": state.prev_qtzd_reconst,
        f"{prefix}_p0": state.part_reconst_mem[0],
        f"{prefix}_p1": state.part_reconst_mem[1],
        f"{prefix}_a0": state.pole_mem[0],
        f"{prefix}_a1": state.pole_mem[1],
        f"{prefix}_b0": state.zero_mem[0],
        f"{prefix}_b1": state.zero_mem[1],
        f"{prefix}_b2": state.zero_mem[2],
        f"{prefix}_b3": state.zero_mem[3],
        f"{prefix}_b4": state.zero_mem[4],
        f"{prefix}_b5": state.zero_mem[5],
        f"{prefix}_det": state.scale_factor,
        f"{prefix}_log": state.log_factor,
    }


@dataclass
class EncoderState:
    prev_samples: list[int] = field(default_factory=lambda: [0] * 1024)
    prev_pos: int = 22
    low: BandState = field(
        default_factory=lambda: BandState(scale_factor=8)
    )
    high: BandState = field(
        default_factory=lambda: BandState(scale_factor=2)
    )


@dataclass
class DecoderState:
    prev_samples: list[int] = field(default_factory=lambda: [0] * 1024)
    prev_pos: int = 22
    low: BandState = field(
        default_factory=lambda: BandState(scale_factor=8)
    )
    high: BandState = field(
        default_factory=lambda: BandState(scale_factor=2)
    )


def encode_64k(samples: np.ndarray) -> tuple[bytes, np.ndarray, np.ndarray, list[dict]]:
    state = EncoderState()
    payload = bytearray()
    low_ref: list[int] = []
    high_ref: list[int] = []
    rows: list[dict] = []
    samples = np.asarray(samples, dtype=np.int16)

    for frame, base in enumerate(range(0, len(samples) - 1, 2)):
        state.prev_samples[state.prev_pos] = s16(int(samples[base]))
        state.prev_samples[state.prev_pos + 1] = s16(int(samples[base + 1]))
        xout0, xout1 = apply_qmf(state.prev_samples, state.prev_pos + 2)
        state.prev_pos += 2
        if state.prev_pos >= 1024:
            state.prev_samples[:22] = state.prev_samples[state.prev_pos - 22:state.prev_pos]
            state.prev_pos = 22

        xlow = s32((xout0 + xout1) >> 14)
        xhigh = s32((xout0 - xout1) >> 14)
        ihigh = encode_high(state.high, xhigh)
        ilow = encode_low(state.low, xlow)
        dhigh = s32(state.high.scale_factor * HIGH_INV_QUANT[ihigh] >> 10)
        update_high_predictor(state.high, dhigh, ihigh)
        update_low_predictor(state.low, ilow >> 2)
        code = (ihigh << 6) | ilow
        payload.append(code)
        low_ref.append(xlow)
        high_ref.append(xhigh)
        rows.append(
            {
                "frame": frame,
                "mode": 1,
                "external_mode": 1,
                "byte": code,
                "audio_bits": 8,
                "ihigh": ihigh,
                "ilow_field": ilow,
                "ilow_update": ilow >> 2,
                "xlow": xlow,
                "xhigh": xhigh,
                "det_low": state.low.scale_factor,
                "det_high": state.high.scale_factor,
                "gain_low_db": db_ratio(state.low.scale_factor, 8),
                "gain_high_db": db_ratio(state.high.scale_factor, 2),
                **state_fields("low", state.low),
                **state_fields("high", state.high),
            }
        )

    return bytes(payload), np.asarray(low_ref, dtype=np.int32), np.asarray(high_ref, dtype=np.int32), rows


def mode_payload(payload64: bytes, mode: int) -> bytes:
    if mode == 1:
        return bytes(payload64)
    out = bytearray(len(payload64))
    if mode == 2:
        for i, code in enumerate(payload64):
            out[i] = code & 0xFE
    elif mode == 3:
        for i, code in enumerate(payload64):
            out[i] = code & 0xFC
    else:
        raise ValueError(f"unsupported G.722 mode {mode}")
    return bytes(out)


def split_mode_byte(code: int, mode: int) -> tuple[int, int, int]:
    ihigh = (code >> 6) & 3
    if mode == 1:
        ilow_field = code & 0x3F
        update_index = ilow_field >> 2
    elif mode == 2:
        ilow_field = (code >> 1) & 0x1F
        update_index = ilow_field >> 1
    elif mode == 3:
        ilow_field = (code >> 2) & 0x0F
        update_index = ilow_field
    else:
        raise ValueError(f"unsupported G.722 mode {mode}")
    return ihigh, ilow_field, update_index


def decode_payload(
    payload: bytes,
    mode: int,
    reference_low: np.ndarray | None = None,
    reference_high: np.ndarray | None = None,
) -> dict:
    state = DecoderState()
    low_out: list[int] = []
    high_out: list[int] = []
    pcm: list[int] = []
    rows: list[dict] = []
    quant5 = LOW_INV_QUANT5
    quant4 = LOW_INV_QUANT4

    for frame, code in enumerate(payload):
        ihigh, ilow_field, update_index = split_mode_byte(code, mode)
        if mode == 1:
            low_table = LOW_INV_QUANT6
            low_value = low_table[ilow_field]
        elif mode == 2:
            low_value = quant5[ilow_field]
        else:
            low_value = quant4[ilow_field]

        rlow = clip_intp2((state.low.scale_factor * low_value >> 10) + state.low.s_predictor, 14)
        update_low_predictor(state.low, update_index)
        dhigh = s32(state.high.scale_factor * HIGH_INV_QUANT[ihigh] >> 10)
        rhigh = clip_intp2(dhigh + state.high.s_predictor, 14)
        update_high_predictor(state.high, dhigh, ihigh)

        low_out.append(rlow)
        high_out.append(rhigh)
        state.prev_samples[state.prev_pos] = s16(rlow + rhigh)
        state.prev_samples[state.prev_pos + 1] = s16(rlow - rhigh)
        xout0, xout1 = apply_qmf(state.prev_samples, state.prev_pos + 2)
        state.prev_pos += 2
        pcm.extend(
            (
                clip(xout0 >> 11, -32768, 32767),
                clip(xout1 >> 11, -32768, 32767),
            )
        )
        if state.prev_pos >= 1024:
            state.prev_samples[:22] = state.prev_samples[state.prev_pos - 22:state.prev_pos]
            state.prev_pos = 22

        row = {
            "frame": frame,
            "mode": mode,
            "external_mode": mode,
            "byte": code,
            "audio_bits": MODES[mode]["audio_bits"],
            "ihigh": ihigh,
            "ilow_field": ilow_field,
            "ilow_update": update_index,
            "rlow": rlow,
            "rhigh": rhigh,
            "det_low": state.low.scale_factor,
            "det_high": state.high.scale_factor,
            "gain_low_db": db_ratio(state.low.scale_factor, 8),
            "gain_high_db": db_ratio(state.high.scale_factor, 2),
            **state_fields("low", state.low),
            **state_fields("high", state.high),
        }
        if reference_low is not None:
            row["xlow"] = int(reference_low[frame])
            row["elow"] = int(reference_low[frame]) - rlow
        if reference_high is not None:
            row["xhigh"] = int(reference_high[frame])
            row["ehigh"] = int(reference_high[frame]) - rhigh
        rows.append(row)

    return {
        "low": np.asarray(low_out, dtype=np.int32),
        "high": np.asarray(high_out, dtype=np.int32),
        "pcm": np.asarray(pcm, dtype=np.int16),
        "rows": rows,
    }


def synthesize_subbands(low: np.ndarray, high: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """QMF-synthesize low/high sub-bands separately (before final clipping)."""
    low_state = DecoderState()
    high_state = DecoderState()
    low_pcm: list[int] = []
    high_pcm: list[int] = []
    for low_value, high_value in zip(low, high):
        for state, value, output in (
            (low_state, int(low_value), low_pcm),
            (high_state, int(high_value), high_pcm),
        ):
            state.prev_samples[state.prev_pos] = s16(value)
            state.prev_samples[state.prev_pos + 1] = s16(value)
            xout0, xout1 = apply_qmf(state.prev_samples, state.prev_pos + 2)
            state.prev_pos += 2
            output.extend(
                (
                    clip(xout0 >> 11, -32768, 32767),
                    clip(xout1 >> 11, -32768, 32767),
                )
            )
            if state.prev_pos >= 1024:
                state.prev_samples[:22] = state.prev_samples[state.prev_pos - 22:state.prev_pos]
                state.prev_pos = 22
    return (
        np.asarray(low_pcm, dtype=np.int32),
        np.asarray(high_pcm, dtype=np.int32),
    )
