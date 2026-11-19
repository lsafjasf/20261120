#!/usr/bin/env python3
"""Static guardrails for the normal (non-test) listening presentation."""

from __future__ import annotations

import sys
from pathlib import Path


def main() -> int:
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent.parent
    app = root / "Source" / "App"
    main_component = (app / "MainComponent.cpp").read_text(encoding="utf-8")
    timeline = (app / "TimelineComponent.cpp").read_text(encoding="utf-8")
    engine = (app / "AudioPlaybackEngine.cpp").read_text(encoding="utf-8")
    failures: list[str] = []

    for forbidden in ("getFileName", "getFullPathName"):
        if forbidden in main_component:
            failures.append(f"MainComponent exposes {forbidden}")

    for forbidden in ("AudioThumbnail", "getMagnitude", "drawWaveform"):
        if forbidden in timeline or forbidden in main_component:
            failures.append(f"normal UI contains waveform API {forbidden}")

    if "xSource" in main_component:
        failures.append("MainComponent references the secret xSource name")

    for line in main_component.splitlines():
        if "secretSourceForCurrentTrial" in line and "setXUsesA" not in line:
            failures.append("secretSourceForCurrentTrial used outside audio mapping")

    if "sampleAt" not in engine or "xUsesA_" not in engine:
        failures.append("X audio mapping is not isolated in the playback engine")

    if failures:
        for failure in failures:
            print(f"FAIL: {failure}")
        return 1

    print("UI privacy guardrails passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
