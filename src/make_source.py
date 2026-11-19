#!/usr/bin/env python3
"""Build a deterministic test source MP4:
sharp-edge video + frame counter (numpy frames), AAC audio, mov_text subs.
"""
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from framegen import W, H, FPS, DURATION, raw_stream

ROOT = Path(__file__).resolve().parent.parent
FFMPEG = ROOT / "tools" / "ffmpeg"
OUT = ROOT / "media" / "source.mp4"
SRT = ROOT / "media" / "source.srt"


def write_srt() -> None:
    cues = []
    for i in range(12):
        start = f"00:00:{i:02d},000"
        end = f"00:00:{i + 1:02d},000"
        cues.append(f"{i + 1}\n{start} --> {end}\nsecond {i + 1} of 12\n")
    SRT.write_text("\n".join(cues), encoding="utf-8")


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    write_srt()
    base = ROOT / "media" / "_source_av.mp4"
    cmd = [
        str(FFMPEG), "-y",
        "-f", "rawvideo", "-pix_fmt", "rgb24",
        "-s", f"{W}x{H}", "-r", str(FPS), "-i", "pipe:0",
        "-f", "lavfi", "-i",
        f"sine=frequency=1000:duration={DURATION}",
        "-vf", "format=yuv420p",
        "-map", "0:v", "-map", "1:a",
        "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        # closed GOPs of exactly 30 frames, I/P only (no B-frames) so every
        # stream-copied segment has DTS == PTS and concat joins are gap-free;
        # the default in-loop deblocking stays enabled
        "-x264-params",
        "keyint=30:min-keyint=30:open-gop=0:scenecut=0:bframes=0",
        "-c:a", "aac", "-b:a", "128k", "-ar", "44100",
        str(base),
    ]
    subprocess.run(cmd, input=raw_stream(), check=True)
    mux = [
        str(FFMPEG), "-y", "-i", str(base), "-i", str(SRT),
        "-map", "0:v", "-map", "0:a", "-map", "1:0",
        "-c", "copy", "-c:s", "mov_text",
        "-movflags", "+faststart",
        str(OUT),
    ]
    subprocess.run(mux, check=True)
    base.unlink()
    print(f"wrote {OUT}")


if __name__ == "__main__":
    sys.exit(main())
