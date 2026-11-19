"""Deterministic WebVTT layout engine (standard library only).

Modules:
- parser:    WEBMARKER / REGION / STYLE / NOTE / cue parsing
- metrics:   font metrics, wrapping and geometry
- engine:    cue placement, overlap stack order, region scrolling
- framerate: script-tag declared fps vs timestamp-derived fps
"""

__all__ = ["parser", "metrics", "engine", "framerate"]
