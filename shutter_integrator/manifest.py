"""Frame manifest: maps output frames to their photon subframes.

The manifest records, per output frame, the shutter interval and the
available subframes (sample time + image path).  ``expected_times``
describes the subframes the renderer was supposed to deliver; any
expected time without a matching subframe is reported as missing so
the affected output frame can be flagged.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

TIME_ATOL = 1e-6


@dataclass
class SubFrame:
    time: float
    path: str


@dataclass
class OutputFrame:
    index: int
    t_open: float
    t_close: float
    subframes: List[SubFrame] = field(default_factory=list)
    expected_times: List[float] = field(default_factory=list)

    @property
    def times(self) -> List[float]:
        return [sf.time for sf in self.subframes]

    def missing_times(self, atol: float = TIME_ATOL) -> List[float]:
        """Expected sample times that have no matching subframe."""
        have = self.times
        return [
            te
            for te in self.expected_times
            if not any(abs(te - th) <= atol for th in have)
        ]

    def extra_times(self, atol: float = TIME_ATOL) -> List[float]:
        """Subframe times that were never expected (informational)."""
        exp = self.expected_times
        if not exp:
            return []
        return [
            th
            for th in self.times
            if not any(abs(th - te) <= atol for te in exp)
        ]

    @property
    def has_missing(self) -> bool:
        return bool(self.missing_times())


@dataclass
class Manifest:
    frames: List[OutputFrame]
    root: Path = field(default_factory=Path.cwd)

    def resolve(self, sub: SubFrame) -> Path:
        p = Path(sub.path)
        return p if p.is_absolute() else self.root / p

    def flagged_frames(self) -> List[int]:
        """Indices of output frames affected by missing subframes."""
        return [f.index for f in self.frames if f.has_missing]


def load_manifest(path: "str | Path") -> Manifest:
    path = Path(path)
    data = json.loads(path.read_text())
    frames = []
    for entry in data["frames"]:
        shutter = entry["shutter"]
        subs = [
            SubFrame(time=float(s["time"]), path=str(s["path"]))
            for s in entry.get("subframes", [])
        ]
        subs.sort(key=lambda s: s.time)
        frames.append(
            OutputFrame(
                index=int(entry["index"]),
                t_open=float(shutter[0]),
                t_close=float(shutter[1]),
                subframes=subs,
                expected_times=[
                    float(t) for t in entry.get("expected_times", [])
                ],
            )
        )
    frames.sort(key=lambda f: f.index)
    return Manifest(frames=frames, root=path.parent)


def save_manifest(manifest: Manifest, path: "str | Path") -> None:
    path = Path(path)
    data = {
        "frames": [
            {
                "index": f.index,
                "shutter": [f.t_open, f.t_close],
                "subframes": [
                    {"time": s.time, "path": s.path} for s in f.subframes
                ],
                "expected_times": list(f.expected_times),
            }
            for f in manifest.frames
        ]
    }
    path.write_text(json.dumps(data, indent=2))
