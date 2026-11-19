#!/usr/bin/env python3
"""Build deterministic media fixtures used by the DRM simulation.

The MP4 is really CENC-encrypted by ffmpeg, so its moov contains a standard
tenc/default_KID field.  The HEVC fixture is extracted from a real x265 stream
and deliberately keeps only VPS and SPS NAL units.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures"
OLD_KID_HEX = "00112233445566778899aabbccddeeff"
OLD_KEY_HEX = "000102030405060708090a0b0c0d0e0f"


def _run(command: list[str]) -> None:
    subprocess.run(command, check=True, stdout=subprocess.DEVNULL)


def _split_annex_b(data: bytes) -> list[tuple[int, int]]:
    starts: list[tuple[int, int]] = []
    index = 0
    while index + 3 < len(data):
        if data[index : index + 4] == b"\x00\x00\x00\x01":
            starts.append((index, 4))
            index += 4
        elif data[index : index + 3] == b"\x00\x00\x01":
            starts.append((index, 3))
            index += 3
        else:
            index += 1
    return starts


def build_mp4(ffmpeg: str, destination: Path, force: bool) -> None:
    if destination.exists() and not force:
        return
    _run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=320x180:rate=12",
            "-t",
            "2",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-g",
            "24",
            "-keyint_min",
            "24",
            "-sc_threshold",
            "0",
            "-encryption_scheme",
            "cenc-aes-ctr",
            "-encryption_key",
            OLD_KEY_HEX,
            "-encryption_kid",
            OLD_KID_HEX,
            "-movflags",
            "+faststart",
            str(destination),
        ]
    )


def build_hevc(ffmpeg: str, destination: Path, force: bool) -> None:
    if destination.exists() and not force:
        return
    with tempfile.TemporaryDirectory(prefix="drm-hevc-") as temp_dir:
        source = Path(temp_dir) / "source.h265"
        _run(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-nostdin",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "testsrc2=size=128x96:rate=10",
                "-t",
                "1",
                "-c:v",
                "libx265",
                "-x265-params",
                "log-level=error:keyint=10",
                "-f",
                "hevc",
                str(source),
            ]
        )
        data = source.read_bytes()

    starts = _split_annex_b(data)
    kept = bytearray()
    for index, (start, start_code_size) in enumerate(starts):
        end = starts[index + 1][0] if index + 1 < len(starts) else len(data)
        nal_type = (data[start + start_code_size] >> 1) & 0x3F
        if nal_type in (32, 33):  # VPS and SPS; intentionally omit PPS (34).
            kept.extend(data[start:end])
    destination.write_bytes(kept)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        parser.error("ffmpeg is required to rebuild fixtures")
    FIXTURES.mkdir(parents=True, exist_ok=True)
    mp4 = FIXTURES / "source_cenc.mp4"
    hevc = FIXTURES / "hevc_vps_sps_no_pps.h265"
    build_mp4(ffmpeg, mp4, args.force)
    build_hevc(ffmpeg, hevc, args.force)
    print(f"{mp4.relative_to(ROOT)}: {mp4.stat().st_size} bytes")
    print(f"{hevc.relative_to(ROOT)}: {hevc.stat().st_size} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
