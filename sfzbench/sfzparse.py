"""Minimal SFZ parser focused on region trigger opcodes.

Extracts, per <region>: key range (lokey/hikey/key), velocity range
(lovel/hivel), root key (pitch_keycenter), round-robin (seq_length/
seq_position) and random split (lorand/hirand). Header inheritance
(<global>/<master>/<group>) is applied in document order.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

NOTE_NAMES = {"c": 0, "c#": 1, "db": 1, "d": 2, "d#": 3, "eb": 3,
              "e": 4, "f": 5, "f#": 6, "gb": 6, "g": 7, "g#": 8,
              "ab": 8, "a": 9, "a#": 10, "bb": 10, "b": 11}

HEADERS = ("global", "master", "group", "region")


def note_to_midi(token: str) -> int:
    """Accept MIDI numbers or names like c4, a#3, eb5 (C4 = 60)."""
    token = token.strip().lower()
    if re.fullmatch(r"-?\d+", token):
        return int(token)
    m = re.fullmatch(r"([a-g](?:#|b)?)(-?\d+)", token)
    if not m or m.group(1) not in NOTE_NAMES:
        raise ValueError(f"bad note token: {token!r}")
    return NOTE_NAMES[m.group(1)] + (int(m.group(2)) + 1) * 12


def midi_to_name(midi: int) -> str:
    names = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
    return f"{names[midi % 12]}{midi // 12 - 1}"


@dataclass
class Region:
    line: int = 0
    sample: str = ""
    lokey: int = 0
    hikey: int = 127
    lovel: int = 1
    hivel: int = 127
    pitch_keycenter: int | None = None  # defaults to lokey when unset
    seq_length: int = 1
    seq_position: int = 1
    lorand: float = 0.0
    hirand: float = 1.0
    opcodes: dict = field(default_factory=dict)

    @property
    def root(self) -> int:
        return self.pitch_keycenter if self.pitch_keycenter is not None else self.lokey

    def covers(self, key: int, vel: int) -> bool:
        return self.lokey <= key <= self.hikey and self.lovel <= vel <= self.hivel


def _tokenize(text: str):
    """Yield (kind, value, lineno); kind is 'header' or 'opcode'."""
    tokens = []
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.split("//", 1)[0]
        for m in re.finditer(r"<(\w+)>|([A-Za-z_]\w*)=([^\s<]+)", line):
            if m.group(1):
                tokens.append(("header", m.group(1).lower(), lineno))
            else:
                tokens.append(("opcode", (m.group(2).lower(), m.group(3)), lineno))
    return tokens


def _build_region(ops: dict, line: int) -> Region:
    r = Region(line=line, opcodes=ops)
    r.sample = ops.get("sample", "")
    if "key" in ops:
        r.lokey = r.hikey = note_to_midi(ops["key"])
        r.pitch_keycenter = r.lokey
    if "lokey" in ops:
        r.lokey = note_to_midi(ops["lokey"])
    if "hikey" in ops:
        r.hikey = note_to_midi(ops["hikey"])
    if "lovel" in ops:
        r.lovel = int(ops["lovel"])
    if "hivel" in ops:
        r.hivel = int(ops["hivel"])
    if "pitch_keycenter" in ops:
        r.pitch_keycenter = note_to_midi(ops["pitch_keycenter"])
    if "seq_length" in ops:
        r.seq_length = max(1, int(ops["seq_length"]))
    if "seq_position" in ops:
        r.seq_position = int(ops["seq_position"])
    if "lorand" in ops:
        r.lorand = float(ops["lorand"])
    if "hirand" in ops:
        r.hirand = float(ops["hirand"])
    return r


def parse_sfz(path: str | Path) -> list[Region]:
    """Parse an .sfz file and return concrete regions with inheritance applied."""
    path = Path(path)
    tokens = _tokenize(path.read_text(encoding="utf-8", errors="replace"))
    inherited: dict[str, str] = {}
    current: dict[str, str] | None = None
    region_line = 0
    regions: list[Region] = []

    for kind, value, lineno in tokens:
        if kind == "header":
            if current is not None:
                regions.append(_build_region(dict(inherited, **current), region_line))
                current = None
            if value == "region":
                current = {}
                region_line = lineno
            continue
        name, val = value
        if current is None:
            inherited[name] = val
        else:
            current[name] = val
    if current is not None:
        regions.append(_build_region(dict(inherited, **current), region_line))
    return regions
