"""Dual-channel simultaneous-activation sample.

Both channels ('dialogue' and 'system') are active over the same window;
the demo dumps the overlay order at several timestamps and verifies it
against the priority rules in subtitle_overlay.py.
"""
from __future__ import annotations

import json
import os

from subtitle_overlay import SubtitleEvent, SubtitleOverlay

OUT = "artifacts"


def build_overlay() -> SubtitleOverlay:
    overlay = SubtitleOverlay(priorities={"dialogue": 10, "system": 100})
    # both channels active simultaneously during [1.0, 3.0)
    overlay.add_event(SubtitleEvent("dialogue", "对话：你听到底噪了吗？", 1.0, 3.0))
    overlay.add_event(SubtitleEvent("system", "系统：录音电平过低", 1.0, 3.0))
    return overlay


def expected_order(overlay: SubtitleOverlay, t: float) -> list[str]:
    """Independent re-derivation of the rule, used as the check oracle."""
    events = sorted(
        overlay.active_events(t),
        key=lambda e: (overlay.priorities[e.channel], e.start, e.channel),
    )
    return [e.channel for e in events]


def main() -> None:
    os.makedirs(OUT, exist_ok=True)
    overlay = build_overlay()
    frames = []
    ok = True
    for t in (0.5, 1.0, 1.5, 2.5, 3.0):
        order = [e.channel for e in overlay.render_order(t)]
        lines = overlay.render_frame(t)
        consistent = order == expected_order(overlay, t)
        ok = ok and consistent
        frames.append(
            {
                "t": t,
                "bottom_to_top": order,
                "topmost": overlay.top_event(t).channel if order else None,
                "frame": lines,
                "matches_priority_rules": consistent,
            }
        )
    result = {"sample": "dual_channel_simultaneous", "all_frames_consistent": ok, "frames": frames}
    with open(os.path.join(OUT, "subtitle_dual_channel.json"), "w") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    assert ok, "overlay order violates priority rules"


if __name__ == "__main__":
    main()
