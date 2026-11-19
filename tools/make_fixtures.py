#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from stereo_preflight.fixtures import TestPattern, write_test_video


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate stereo preflight test clips")
    parser.add_argument("output_dir", nargs="?", default="artifacts/fixtures")
    parser.add_argument("--frames", type=int, default=48)
    parser.add_argument("--packing", choices=("sbs", "ou"), default="sbs")
    args = parser.parse_args()
    output = Path(args.output_dir)
    cases = {
        "normal": TestPattern(packing=args.packing, frames=args.frames),
        "swapped_eyes": TestPattern(packing=args.packing, frames=args.frames, swap_eyes=True),
        "dropped_frame": TestPattern(
            packing=args.packing,
            frames=args.frames,
            drop_second_at=args.frames // 3,
        ),
    }
    for name, pattern in cases.items():
        path = output / f"{name}_{args.packing}.mp4"
        write_test_video(path, pattern)
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
