"""Matplotlib figures: per-frame curves, heatmaps and alignment overlays."""

from __future__ import annotations

import os

import cv2
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from .pipeline import METRICS, REGION_BLOCK


def plot_metric_curves(
    rows: list[dict],
    boundary_frame: int,
    noise_flags: np.ndarray,
    fps: float,
    out_path: str,
) -> None:
    frames = [row["frame"] for row in rows]
    times = [frame / fps for frame in frames]
    fig, axes = plt.subplots(3, 1, figsize=(11, 9), sharex=True)
    noise_frames = np.flatnonzero(noise_flags)
    for axis, metric in zip(axes, METRICS):
        values = [row[metric] for row in rows]
        axis.plot(times, values, lw=1.4, label=metric)
        axis.axvline(
            boundary_frame / fps, color="crimson", ls="--", lw=1.2,
            label=f"bitrate boundary @ frame {boundary_frame}",
        )
        if noise_frames.size:
            axis.axvspan(
                noise_frames[0] / fps,
                (noise_frames[-1] + 1) / fps,
                color="orange", alpha=0.18, label="high-noise burst",
            )
        axis.set_ylabel(metric)
        axis.grid(alpha=0.3)
        axis.legend(loc="lower left", fontsize=8)
    axes[-1].set_xlabel("time (s)")
    fig.suptitle("Per-frame PSNR / SSIM / MS-SSIM")
    fig.tight_layout()
    fig.savefig(out_path, dpi=140)
    plt.close(fig)


def plot_high_noise(noise_report: list[dict], out_path: str) -> None:
    metrics = [item["metric"] for item in noise_report]
    relative = [item["relative_delta_pct"] for item in noise_report]
    effect = [item["effect_size"] for item in noise_report]
    fig, (ax_left, ax_right) = plt.subplots(1, 2, figsize=(11, 4))
    ax_left.bar(metrics, effect, color="purple")
    ax_left.set_ylabel("Cohen d (noise vs clean)")
    ax_left.set_title("Standardised noise effect")
    ax_left.grid(alpha=0.3, axis="y")
    for idx, value in enumerate(effect):
        ax_left.text(idx, value, f"{value:.2f}", ha="center", va="top")
    ax_right.bar(metrics, relative, color="darkorange")
    ax_right.set_ylabel("relative change (%)")
    ax_right.set_title("High-noise relative drop")
    ax_right.grid(alpha=0.3, axis="y")
    fig.tight_layout()
    fig.savefig(out_path, dpi=140)
    plt.close(fig)


def plot_segment_means(segments: list[dict], out_path: str) -> None:
    fig, axes = plt.subplots(1, 3, figsize=(11, 3.6))
    for axis, metric in zip(axes, METRICS):
        labels = [segment["label"] for segment in segments]
        values = [segment["means"][metric] for segment in segments]
        axis.bar(labels, values, color=["steelblue", "indianred"])
        axis.set_title(metric)
        axis.grid(alpha=0.3, axis="y")
        for idx, value in enumerate(values):
            axis.text(idx, value, f"{value:.4f}", ha="center", va="bottom")
    fig.suptitle("High vs low bitrate segment means")
    fig.tight_layout()
    fig.savefig(out_path, dpi=140)
    plt.close(fig)


def plot_heatmaps(region_dir: str, heatmap_dir: str) -> list[str]:
    os.makedirs(heatmap_dir, exist_ok=True)
    paths: list[str] = []
    for npz_name in sorted(os.listdir(region_dir)):
        if not npz_name.endswith(".npz"):
            continue
        data = np.load(os.path.join(region_dir, npz_name))
        frame = int(npz_name.split("_")[1].split(".")[0])
        fig, axes = plt.subplots(1, 3, figsize=(13, 3.4))
        for axis, metric in zip(axes, METRICS):
            image = data[f"{metric}_distortion"]
            im = axis.imshow(image, cmap="inferno")
            axis.set_title(metric)
            axis.axis("off")
            fig.colorbar(im, ax=axis, fraction=0.046, pad=0.04)
        fig.suptitle(f"Frame {frame}: local distortion maps (shared grid)")
        fig.tight_layout()
        out_path = os.path.join(
            heatmap_dir, f"heatmap_frame_{frame:06d}.png"
        )
        fig.savefig(out_path, dpi=140)
        plt.close(fig)
        paths.append(out_path)
    return paths


def plot_alignment_regions(
    region_dir: str, sample_frames: list[int], out_path: str
) -> None:
    fig, axes = plt.subplots(len(sample_frames), 1, figsize=(10, 3.2 * len(sample_frames)))
    if len(sample_frames) == 1:
        axes = [axes]
    colors = {"psnr_db": (1.0, 0.2, 0.2), "ssim": (0.2, 0.9, 0.3), "msssim": (0.3, 0.5, 1.0)}
    for axis, frame in zip(axes, sample_frames):
        data = np.load(os.path.join(region_dir, f"frame_{frame:06d}.npz"))
        axis.imshow(np.ones((180, 320, 3)), cmap="gray", vmin=0, vmax=1)
        for metric in METRICS:
            grid = np.load(
                os.path.join(region_dir, f"frame_{frame:06d}.npz"),
            )[f"{metric}_distortion"]
            h, w = grid.shape
            hb, wb = h // REGION_BLOCK, w // REGION_BLOCK
            block = grid[: hb * REGION_BLOCK, : wb * REGION_BLOCK].reshape(
                hb, REGION_BLOCK, wb, REGION_BLOCK
            ).mean(axis=(1, 3))
            hot = block >= np.percentile(block, 80.0)
            mask = cv2.resize(
                hot.astype(np.uint8), (w, h), interpolation=cv2.INTER_NEAREST
            ).astype(bool)
            overlay = np.zeros((h, w, 4))
            overlay[mask] = (*colors[metric], 0.35)
            axis.imshow(overlay)
        axis.set_title(f"Frame {frame}: hotspot overlay (R=PSNR G=SSIM B=MS-SSIM)")
        axis.axis("off")
    fig.tight_layout()
    fig.savefig(out_path, dpi=140)
    plt.close(fig)
