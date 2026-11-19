"""2x2 tile bitrate and visual-boundary verification."""

from __future__ import annotations

import json
import math
from pathlib import Path
import subprocess
from typing import Any

from .media import HEIGHT, WIDTH, decode_rgb_frames
from .mp4 import media_timescale, parse_samples


def _luma(pixel: bytes) -> int:
    return (pixel[0] * 299 + pixel[1] * 587 + pixel[2] * 114) // 1000


def edge_metrics(
    frame: bytes, width: int, height: int, x: int, y: int, tile_width: int, tile_height: int
) -> dict[str, float]:
    total = 0
    count = 0
    strong = 0
    for row in range(y, y + tile_height):
        for column in range(x, x + tile_width):
            index = (row * width + column) * 3
            center = _luma(frame[index : index + 3])
            right = _luma(frame[index + 3 : index + 6]) if column + 1 < x + tile_width else center
            down_index = ((row + 1) * width + column) * 3
            down = (
                _luma(frame[down_index : down_index + 3])
                if row + 1 < y + tile_height
                else center
            )
            gradient = abs(right - center) + abs(down - center)
            total += gradient
            strong += gradient >= 40
            count += 1
    return {
        "edge_energy_per_pixel": total / max(1, count),
        "strong_edge_percent": 100.0 * strong / max(1, count),
    }


def _mae(a: bytes, b: bytes) -> float:
    return sum(abs(left - right) for left, right in zip(a, b)) / max(1, len(a))


def _psnr(mse: float) -> float:
    return 99.0 if mse == 0 else 10.0 * math.log10((255.0 * 255.0) / mse)


def _assemble(tiles: list[dict[str, Any]], decoded: dict[str, bytes]) -> bytes:
    mosaic = bytearray(WIDTH * HEIGHT * 3)
    for tile in tiles:
        frame = decoded[tile["id"]]
        tile_width, tile_height = tile["width"], tile["height"]
        for row in range(tile_height):
            source = row * tile_width * 3
            target = (
                (tile["y"] + row) * WIDTH + tile["x"]
            ) * 3
            mosaic[target : target + tile_width * 3] = frame[source : source + tile_width * 3]
    return bytes(mosaic)


def _seam_and_interior_mae(actual: bytes, expected: bytes) -> tuple[float, float]:
    seam_errors: list[int] = []
    interior_errors: list[int] = []
    seam_rows = {HEIGHT // 2 - 1, HEIGHT // 2}
    seam_columns = {WIDTH // 2 - 1, WIDTH // 2}
    for row in range(HEIGHT):
        for column in range(WIDTH):
            offset = (row * WIDTH + column) * 3
            error = sum(abs(actual[offset + c] - expected[offset + c]) for c in range(3))
            if row in seam_rows or column in seam_columns:
                seam_errors.append(error)
            else:
                interior_errors.append(error)
    return (
        sum(seam_errors) / max(1, len(seam_errors)),
        sum(interior_errors) / max(1, len(interior_errors)),
    )


def _rank(values: list[float]) -> list[int]:
    ordered = sorted(range(len(values)), key=lambda index: values[index])
    ranks = [0] * len(values)
    for rank, index in enumerate(ordered, start=1):
        ranks[index] = rank
    return ranks


def _pearson(xs: list[float], ys: list[float]) -> float:
    if len(xs) < 2:
        return 0.0
    mean_x, mean_y = sum(xs) / len(xs), sum(ys) / len(ys)
    numerator = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    denominator = math.sqrt(
        sum((x - mean_x) ** 2 for x in xs) * sum((y - mean_y) ** 2 for y in ys)
    )
    return numerator / denominator if denominator else 0.0


def _write_ppm(path: Path, frame: bytes, width: int, height: int) -> None:
    path.write_bytes(f"P6\n{width} {height}\n255\n".encode() + frame)


def _render_comparison(source: bytes, mosaic: bytes, target: Path) -> None:
    combined = bytearray(WIDTH * 2 * HEIGHT * 3)
    row_bytes = WIDTH * 3
    for row in range(HEIGHT):
        source_row = source[row * row_bytes : (row + 1) * row_bytes]
        mosaic_row = mosaic[row * row_bytes : (row + 1) * row_bytes]
        offset = row * row_bytes * 2
        combined[offset : offset + row_bytes] = source_row
        combined[offset + row_bytes : offset + row_bytes * 2] = mosaic_row
    ppm = target.with_suffix(".ppm")
    _write_ppm(ppm, bytes(combined), WIDTH * 2, HEIGHT)
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(ppm), str(target)],
        check=True,
    )
    ppm.unlink(missing_ok=True)


def analyse_tiles(
    main_path: Path,
    manifest_path: Path,
    report_path: Path,
    comparison_path: Path,
) -> dict[str, Any]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    tiles = manifest["tiles"]
    geometry_ok = True
    seen_coordinates: set[tuple[int, int]] = set()
    for tile in tiles:
        expected_x = tile["column"] * (WIDTH // manifest["columns"])
        expected_y = tile["row"] * (HEIGHT // manifest["rows"])
        geometry_ok &= tile["x"] == expected_x and tile["y"] == expected_y
        geometry_ok &= (tile["x"], tile["y"]) not in seen_coordinates
        seen_coordinates.add((tile["x"], tile["y"]))

    source_frames = decode_rgb_frames(main_path, [0, 60, 119])
    decoded_tiles: dict[str, dict[int, bytes]] = {}
    for tile in tiles:
        path = Path(tile["path"])
        data = path.read_bytes()
        samples = parse_samples(data)
        timescale = media_timescale(data)
        duration_s = sum(sample.duration for sample in samples) / timescale
        tile["media_bytes"] = sum(sample.size for sample in samples)
        tile["container_bytes"] = len(data)
        tile["duration_s"] = duration_s
        tile["bitrate_kbps"] = tile["media_bytes"] * 8 / duration_s / 1000.0
        tile["sample_count"] = len(samples)
        decoded_tiles[tile["id"]] = decode_rgb_frames(
            path, [0, 60, 119], tile["width"], tile["height"]
        )

    frame_results = []
    for frame_index, source in source_frames.items():
        mosaic = _assemble(tiles, {key: value[frame_index] for key, value in decoded_tiles.items()})
        mse = _mae(source, mosaic) ** 2
        seam_mae, interior_mae = _seam_and_interior_mae(mosaic, source)
        frame_results.append(
            {
                "frame": frame_index,
                "mosaic_psnr_db": _psnr(mse),
                "seam_mae_rgb_sum": seam_mae,
                "interior_mae_rgb_sum": interior_mae,
                "seam_to_interior_ratio": seam_mae / max(1e-9, interior_mae),
            }
        )
        if frame_index == 0:
            _render_comparison(source, mosaic, comparison_path)

    edge_values = []
    for tile in tiles:
        metrics = edge_metrics(
            source_frames[0],
            WIDTH,
            HEIGHT,
            tile["x"],
            tile["y"],
            tile["width"],
            tile["height"],
        )
        tile.update(metrics)
        edge_values.append(metrics["edge_energy_per_pixel"])
    byte_values = [float(tile["media_bytes"]) for tile in tiles]
    spearman = _pearson(
        [float(value) for value in _rank(edge_values)],
        [float(value) for value in _rank(byte_values)],
    )
    mean_seam_ratio = sum(row["seam_to_interior_ratio"] for row in frame_results) / len(
        frame_results
    )
    max_seam_ratio = max(row["seam_to_interior_ratio"] for row in frame_results)
    mean_psnr = sum(row["mosaic_psnr_db"] for row in frame_results) / len(frame_results)
    result = {
        "geometry_aligned": geometry_ok,
        "source": str(main_path),
        "grid": [manifest["rows"], manifest["columns"]],
        "tiles": tiles,
        "frames_checked": frame_results,
        "edge_to_bytes_spearman": spearman,
        "edge_and_bytes_follow_same_rank": spearman >= 0.8,
        "mean_mosaic_psnr_db": mean_psnr,
        "mean_seam_to_interior_ratio": mean_seam_ratio,
        "max_seam_to_interior_ratio": max_seam_ratio,
        "seams_aligned": mean_seam_ratio <= 2.0 and max_seam_ratio <= 3.0,
        "comparison_image": str(comparison_path),
    }
    result["passed"] = (
        result["geometry_aligned"]
        and result["edge_and_bytes_follow_same_rank"]
        and result["seams_aligned"]
        and mean_psnr >= 30.0
    )
    report_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result
