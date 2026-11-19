"""Audition keyboard: tracker-style computer-keyboard -> MIDI mapping.

Lower row  z s x d c v g b h n j m  ->  C C# D D# E F F# G G# A A# B
Upper row  q 2 w 3 e r 5 t 6 y 7 u i  ->  one octave higher
Commands:  + / - octave shift, v<1-127> set velocity,
           q quit. Every trigger is appended to an output WAV so the
           session stays audible/reviewable after the fact.
"""

from __future__ import annotations

from . import audio
from .audition import Engine

KEYMAP = {}
for i, ch in enumerate("zsxdcvgbhnjm"):
    KEYMAP[ch] = i
for i, ch in enumerate("q2w3er5t6y7ui"):
    KEYMAP[ch] = i + 12


def describe(events) -> str:
    parts = []
    for ev in events:
        if not ev.sample:
            parts.append(f"  #{ev.seq} {ev.note} v{ev.vel}: NO REGION MATCHED"
                         + (f" (rr slot {ev.rr_position})" if ev.rr_position else ""))
            continue
        f0 = (f"{ev.measured_f0:8.2f}Hz ({ev.f0_cents_err:+.1f}c)"
              if ev.measured_f0 else "n/a")
        rr = f" rr={ev.rr_position}/{ev.rr_length}" if ev.rr_length > 1 else ""
        parts.append(
            f"  #{ev.seq} {ev.note} v{ev.vel}: {ev.sample}"
            f"  transpose={ev.transpose:+d}st{rr}  f0={f0}"
            + ("" if ev.file_exists else "  [FILE MISSING]"))
    return "\n".join(parts)


def run_keyboard(engine: Engine, out_wav: str, start_octave: int = 4,
                 velocity: int = 100):
    print("SFZ audition keyboard - keys z..m / q..i play notes, "
          "+/- octave, v<nn> velocity, q quit")
    rendered: list[list[float]] = []
    octave = start_octave
    base = (octave + 1) * 12
    while True:
        try:
            line = input(f"oct={octave} vel={velocity} > ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not line:
            continue
        if line == "q":
            break
        if line in "+-":
            octave += 1 if line == "+" else -1
            base = (octave + 1) * 12
            continue
        if line.startswith("v") and line[1:].isdigit():
            velocity = max(1, min(127, int(line[1:])))
            continue
        for ch in line:
            if ch not in KEYMAP:
                continue
            key = base + KEYMAP[ch]
            events, buf = engine.trigger(key, velocity)
            print(describe(events))
            if buf:
                rendered.append(buf)
                rendered.append([0.0] * int(0.05 * audio.SR))
    if rendered:
        audio.write_wav(out_wav, [s for t in rendered for s in t])
        print(f"session rendered to {out_wav}")
