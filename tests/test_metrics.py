"""Sanity checks for the metric implementations and PSSH rewrite."""

import os
import sys

import cv2
import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from quality_metrics.metrics import (  # noqa: E402
    bt601_luma,
    msssim,
    msssim_map,
    psnr_global,
    ssim,
    ssim_map,
)
from quality_metrics.pssh import (  # noqa: E402
    DEFAULT_KID,
    bytes_to_kid,
    parse_pssh,
    rewrite_pssh,
    widevine_proto_kids,
)


@pytest.fixture(name="frames")
def make_frames():
    rng = np.random.default_rng(7)
    reference = rng.integers(0, 256, (96, 128, 3), dtype=np.uint8)
    distorted = np.clip(
        reference.astype(np.int16)
        + rng.normal(0, 12, reference.shape),
        0, 255,
    ).astype(np.uint8)
    return bt601_luma(reference), bt601_luma(distorted)


def test_identical_frames_are_perfect(frames):
    ref, _ = frames
    assert psnr_global(ref, ref) == float("inf")
    assert ssim(ref, ref) == pytest.approx(1.0, abs=1e-6)
    assert msssim(ref, ref) == pytest.approx(1.0, abs=1e-6)


def test_metric_ranges(frames):
    ref, dist = frames
    assert psnr_global(ref, dist) > 20.0
    assert 0.0 < ssim(ref, dist) <= 1.0
    assert 0.0 < msssim(ref, dist) <= 1.0


def test_ssim_matches_opencv(frames):
    ref, dist = frames
    mine = float(np.mean(ssim_map(ref, dist)))
    cv_value = cv2.SSIM(
        ref.astype(np.float32), dist.astype(np.float32)
    )[0] if hasattr(cv2, "SSIM") else None
    if cv_value is not None:
        assert mine == pytest.approx(float(cv_value), rel=2e-2)


def test_ssim_known_formula(frames):
    """Cross-check the windowed SSIM against an independent numpy formula."""
    ref, dist = frames
    c1 = (0.01 * 255.0) ** 2
    c2 = (0.03 * 255.0) ** 2
    kernel = cv2.getGaussianKernel(11, 1.5)
    window = kernel @ kernel.T

    def blur(img):
        return cv2.filter2D(img, -1, window, borderType=cv2.BORDER_REFLECT)

    mu_r = blur(ref)
    mu_d = blur(dist)
    sigma_r = blur(ref * ref) - mu_r ** 2
    sigma_d = blur(dist * dist) - mu_d ** 2
    sigma_rd = blur(ref * dist) - mu_r * mu_d
    expected = np.mean(
        ((2 * mu_r * mu_d + c1) * (2 * sigma_rd + c2))
        / (
            (mu_r ** 2 + mu_d ** 2 + c1)
            * (sigma_r + sigma_d + c2)
        )
    )
    assert ssim(ref, dist) == pytest.approx(float(expected), rel=1e-4)


def test_msssim_map_resolution_and_range(frames):
    ref, dist = frames
    map_value = msssim_map(ref, dist)
    assert map_value.shape == ref.shape
    assert map_value.min() >= 0.0
    assert map_value.max() <= 1.0 + 1e-6


def test_psnr_known_value():
    ref = np.zeros((32, 32), dtype=np.float64)
    dist = np.full((32, 32), 10.0, dtype=np.float64)
    assert psnr_global(ref, dist) == pytest.approx(
        10 * np.log10(255 ** 2 / 100.0)
    )


def test_pssh_roundtrip(tmp_path):
    old_kid = bytes(range(16))
    new_kid_hex = DEFAULT_KID.replace("-", "")
    wv_data = (
        b"\x08\x01"
        + b"\x12\x10" + old_kid
        + b"\x1a\x03abc"
        + b"\x22\x03def"
    )
    system_id = bytes.fromhex("edef8ba979d64acea3c827dcd51d21ed")
    payload = (
        bytes([1]) + (0).to_bytes(3, "big") + system_id
        + (1).to_bytes(4, "big") + old_kid
        + len(wv_data).to_bytes(4, "big") + wv_data
    )
    blob = (8 + len(payload)).to_bytes(4, "big") + b"pssh" + payload
    rewritten, report = rewrite_pssh(blob)
    assert len(rewritten) == len(blob)
    box = parse_pssh(rewritten)
    assert bytes_to_kid(box.box_kids[0]) == DEFAULT_KID
    assert [bytes_to_kid(k) for k in widevine_proto_kids(box.data)] == [
        DEFAULT_KID
    ]
    assert report["preserved_fields"] == [1, 3, 4]
    # Non-KID protobuf payload survives.
    assert b"abc" in box.data and b"def" in box.data
    assert new_kid_hex in rewritten.hex()
