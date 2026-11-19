# -*- coding: utf-8 -*-
"""Export the standalone narration track (WAV) and the placement list.

Real productions would concatenate recorded takes; here each placement is
rendered as a distinct tone burst so exported start times can be verified
by envelope onset detection.
"""

from __future__ import annotations

import json
import math
import struct
import wave
from array import array

from .models import ScheduleResult

SAMPLE_RATE = 16000
BASE_FREQ = 330.0
FREQ_STEP = 75.0
AMPLITUDE = 0.6
FADE_S = 0.01


def _tone(freq: float, duration: float) -> array:
    n = int(round(duration * SAMPLE_RATE))
    fade = max(1, int(FADE_S * SAMPLE_RATE))
    out = array("h", [0]) * 0
    out = array("h")
    for i in range(n):
        env = min(1.0, i / fade, (n - 1 - i) / fade)
        v = AMPLITUDE * env * math.sin(2 * math.pi * freq * i / SAMPLE_RATE)
        out.append(int(v * 32767))
    return out


def render_narration_track(res: ScheduleResult, path: str,
                           total_duration: float | None = None) -> None:
    """Write one WAV containing every placed narration at its start time."""
    end = total_duration or 0.0
    for p in res.placements:
        end = max(end, p.end)
    n = int(math.ceil(end * SAMPLE_RATE)) + 1
    mix = [0] * n
    for idx, p in enumerate(res.placements):
        freq = BASE_FREQ + FREQ_STEP * (idx % 8)
        tone = _tone(freq, p.end - p.start)
        off = int(round(p.start * SAMPLE_RATE))
        for j, s in enumerate(tone):
            if off + j < n:
                v = mix[off + j] + s
                mix[off + j] = max(-32768, min(32767, v))
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(array("h", mix).tobytes())


def detect_onsets(path: str, threshold: float = 0.2,
                  min_silence: float = 0.05) -> list[tuple[float, float]]:
    """Detect active regions (start, end) in a WAV via its envelope."""
    with wave.open(path, "rb") as w:
        rate = w.getframerate()
        raw = w.readframes(w.getnframes())
    samples = array("h")
    samples.frombytes(raw)
    if not samples:
        return []
    peak = max(1, max(abs(s) for s in samples))
    limit = threshold * peak
    active = [abs(s) >= limit for s in samples]
    regions: list[list[int]] = []
    i = 0
    n = len(active)
    gap = int(min_silence * rate)
    while i < n:
        if active[i]:
            j = i
            last = i
            while j < n:
                if active[j]:
                    last = j
                    j += 1
                elif j - last <= gap:
                    j += 1
                else:
                    break
            regions.append([i, last])
            i = last + 1
        else:
            i += 1
    return [(a / rate, (b + 1) / rate) for a, b in regions]


def export_placement_list(res: ScheduleResult, path: str) -> None:
    """JSON placement list: placements + rejections + notes."""
    data = {
        "score": {
            "importance_total": res.score[0] if res.score else 0,
            "long_versions": res.score[1] if res.score else 0,
        },
        "placements": [
            {
                "clip_id": p.clip.clip_id,
                "version": p.version,
                "start": round(p.start, 6),
                "end": round(p.end, 6),
                "importance": p.clip.importance,
                "locked": p.locked,
                "text": p.clip.text,
            }
            for p in res.placements
        ],
        "rejections": [
            {"clip_id": r.clip_id, "reason": r.reason}
            for r in res.rejections
        ],
        "notes": res.notes,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
