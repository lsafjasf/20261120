"""Text-level SRT cutting: cues intersecting [start, end) are kept and their
timestamps shifted so the clip begins at t=0. No codec re-encoding.
"""
from __future__ import annotations

import re
from pathlib import Path

TIME_RE = re.compile(r"(\d{2}):(\d{2}):(\d{2}),(\d{3})")


def to_seconds(ts: str) -> float:
    h, m, s, ms = TIME_RE.fullmatch(ts).groups()
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000


def to_ts(value: float) -> str:
    ms = round(value * 1000)
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, milli = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{milli:03d}"


def cut_srt(src: Path, start: float, end: float, dest: Path) -> int:
    blocks = re.split(r"\n\s*\n", Path(src).read_text(encoding="utf-8").strip())
    out: list[str] = []
    idx = 0
    for block in blocks:
        lines = [ln for ln in block.splitlines() if ln.strip()]
        if len(lines) < 3 or "-->" not in lines[1]:
            continue
        a, b = (x.strip() for x in lines[1].split("-->"))
        lo, hi = to_seconds(a), to_seconds(b)
        clipped_lo, clipped_hi = max(lo, start), min(hi, end)
        if clipped_hi <= clipped_lo:
            continue
        idx += 1
        out.append(
            f"{idx}\n"
            f"{to_ts(clipped_lo - start)} --> {to_ts(clipped_hi - start)}\n"
            + "\n".join(lines[2:])
        )
    dest.write_text("\n\n".join(out) + "\n", encoding="utf-8")
    return idx
