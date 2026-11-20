"""Replayable sample: synthetic subframes on disk + full pipeline.

Generates photon subframes for the moving-bar and occlusion scenes,
writes a manifest (with one deliberately dropped subframe to show
flagging), integrates with both shutter weights, builds the
side-by-side preview and runs the validation suite.  Everything is
deterministic, so ``python -m shutter_integrator demo --out DIR``
replays the whole sample end to end.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from .imageio import save_frame
from .manifest import Manifest, OutputFrame, SubFrame, save_manifest
from .scenes import MovingBarScene, OcclusionScene

# (scene_name, frame_index, subframe_index) dropped on purpose
DROPPED = ("occlusion", 2, 4)


def generate_sample_data(out_dir, n_frames: int = 4, n_sub: int = 9) -> dict:
    """Write subframes + one manifest per scene; returns their paths."""
    out_dir = Path(out_dir)
    scenes = {"bar": MovingBarScene(), "occlusion": OcclusionScene()}
    paths = {}
    for name, scene in scenes.items():
        frames = []
        for k in range(n_frames):
            expected = [float(k + (i + 0.5) / n_sub) for i in range(n_sub)]
            subs = []
            for i, t in enumerate(expected):
                if (name, k, i) == DROPPED:
                    continue  # simulate a lost render
                rel = f"subframes/{name}/f{k:04d}_s{i:02d}.npy"
                save_frame(scene.frame_at(t), out_dir / rel)
                subs.append(SubFrame(time=t, path=rel))
            frames.append(
                OutputFrame(
                    index=k,
                    t_open=float(k),
                    t_close=float(k + 1),
                    subframes=subs,
                    expected_times=expected,
                )
            )
        manifest = Manifest(frames=frames, root=out_dir)
        path = out_dir / f"manifest_{name}.json"
        save_manifest(manifest, path)
        paths[name] = path
    return paths
