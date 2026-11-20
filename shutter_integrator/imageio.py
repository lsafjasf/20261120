"""Image IO for linear premultiplied frames and preview rendering.

Internal frames are float32 (H, W, 4) premultiplied linear RGBA.
``.npy`` files store them verbatim; PNG files are 8-bit sRGB with
straight alpha and are converted on load/save.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image


def srgb_to_linear(c: np.ndarray) -> np.ndarray:
    c = np.clip(c, 0.0, 1.0)
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def linear_to_srgb(c: np.ndarray) -> np.ndarray:
    c = np.clip(c, 0.0, 1.0)
    return np.where(
        c <= 0.0031308, c * 12.92, 1.055 * c ** (1.0 / 2.4) - 0.055
    )


def premultiply(rgba: np.ndarray) -> np.ndarray:
    out = rgba.copy()
    out[..., :3] *= out[..., 3:4]
    return out


def unpremultiply(rgba: np.ndarray) -> np.ndarray:
    out = rgba.copy()
    a = out[..., 3:4]
    safe = np.where(a > 1e-8, a, 1.0)
    rgb = np.where(a > 1e-8, out[..., :3] / safe, 0.0)
    out[..., :3] = np.clip(rgb, 0.0, 1.0)
    return out


def load_frame(path: "str | Path") -> np.ndarray:
    """Load a frame as float32 premultiplied linear RGBA."""
    path = Path(path)
    if path.suffix == ".npy":
        arr = np.load(path).astype(np.float32)
        if arr.ndim == 2:
            arr = np.repeat(arr[..., None], 4, axis=-1)
            arr[..., 3] = 1.0
        if arr.shape[-1] == 3:
            arr = np.dstack([arr, np.ones(arr.shape[:2], np.float32)])
        return arr
    img = Image.open(path).convert("RGBA")
    arr = np.asarray(img, dtype=np.float32) / 255.0
    arr[..., :3] = srgb_to_linear(arr[..., :3])
    return premultiply(arr)


def save_frame(frame: np.ndarray, path: "str | Path") -> None:
    """Save a premultiplied linear frame (npy keeps full precision)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix == ".npy":
        np.save(path, frame.astype(np.float32))
        return
    img = Image.fromarray(to_display(frame), "RGBA")
    img.save(path)


def to_display(frame: np.ndarray) -> np.ndarray:
    """Premultiplied linear -> 8-bit sRGB straight-alpha image."""
    straight = unpremultiply(np.asarray(frame, dtype=np.float32))
    srgb = straight.copy()
    srgb[..., :3] = linear_to_srgb(straight[..., :3])
    return (np.clip(srgb, 0, 1) * 255.0 + 0.5).astype(np.uint8)


_HOT = np.array(
    [
        [0.0, 0.0, 0.0],
        [0.55, 0.0, 0.05],
        [0.95, 0.45, 0.0],
        [1.0, 0.95, 0.35],
        [1.0, 1.0, 1.0],
    ],
    dtype=np.float64,
)


def error_map(
    frame: np.ndarray,
    reference: np.ndarray,
    scale: float = 20.0,
) -> np.ndarray:
    """Render |frame - reference| as an 8-bit RGB heat map.

    ``scale`` amplifies the error so small deviations stay visible;
    the map saturates at an error of 1/scale.
    """
    err = np.abs(
        np.asarray(frame, np.float64) - np.asarray(reference, np.float64)
    )
    mag = err.max(axis=-1) if err.ndim == 3 else err
    v = np.clip(mag * scale, 0.0, 1.0)
    pos = v * (len(_HOT) - 1)
    i0 = np.floor(pos).astype(int)
    i1 = np.minimum(i0 + 1, len(_HOT) - 1)
    frac = (pos - i0)[..., None]
    rgb = _HOT[i0] * (1 - frac) + _HOT[i1] * frac
    return (rgb * 255.0 + 0.5).astype(np.uint8)
