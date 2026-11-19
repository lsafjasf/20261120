"""Shared helpers: ffprobe film_grain_params parsing and raw YUV IO."""
import json

import numpy as np

W, H = 320, 240
FRAME_SIZE = W * H * 3 // 2


def load_grain_params(path):
    """Return list of per-frame film_grain_params dicts (None if absent)."""
    with open(path) as f:
        doc = json.load(f)
    out = []
    for frame in doc["frames"]:
        sd = None
        for side in frame.get("side_data_list", []):
            if side.get("side_data_type") == "Film grain parameters":
                sd = side
                break
        out.append(_convert(sd) if sd else None)
    return out


def _pairs(values, scalings):
    xs = [int(v) for v in values.split()]
    ys = [int(v) for v in scalings.split()]
    return list(zip(xs, ys))


def _convert(sd):
    comps = sd["components"]
    y_points = _pairs(comps[0]["y_points_value"], comps[0]["y_points_scaling"])
    uv_points = [
        _pairs(comps[1]["uv_points_value"], comps[1]["uv_points_scaling"]),
        _pairs(comps[2]["uv_points_value"], comps[2]["uv_points_scaling"]),
    ]
    return {
        "seed": int(sd["seed"]),
        "y_points": y_points,
        "uv_points": uv_points,
        "num_y_points": len(y_points),
        "num_uv_points": [len(uv_points[0]), len(uv_points[1])],
        "ar_coeffs_y": [int(v) for v in comps[0]["ar_coeffs_y"].split()],
        "ar_coeffs_uv": [
            [int(v) for v in comps[1]["ar_coeffs_uv"].split()],
            [int(v) for v in comps[2]["ar_coeffs_uv"].split()],
        ],
        "ar_coeff_lag": int(sd["ar_coeff_lag"]),
        "ar_coeff_shift": int(sd["ar_coeff_shift"]),
        "scaling_shift": int(sd["scaling_shift"]),
        "grain_scale_shift": int(sd["grain_scale_shift"]),
        "chroma_scaling_from_luma": int(sd["chroma_scaling_from_luma"]),
        "uv_mult": [int(comps[1]["uv_mult"]), int(comps[2]["uv_mult"])],
        "uv_mult_luma": [int(comps[1]["uv_mult_luma"]),
                         int(comps[2]["uv_mult_luma"])],
        "uv_offset": [int(comps[1]["uv_offset"]), int(comps[2]["uv_offset"])],
        "overlap_flag": int(sd["overlap_flag"]),
        "clip_to_restricted_range": int(sd["limit_output_range"]),
    }


def read_yuv(path, w=W, h=H):
    """Read yuv420p rawvideo -> list of (Y, U, V) uint8 arrays."""
    raw = np.fromfile(path, dtype=np.uint8)
    fs = w * h * 3 // 2
    frames = []
    for off in range(0, len(raw), fs):
        buf = raw[off:off + fs]
        y = buf[:w * h].reshape(h, w)
        u = buf[w * h:w * h + w * h // 4].reshape(h // 2, w // 2)
        v = buf[w * h + w * h // 4:].reshape(h // 2, w // 2)
        frames.append((y, u, v))
    return frames


def write_yuv(path, frames):
    with open(path, "wb") as f:
        for y, u, v in frames:
            f.write(np.asarray(y, np.uint8).tobytes())
            f.write(np.asarray(u, np.uint8).tobytes())
            f.write(np.asarray(v, np.uint8).tobytes())
