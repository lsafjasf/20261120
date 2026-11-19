"""Deterministic synthetic video generation for the transport experiment."""

from __future__ import annotations

from pathlib import Path
import json
import subprocess


WIDTH = 640
HEIGHT = 360
FPS = 30
FRAME_COUNT = 120
TILE_COLUMNS = 2
TILE_ROWS = 2


def _pixel(frame: bytearray, width: int, x: int, y: int, rgb: tuple[int, int, int]) -> None:
    offset = (y * width + x) * 3
    frame[offset : offset + 3] = bytes(rgb)


def _fill_rect(
    frame: bytearray,
    width: int,
    height: int,
    x0: int,
    y0: int,
    x1: int,
    y1: int,
    rgb: tuple[int, int, int],
) -> None:
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(width, x1), min(height, y1)
    if x0 >= x1 or y0 >= y1:
        return
    row = bytes(rgb) * (x1 - x0)
    for y in range(y0, y1):
        offset = (y * width + x0) * 3
        frame[offset : offset + len(row)] = row


def make_frame(frame_index: int, width: int = WIDTH, height: int = HEIGHT) -> bytes:
    """Create four quadrants with deliberately different edge densities."""
    frame = bytearray(width * height * 3)
    mid_x, mid_y = width // 2, height // 2
    shift = frame_index * 2

    for y in range(height):
        for x in range(width):
            if y < mid_y and x < mid_x:
                checker = ((x + shift) // 12 + y // 12) & 1
                value = 228 if checker else 24
                color = (value, 48 + (y * 80 // mid_y), 220 - value // 3)
            elif y < mid_y:
                color = (20, 95 + (y * 35 // mid_y), 150)
            elif x < mid_x:
                stripe = ((x + y + shift * 2) // 9) & 1
                color = (245, 180 if stripe else 85, 30)
            else:
                glow = (x - mid_x) * 150 // max(1, width - mid_x)
                color = (35 + glow, 22 + y * 60 // mid_y, 90 + glow // 2)
            offset = (y * width + x) * 3
            frame[offset : offset + 3] = bytes(color)

    box_x = 45 + (shift * 3) % max(1, mid_x - 100)
    box_y = 28 + (frame_index * 2) % max(1, mid_y - 60)
    _fill_rect(frame, width, height, box_x, box_y, box_x + 72, box_y + 44, (255, 220, 15))
    _fill_rect(
        frame,
        width,
        height,
        mid_x + 190 - (shift * 2) % 140,
        mid_y + 90,
        mid_x + 255 - (shift * 2) % 140,
        mid_y + 145,
        (245, 80, 120),
    )
    return bytes(frame)


def _run(command: list[str], *, stdin: bytes | None = None) -> None:
    process = subprocess.run(
        command,
        input=stdin,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        check=False,
    )
    if process.returncode != 0:
        message = process.stderr.decode("utf-8", "replace")[-2000:]
        raise RuntimeError(f"ffmpeg failed ({process.returncode}):\n{message}")


def generate_main_stream(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = b"".join(make_frame(index) for index in range(FRAME_COUNT))
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "-s",
        f"{WIDTH}x{HEIGHT}",
        "-r",
        str(FPS),
        "-i",
        "-",
        "-an",
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "21",
        "-g",
        "30",
        "-keyint_min",
        "30",
        "-sc_threshold",
        "0",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+frag_keyframe+empty_moov+default_base_moof",
        "-f",
        "mp4",
        str(path),
    ]
    _run(command, stdin=raw)


def generate_tile_streams(source: Path, output_dir: Path) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    tile_width, tile_height = WIDTH // TILE_COLUMNS, HEIGHT // TILE_ROWS
    manifest = {"rows": TILE_ROWS, "columns": TILE_COLUMNS, "tiles": []}
    for row in range(TILE_ROWS):
        for column in range(TILE_COLUMNS):
            x, y = column * tile_width, row * tile_height
            target = output_dir / f"tile-r{row}c{column}.mp4"
            command = [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(source),
                "-an",
                "-vf",
                f"crop={tile_width}:{tile_height}:{x}:{y}",
                "-c:v",
                "libx264",
                "-preset",
                "medium",
                "-crf",
                "20",
                "-g",
                "30",
                "-keyint_min",
                "30",
                "-sc_threshold",
                "0",
                "-pix_fmt",
                "yuv420p",
                "-movflags",
                "+frag_keyframe+empty_moov+default_base_moof",
                "-f",
                "mp4",
                str(target),
            ]
            _run(command)
            manifest["tiles"].append(
                {
                    "id": f"r{row}c{column}",
                    "row": row,
                    "column": column,
                    "x": x,
                    "y": y,
                    "width": tile_width,
                    "height": tile_height,
                    "path": str(target),
                }
            )
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def decode_rgb_frames(
    path: Path,
    frame_indices: list[int],
    width: int = WIDTH,
    height: int = HEIGHT,
) -> dict[int, bytes]:
    """Decode selected frames from a video as tightly packed RGB24."""
    wanted = set(frame_indices)
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(path),
        "-frames:v",
        str(max(wanted) + 1),
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "-",
    ]
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert process.stdout is not None
    frame_size = width * height * 3
    decoded: dict[int, bytes] = {}
    index = 0
    while True:
        frame = process.stdout.read(frame_size)
        if len(frame) < frame_size:
            break
        if index in wanted:
            decoded[index] = frame
        index += 1
        if index > max(wanted):
            break
    process.stdout.close()
    stderr = process.stderr.read() if process.stderr else b""
    if process.wait() != 0:
        raise RuntimeError(stderr.decode("utf-8", "replace")[-2000:])
    if process.stderr:
        process.stderr.close()
    return decoded
