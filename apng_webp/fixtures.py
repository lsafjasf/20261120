from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass
from pathlib import Path

from .model import FrameControl
from .png import PNG_SIGNATURE


@dataclass(frozen=True)
class FixtureFrame:
    x: int
    y: int
    width: int
    height: int
    delay_num: int
    delay_den: int
    dispose_op: int
    blend_op: int
    pixels: bytes


def _chunk(chunk_type: bytes, data: bytes) -> bytes:
    checksum = zlib.crc32(chunk_type + data) & 0xFFFFFFFF
    return (
        struct.pack(">I", len(data))
        + chunk_type
        + data
        + struct.pack(">I", checksum)
    )


def _compressed_rgba(pixels: bytes, width: int, height: int) -> bytes:
    if len(pixels) != width * height * 4:
        raise ValueError("RGBA fixture buffer has the wrong size")
    rows = bytearray()
    stride = width * 4
    for y in range(height):
        rows.append(0)
        rows.extend(pixels[y * stride : (y + 1) * stride])
    return zlib.compress(bytes(rows), 9)


def write_apng(
    path: Path | str,
    canvas_width: int,
    canvas_height: int,
    frames: list[FixtureFrame],
    num_plays: int,
) -> None:
    if not frames:
        raise ValueError("an APNG fixture needs at least one frame")
    first = frames[0]
    if first.width != canvas_width or first.height != canvas_height:
        raise ValueError("fixture frame zero must cover the whole canvas")

    output = bytearray(PNG_SIGNATURE)
    ihdr = struct.pack(
        ">IIBBBBB", canvas_width, canvas_height, 8, 6, 0, 0, 0
    )
    output.extend(_chunk(b"IHDR", ihdr))
    output.extend(_chunk(b"acTL", struct.pack(">II", len(frames), num_plays)))

    sequence = 0
    for index, frame in enumerate(frames):
        control = struct.pack(
            ">IIIIIHHBB",
            sequence,
            frame.width,
            frame.height,
            frame.x,
            frame.y,
            frame.delay_num,
            frame.delay_den,
            frame.dispose_op,
            frame.blend_op,
        )
        output.extend(_chunk(b"fcTL", control))
        sequence += 1
        compressed = _compressed_rgba(
            frame.pixels, frame.width, frame.height
        )
        if index == 0:
            output.extend(_chunk(b"IDAT", compressed))
        else:
            output.extend(
                _chunk(b"fdAT", struct.pack(">I", sequence) + compressed)
            )
            sequence += 1
    output.extend(_chunk(b"IEND", b""))
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(output)


def solid(
    width: int, height: int, color: tuple[int, int, int, int]
) -> bytearray:
    return bytearray(bytes(color) * (width * height))


def fill_rect(
    canvas: bytearray,
    canvas_width: int,
    x: int,
    y: int,
    width: int,
    height: int,
    color: tuple[int, int, int, int],
) -> None:
    row = bytes(color) * width
    for row_index in range(y, y + height):
        start = (row_index * canvas_width + x) * 4
        canvas[start : start + len(row)] = row


def subimage(
    canvas: bytes,
    canvas_width: int,
    x: int,
    y: int,
    width: int,
    height: int,
) -> bytes:
    result = bytearray()
    for row in range(y, y + height):
        start = (row * canvas_width + x) * 4
        result.extend(canvas[start : start + width * 4])
    return bytes(result)


def _rgba(
    width: int,
    height: int,
    background: tuple[int, int, int, int],
    rectangles: list[tuple[int, int, int, int, tuple[int, int, int, int]]],
) -> bytes:
    canvas = solid(width, height, background)
    for x, y, rect_width, rect_height, color in rectangles:
        fill_rect(canvas, width, x, y, rect_width, rect_height, color)
    return bytes(canvas)


def _offset_subframe() -> tuple[int, int, list[FixtureFrame], int]:
    width, height = 48, 32
    base = _rgba(
        width,
        height,
        (18, 24, 32, 255),
        [
            (0, 0, 24, 8, (220, 70, 50, 255)),
            (24, 24, 24, 8, (50, 150, 220, 255)),
        ],
    )
    first_patch = _rgba(
        12,
        10,
        (240, 190, 40, 220),
        [(2, 2, 5, 4, (255, 255, 255, 255))],
    )
    second_patch = _rgba(
        8, 8, (140, 60, 210, 255), [(2, 2, 3, 3, (20, 240, 180, 160))]
    )
    third_patch = _rgba(
        4, 4, (255, 255, 255, 96), [(1, 1, 2, 2, (255, 40, 60, 230))]
    )
    frames = [
        FixtureFrame(0, 0, width, height, 1, 10, 0, 0, base),
        FixtureFrame(20, 6, 12, 10, 2, 10, 0, 0, first_patch),
        FixtureFrame(34, 18, 8, 8, 3, 10, 1, 0, second_patch),
        FixtureFrame(2, 2, 4, 4, 1, 10, 0, 1, third_patch),
    ]
    return width, height, frames, 3


def _alpha_overlay() -> tuple[int, int, list[FixtureFrame], int]:
    width, height = 40, 40
    base = _rgba(
        width,
        height,
        (0, 0, 0, 0),
        [
            (4, 4, 16, 16, (200, 40, 60, 255)),
            (24, 8, 10, 24, (40, 180, 90, 120)),
        ],
    )
    shifted = _rgba(
        width,
        height,
        (0, 0, 0, 0),
        [
            (8, 10, 14, 14, (30, 120, 240, 160)),
            (24, 8, 10, 24, (40, 180, 90, 120)),
        ],
    )
    overlay = _rgba(
        16,
        16,
        (255, 210, 20, 128),
        [
            (0, 0, 8, 8, (20, 20, 20, 255)),
            (8, 8, 8, 8, (220, 40, 220, 80)),
        ],
    )
    transparent_edges = _rgba(
        10,
        10,
        (0, 0, 0, 0),
        [(3, 3, 4, 4, (255, 255, 255, 160))],
    )
    frames = [
        FixtureFrame(0, 0, width, height, 1, 4, 0, 0, base),
        FixtureFrame(0, 0, width, height, 1, 4, 0, 1, shifted),
        FixtureFrame(12, 12, 16, 16, 1, 4, 0, 1, overlay),
        FixtureFrame(5, 5, 10, 10, 1, 4, 0, 1, transparent_edges),
    ]
    return width, height, frames, 0


def _restore_previous() -> tuple[int, int, list[FixtureFrame], int]:
    width, height = 32, 32
    base = _rgba(
        width,
        height,
        (30, 32, 40, 255),
        [(8, 8, 16, 16, (220, 180, 40, 255))],
    )
    patch = _rgba(
        14,
        14,
        (10, 200, 220, 210),
        [(3, 3, 8, 8, (240, 40, 60, 255))],
    )
    overlay = _rgba(
        6,
        6,
        (255, 255, 255, 100),
        [(2, 2, 2, 2, (10, 10, 10, 255))],
    )
    frames = [
        FixtureFrame(0, 0, width, height, 2, 10, 0, 0, base),
        FixtureFrame(9, 9, 14, 14, 2, 10, 2, 0, patch),
        FixtureFrame(1, 1, 6, 6, 2, 10, 0, 1, overlay),
    ]
    return width, height, frames, 2


def _fractional_delay() -> tuple[int, int, list[FixtureFrame], int]:
    width, height = 16, 16
    frames = []
    for index in range(6):
        x = index * 2
        pixels = _rgba(
            width,
            height,
            (10, 10, 16, 255),
            [(x, 4, 3, 8, (20 + index * 35, 220, 80, 255))],
        )
        frames.append(
            FixtureFrame(0, 0, width, height, 1, 6, 0, 0, pixels)
        )
    return width, height, frames, 1


def _zero_delay() -> tuple[int, int, list[FixtureFrame], int]:
    width, height = 16, 16
    first = _rgba(
        width, height, (30, 30, 30, 255), [(2, 2, 8, 8, (240, 80, 40, 255))]
    )
    second = _rgba(
        4, 4, (255, 255, 255, 180), [(1, 1, 2, 2, (20, 40, 240, 255))]
    )
    third = _rgba(
        width, height, (30, 30, 30, 255), [(6, 6, 8, 8, (40, 220, 120, 255))]
    )
    frames = [
        FixtureFrame(0, 0, width, height, 1, 10, 0, 0, first),
        FixtureFrame(6, 6, 4, 4, 1, 10000, 0, 0, second),
        FixtureFrame(0, 0, width, height, 1, 10, 0, 0, third),
    ]
    return width, height, frames, 0


def _stress_mixed() -> tuple[int, int, list[FixtureFrame], int]:
    width, height = 24, 20
    base = _rgba(
        width,
        height,
        (20, 22, 28, 255),
        [
            (1, 1, 10, 5, (210, 40, 50, 255)),
            (14, 13, 8, 5, (40, 120, 220, 200)),
        ],
    )
    patch_two = _rgba(
        5, 7, (240, 210, 30, 180), [(1, 2, 3, 3, (255, 255, 255, 255))]
    )
    patch_three = _rgba(
        6, 4, (80, 220, 140, 120), [(2, 1, 2, 2, (10, 30, 10, 255))]
    )
    patch_four = _rgba(
        3, 3, (240, 60, 160, 255), [(1, 1, 1, 1, (255, 255, 255, 128))]
    )
    patch_five = _rgba(4, 4, (30, 30, 30, 0), [(1, 1, 2, 2, (250, 120, 20, 220))])
    patch_six = _rgba(
        5, 5, (180, 180, 180, 100), [(1, 1, 3, 3, (10, 10, 10, 255))]
    )
    frames = [
        FixtureFrame(0, 0, width, height, 1, 10, 0, 0, base),
        FixtureFrame(3, 5, 5, 7, 1, 10, 0, 0, patch_two),
        FixtureFrame(7, 9, 6, 4, 3, 20, 0, 1, patch_three),
        FixtureFrame(1, 1, 3, 3, 1, 20, 0, 1, patch_four),
        FixtureFrame(15, 11, 4, 4, 1, 10, 1, 0, patch_five),
        FixtureFrame(2, 2, 5, 5, 1, 10, 2, 0, patch_six),
    ]
    return width, height, frames, 5


BUILTIN_FIXTURES = {
    "offset_subframe": _offset_subframe,
    "alpha_overlay": _alpha_overlay,
    "restore_previous": _restore_previous,
    "fractional_delay": _fractional_delay,
    "zero_delay": _zero_delay,
    "stress_mixed": _stress_mixed,
}


def build_builtin_fixtures(output_dir: Path | str) -> list[Path]:
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for name, builder in BUILTIN_FIXTURES.items():
        width, height, frames, loops = builder()
        path = destination / f"{name}.apng"
        write_apng(path, width, height, frames, loops)
        paths.append(path)
    return paths
