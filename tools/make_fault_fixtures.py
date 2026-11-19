#!/usr/bin/env python3
"""Create deterministic granule-discontinuity variants of the fixtures."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ogg_timeline.ogg import parse_pages, ogg_crc


FIXTURES = ROOT / "fixtures"


def shift_granules(
    source: Path,
    destination: Path,
    *,
    sequence_at: int,
    offset_samples: int,
) -> None:
    """Add an offset to every finite granule from a chosen audio page on.

    Keeping the offset on all later pages leaves exactly one bad boundary:
    the transition into ``sequence_at``.  Every following page keeps its
    original granule delta.
    """

    data = bytearray(source.read_bytes())
    parsed = parse_pages(bytes(data))
    pages = [
        page for page in parsed.pages if page.sequence == sequence_at
    ]
    if len(pages) != 1:
        raise RuntimeError(
            f"{source.name}: expected one page with sequence {sequence_at}"
        )
    start_offset = pages[0].offset

    for page in parsed.pages:
        if page.offset < start_offset or not page.has_granule:
            continue
        value = (page.granule_position + offset_samples).to_bytes(
            8, "little", signed=True
        )
        data[page.offset + 6:page.offset + 14] = value
        data[page.offset + 22:page.offset + 26] = b"\x00\x00\x00\x00"
        checksum = ogg_crc(bytes(data[page.offset:page.offset + page.size]))
        data[page.offset + 22:page.offset + 26] = checksum.to_bytes(4, "little")

    destination.write_bytes(data)


def main() -> None:
    shift_granules(
        FIXTURES / "continuous.vorbis.ogg",
        FIXTURES / "discontinuous.vorbis.ogg",
        sequence_at=5,
        offset_samples=512,
    )
    shift_granules(
        FIXTURES / "continuous.opus.ogg",
        FIXTURES / "discontinuous.opus.ogg",
        sequence_at=6,
        offset_samples=960,
    )
    print("wrote discontinuous fixtures")


if __name__ == "__main__":
    main()
