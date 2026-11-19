"""Generate deterministic 4:2:0 YUV fixtures for the VVC switch test."""

from __future__ import annotations

import argparse
from pathlib import Path


def _clip(value: int, bit_depth: int) -> int:
    return max(0, min((1 << bit_depth) - 1, value))


def make_frame(width: int, height: int, frame_index: int, bit_depth: int) -> bytes:
    y_plane = bytearray()
    u_plane = bytearray()
    v_plane = bytearray()
    for y in range(height):
        for x in range(width):
            moving = (x + y + frame_index * 3) & 31
            luma = 32 + (moving * 6) + (x * 120 // max(width - 1, 1))
            u = 128 + ((x - frame_index * 2) * 48 // max(width, 1))
            v = 128 + ((y + frame_index) * 48 // max(height, 1))
            y_plane.extend(_clip(luma, bit_depth).to_bytes(2, "little"))
            if not (x & 1) and not (y & 1):
                u_plane.extend(_clip(u, bit_depth).to_bytes(2, "little"))
                v_plane.extend(_clip(v, bit_depth).to_bytes(2, "little"))
    if bit_depth == 8:
        return bytes(y_plane[0::2]) + bytes(u_plane[0::2]) + bytes(v_plane[0::2])
    return bytes(y_plane + u_plane + v_plane)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--width", type=int, required=True)
    parser.add_argument("--height", type=int, required=True)
    parser.add_argument("--bit-depth", type=int, choices=(8, 10), required=True)
    parser.add_argument("--frames", type=int, default=12)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.width % 2 or args.height % 2:
        raise SystemExit("4:2:0 dimensions must be even")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("wb") as output:
        for frame_index in range(args.frames):
            output.write(
                make_frame(
                    args.width, args.height, frame_index, args.bit_depth
                )
            )


if __name__ == "__main__":
    main()
