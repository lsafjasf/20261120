from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from .core import _as_3x3


@dataclass
class SyntheticPath:
    angle_deg: np.ndarray
    offset_x: np.ndarray
    offset_y: np.ndarray
    scale: np.ndarray


def _sine(t: np.ndarray, amplitude: float, frequency: float, phase: float = 0.0) -> np.ndarray:
    return amplitude * np.sin(2.0 * np.pi * frequency * t + phase)


def rotation_path(frame_count: int, fps: float) -> SyntheticPath:
    t = np.arange(frame_count, dtype=np.float64) / fps
    return SyntheticPath(
        angle_deg=(
            _sine(t, 3.2, 0.32)
            + _sine(t, 1.15, 2.15, 0.4)
            + _sine(t, 0.65, 3.45, 1.1)
        ),
        offset_x=_sine(t, 14.0, 0.26) + _sine(t, 3.6, 2.55, 0.8),
        offset_y=_sine(t, 9.0, 0.41, 1.2) + _sine(t, 2.8, 3.05, 0.2),
        scale=1.0 + _sine(t, 0.004, 0.37),
    )


def fast_path(frame_count: int, fps: float) -> SyntheticPath:
    t = np.arange(frame_count, dtype=np.float64) / fps
    return SyntheticPath(
        angle_deg=_sine(t, 0.9, 0.48) + _sine(t, 0.45, 2.8, 0.6),
        offset_x=_sine(t, 175.0, 0.52) + _sine(t, 7.0, 3.1, 0.1),
        offset_y=_sine(t, 54.0, 0.77) + _sine(t, 4.0, 4.2, 0.9),
        scale=1.0 + _sine(t, 0.002, 0.65),
    )


def fine_line_path(frame_count: int, fps: float) -> SyntheticPath:
    t = np.arange(frame_count, dtype=np.float64) / fps
    return SyntheticPath(
        angle_deg=_sine(t, 1.8, 0.3) + _sine(t, 0.45, 2.4, 0.5),
        offset_x=_sine(t, 18.0, 0.35) + _sine(t, 1.7, 2.9, 0.3),
        offset_y=_sine(t, 11.0, 0.46, 0.7) + _sine(t, 1.5, 3.35, 1.0),
        scale=1.0 + _sine(t, 0.003, 0.4),
    )


def _noise_texture(width: int, height: int, rng: np.random.Generator) -> np.ndarray:
    small = rng.normal(0.0, 24.0, (height // 4, width // 4, 3))
    return cv2.resize(small, (width, height), interpolation=cv2.INTER_CUBIC)


def make_feature_scene(width: int, height: int, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    x_gradient = np.linspace(0.0, 1.0, width, dtype=np.float32)[None, :, None]
    y_gradient = np.linspace(0.0, 1.0, height, dtype=np.float32)[:, None, None]
    base = np.empty((height, width, 3), dtype=np.float32)
    base[..., 0] = (54.0 + 76.0 * x_gradient + 22.0 * y_gradient)[..., 0]
    base[..., 1] = (83.0 + 48.0 * y_gradient)[..., 0]
    base[..., 2] = (64.0 + 54.0 * (1.0 - x_gradient))[..., 0]
    base += _noise_texture(width, height, rng)
    scene = np.clip(base, 8, 247).astype(np.uint8)

    palette = [(32, 190, 235), (228, 108, 42), (52, 212, 90), (240, 230, 35)]
    for _ in range(85):
        center = (int(rng.integers(0, width)), int(rng.integers(0, height)))
        radius = int(rng.integers(4, 27))
        color = tuple(int(value) for value in palette[int(rng.integers(0, len(palette)))])
        cv2.circle(scene, center, radius, color, int(rng.integers(1, 5)), cv2.LINE_AA)
        cv2.circle(scene, center, max(1, radius // 5), (20, 20, 20), -1, cv2.LINE_AA)
    for _ in range(55):
        start = (int(rng.integers(0, width)), int(rng.integers(0, height)))
        end = (int(rng.integers(0, width)), int(rng.integers(0, height)))
        color = tuple(int(value) for value in palette[int(rng.integers(0, len(palette)))])
        cv2.line(scene, start, end, color, int(rng.integers(1, 4)), cv2.LINE_AA)
    for row in range(80, height, 120):
        for column in range(100, width, 150):
            cv2.rectangle(
                scene,
                (column, row),
                (column + 42, row + 28),
                (245, 235, 215),
                2,
                cv2.LINE_AA,
            )
    return scene


def make_fine_line_scene(width: int, height: int) -> np.ndarray:
    scene = np.full((height, width, 3), 236, dtype=np.uint8)
    for x in range(20, width, 10):
        color = (24, 24, 24) if (x // 10) % 5 else (52, 82, 172)
        cv2.line(scene, (x, 12), (x, height - 12), color, 1, cv2.LINE_8)
    for y in range(20, height, 10):
        color = (32, 32, 32) if (y // 10) % 5 else (52, 120, 62)
        cv2.line(scene, (12, y), (width - 12, y), color, 1, cv2.LINE_8)
    for index in range(12):
        x = 45 + index * 105
        cv2.line(scene, (x, 60), (x, height - 60), (8, 8, 8), 2, cv2.LINE_8)
        cv2.putText(
            scene,
            f"{index + 1}",
            (x + 5, 48),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            (20, 20, 20),
            1,
            cv2.LINE_AA,
        )
    cv2.rectangle(scene, (230, 120), (750, 470), (28, 28, 28), 2, cv2.LINE_8)
    return scene


def camera_matrix(
    position: int,
    path: SyntheticPath,
    frame_center: tuple[float, float],
    scene_center: tuple[float, float],
) -> np.ndarray:
    angle = np.radians(float(path.angle_deg[position]))
    scale = float(path.scale[position])
    cosine = scale * np.cos(angle)
    sine = scale * np.sin(angle)
    destination = np.array(
        [
            frame_center[0] + path.offset_x[position],
            frame_center[1] + path.offset_y[position],
        ]
    )
    return _as_3x3(
        np.array(
            [
                [cosine, -sine, destination[0] - cosine * scene_center[0] + sine * scene_center[1]],
                [sine, cosine, destination[1] - sine * scene_center[0] - cosine * scene_center[1]],
            ]
        )
    )


def _render_scene_video(
    scene: np.ndarray,
    path: SyntheticPath,
    output_path: Path,
    width: int,
    height: int,
    fps: float,
) -> dict:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    frame_center = (width / 2.0, height / 2.0)
    scene_center = (scene.shape[1] / 2.0, scene.shape[0] / 2.0)
    writer = cv2.VideoWriter(
        str(output_path),
        cv2.VideoWriter_fourcc(*"FFV1"),
        fps,
        (width, height),
    )
    if not writer.isOpened():
        raise RuntimeError(f"cannot create synthetic video: {output_path}")
    matrices = []
    coverage_min = 255
    scene_mask = np.full(scene.shape[:2], 255, dtype=np.uint8)
    try:
        for index in range(len(path.angle_deg)):
            matrix = camera_matrix(index, path, frame_center, scene_center)
            frame = cv2.warpAffine(
                scene,
                matrix[:2],
                (width, height),
                flags=cv2.INTER_LANCZOS4,
                borderMode=cv2.BORDER_CONSTANT,
                borderValue=(0, 0, 0),
            )
            mask = cv2.warpAffine(
                scene_mask,
                matrix[:2],
                (width, height),
                flags=cv2.INTER_NEAREST,
                borderMode=cv2.BORDER_CONSTANT,
                borderValue=0,
            )
            coverage_min = min(coverage_min, int(mask.min()))
            writer.write(frame)
            matrices.append(matrix)
    finally:
        writer.release()
    if coverage_min < 255:
        raise RuntimeError(f"synthetic camera path exposes scene border: {output_path}")

    truth_path = output_path.with_name(output_path.stem + "_truth.npz")
    np.savez_compressed(
        truth_path,
        matrices=np.asarray(matrices),
        angle_deg=path.angle_deg,
        offset_x=path.offset_x,
        offset_y=path.offset_y,
        scale=path.scale,
        fps=fps,
    )
    return {
        "video": str(output_path),
        "truth": str(truth_path),
        "frames": len(matrices),
        "fps": fps,
        "width": width,
        "height": height,
        "scene_width": scene.shape[1],
        "scene_height": scene.shape[0],
        "coverage_min": coverage_min,
    }


def generate_synthetic_suite(output_dir: str | Path) -> dict[str, dict]:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    width, height, fps, frame_count = 640, 360, 30.0, 108
    definitions = {
        "rotation": (
            make_feature_scene(1200, 720, seed=41),
            rotation_path(frame_count, fps),
        ),
        "fast_motion": (
            make_feature_scene(1500, 900, seed=42),
            fast_path(frame_count, fps),
        ),
        "fine_lines": (
            make_fine_line_scene(1200, 720),
            fine_line_path(frame_count, fps),
        ),
    }
    manifest = {"fps": fps, "width": width, "height": height, "videos": {}}
    for name, (scene, path) in definitions.items():
        output_path = output_dir / f"{name}.avi"
        manifest["videos"][name] = _render_scene_video(
            scene, path, output_path, width, height, fps
        )
    manifest_path = output_dir / "synthetic_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest
