#!/usr/bin/env python3
"""Generate deterministic media, run all checks, and write reports."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mp4_stream_lab.experiment import run_experiment  # noqa: E402
from mp4_stream_lab.media import generate_main_stream, generate_tile_streams  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--reuse-media",
        action="store_true",
        help="reuse existing artifacts/media and artifacts/tiles",
    )
    args = parser.parse_args()

    main_stream = ROOT / "artifacts/media/source-fragmented.mp4"
    manifest = ROOT / "artifacts/tiles/manifest.json"
    if not args.reuse_media or not main_stream.exists() or not manifest.exists():
        generate_main_stream(main_stream)
        generate_tile_streams(main_stream, ROOT / "artifacts/tiles")

    report = run_experiment(ROOT)
    startup = report["startup"]
    integrity = report["integrity"]
    print(
        "chunked playback: "
        f"{startup['chunked']['playback_ready_ms']:.2f} ms; "
        "whole playback: "
        f"{startup['whole']['playback_ready_ms']:.2f} ms"
    )
    print(
        f"samples={integrity['sample_count']}, "
        f"cross-boundary={integrity['samples_spanning_chunk_boundaries']}, "
        f"duplicates={len(integrity['duplicate_sample_keys'])}, "
        f"integrity={'PASS' if integrity['passed'] else 'FAIL'}"
    )
    print(f"tiles={'PASS' if report['tiles']['passed'] else 'FAIL'}")
    print(f"report={ROOT / 'reports/streaming_report.md'}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
