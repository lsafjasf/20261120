"""Generate self-made WAV samples and the three validation SFZ cases.

All samples are synthesized locally (harmonic pluck with a sharp
attack click and a per-take marker tone), so pitch and onset are
unambiguous. Cases:
  case1_velocity_boundary.sfz - two layers split at vel 63/64
  case2_roundrobin_gap.sfz    - seq_length=4 with seq_position 3 missing
  case3_semitone_transpose.sfz- roots offset so playback transposes
  demo_defects.sfz            - uncovered hole + undeclared overlap
                                + missing sample reference (matrix demo)
"""

from __future__ import annotations

from pathlib import Path

from . import audio


def _make_samples(d: Path):
    specs = {
        "soft_c4.wav": (60, 0.0),
        "hard_c4.wav": (60, 5200.0),
        "rr1_c4.wav": (60, 4000.0),
        "rr2_c4.wav": (60, 4500.0),
        "rr4_c4.wav": (60, 5500.0),
        "root_a3.wav": (57, 0.0),
        "root_d4.wav": (62, 0.0),
        "demo_a_c4.wav": (60, 0.0),
        "demo_b_c4.wav": (60, 5000.0),
    }
    for name, (root, tag) in specs.items():
        audio.write_wav(d / name, audio.synth_tone(
            audio.midi_to_freq(root), tag=tag))


CASE1 = """// case1: velocity boundary split at 63/64, keys C4..C5
<region> sample=soft_c4.wav lokey=60 hikey=72 lovel=1   hivel=63  pitch_keycenter=60
<region> sample=hard_c4.wav lokey=60 hikey=72 lovel=64  hivel=127 pitch_keycenter=60
"""

CASE2 = """// case2: round-robin with seq_position 3 missing (gap)
<region> sample=rr1_c4.wav key=60 seq_length=4 seq_position=1
<region> sample=rr2_c4.wav key=60 seq_length=4 seq_position=2
<region> sample=rr4_c4.wav key=60 seq_length=4 seq_position=4
"""

CASE3 = """// case3: semitone transposition around two roots
// root A3 (57) covers G3..B3  -> transpose -2..+2
// root D4 (62) covers C4..E4  -> transpose -2..+2
<region> sample=root_a3.wav lokey=55 hikey=59 pitch_keycenter=57
<region> sample=root_d4.wav lokey=60 hikey=64 pitch_keycenter=62
"""

DEMO = """// demo_defects: intentionally broken instrument for the matrix view
// - keys 64..66 uncovered (hole)
// - keys 68..70: two regions overlap with no lorand/hirand split
// - key 72: references a sample file that does not exist
<region> sample=demo_a_c4.wav lokey=60 hikey=63 pitch_keycenter=60
<region> sample=demo_a_c4.wav lokey=67 hikey=71 pitch_keycenter=60
<region> sample=demo_b_c4.wav lokey=68 hikey=70 pitch_keycenter=60
<region> sample=ghost_missing.wav lokey=72 hikey=72 pitch_keycenter=72
"""


def generate(out_dir: str | Path) -> Path:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    _make_samples(out)
    for name, text in [("case1_velocity_boundary.sfz", CASE1),
                       ("case2_roundrobin_gap.sfz", CASE2),
                       ("case3_semitone_transpose.sfz", CASE3),
                       ("demo_defects.sfz", DEMO)]:
        (out / name).write_text(text, encoding="utf-8")
    return out
