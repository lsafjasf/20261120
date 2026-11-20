"""Manifest-driven integration pipeline used by the CLI."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from .imageio import load_frame, save_frame
from .integrator import integrate_frames
from .manifest import Manifest
from .weights import ShutterWeight


def _nominal_dt(frame) -> float | None:
    exp = frame.expected_times
    if len(exp) >= 2:
        return float(np.median(np.diff(np.asarray(exp, dtype=np.float64))))
    return None


def integrate_manifest(
    manifest: Manifest,
    weight: ShutterWeight,
    out_dir: "str | Path | None" = None,
    save_png: bool = True,
) -> list[dict]:
    """Integrate every output frame of a manifest.

    Returns per-frame records with coverage and missing-subframe
    info; optionally writes .npy (and preview .png) output frames.
    """
    records = []
    out_path = Path(out_dir) if out_dir else None
    for f in manifest.frames:
        if not f.subframes:
            records.append(
                {
                    "index": f.index,
                    "status": "no_subframes",
                    "missing_times": list(f.expected_times),
                }
            )
            continue
        frames = np.stack(
            [load_frame(manifest.resolve(s)) for s in f.subframes]
        )
        res = integrate_frames(
            frames,
            f.times,
            f.t_open,
            f.t_close,
            weight,
            nominal_dt=_nominal_dt(f),
        )
        rec = {
            "index": f.index,
            "status": "ok" if not f.has_missing else "missing_subframes",
            "coverage": round(res.coverage, 6),
            "missing_times": f.missing_times(),
            "n_subframes": len(f.subframes),
        }
        if out_path:
            npy = out_path / f"frame_{f.index:04d}_{weight.name}.npy"
            save_frame(res.frame, npy)
            rec["output"] = str(npy)
            if save_png:
                png = out_path / f"frame_{f.index:04d}_{weight.name}.png"
                save_frame(res.frame, png)
        records.append(rec)
    if out_path:
        (out_path / f"integrate_{weight.name}.json").write_text(
            json.dumps(records, indent=2)
        )
    return records
