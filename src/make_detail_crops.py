#!/usr/bin/env python3
"""Create side-by-side high-frequency detail crops at the primary point."""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from PIL import Image

from common import ENCODED, FFMPEG, FIGURES, MEDIA, PRIMARY_KBPS, ROOT, run

CROP = "crop=320:180:160:90"


def extract(source: Path, output: Path, frame: int) -> None:
    run(
        [
            str(FFMPEG), "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(source),
            "-vf", f"select=eq(n\\,{frame}),{CROP}",
            "-frames:v", "1", str(output),
        ]
    )


def main() -> None:
    work = ROOT / "artifacts" / "work" / "detail_crops"
    work.mkdir(parents=True, exist_ok=True)
    cases = {
        "static, frame 0": {
            "frame": 0,
            "source": MEDIA / "static.mkv",
            "x264": ENCODED / f"x264_static_{PRIMARY_KBPS}k.h264",
            "x265": ENCODED / f"x265_static_{PRIMARY_KBPS}k.hevc",
        },
        "motion, frame 45": {
            "frame": 45,
            "source": MEDIA / "motion.mkv",
            "x264": ENCODED / f"x264_motion_{PRIMARY_KBPS}k.h264",
            "x265": ENCODED / f"x265_motion_{PRIMARY_KBPS}k.hevc",
        },
    }
    fig, axes = plt.subplots(2, 3, figsize=(12, 5.2))
    for row, (title, paths) in enumerate(cases.items()):
        for column, (encoder, path) in enumerate(
            (("source", paths["source"]), ("x264", paths["x264"]), ("x265", paths["x265"]))
        ):
            png = work / f"{row}_{encoder}.png"
            extract(path, png, int(paths["frame"]))
            image = np.asarray(Image.open(png).convert("RGB"))
            axes[row, column].imshow(image, interpolation="nearest")
            axes[row, column].set_title(f"{encoder} | {title}", fontsize=10)
            axes[row, column].axis("off")
    fig.suptitle(f"High-frequency detail crops at ~{PRIMARY_KBPS} kbps")
    fig.tight_layout()
    output = FIGURES / "primary_detail_crops.png"
    fig.savefig(output, dpi=180)
    plt.close(fig)
    print(output.relative_to(ROOT))


if __name__ == "__main__":
    main()
