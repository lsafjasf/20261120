"""Audition engine: resolves SFZ trigger rules and renders playable audio.

Trigger rules implemented (the common player subset):
  - key range (lokey/hikey) and velocity range (lovel/hivel) gating
  - round-robin: seq_length / seq_position with a per-key counter
  - random split: lorand / hirand windows, one draw per trigger
  - pitch: sample root = pitch_keycenter; playback transposes by
    (triggered key - root) semitones via resampling
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from pathlib import Path

from . import audio
from .coverage import sample_path
from .sfzparse import Region, midi_to_name, parse_sfz


@dataclass
class TriggerEvent:
    seq: int
    key: int
    vel: int
    sample: str
    file_exists: bool
    transpose: int          # semitones applied at playback
    rr_position: int        # seq_position selected (0 when no RR)
    rr_length: int
    rand_draw: float | None
    measured_f0: float | None = None
    target_f0: float | None = None
    f0_cents_err: float | None = None
    note: str = ""

    def as_dict(self):
        d = dict(self.__dict__)
        return d


class Engine:
    def __init__(self, sfz_path: str | Path, seed: int = 42):
        self.sfz_path = Path(sfz_path)
        self.regions = parse_sfz(self.sfz_path)
        self.dir = self.sfz_path.parent
        self.rr_counters: dict[int, int] = {}
        self.rand = random.Random(seed)
        self.trigger_count = 0
        self._cache: dict[str, tuple[list[float], int]] = {}

    def _load(self, region: Region) -> tuple[list[float], int] | None:
        p = sample_path(self.dir, region)
        key = str(p)
        if key not in self._cache:
            if not p.exists():
                return None
            self._cache[key] = audio.read_wav(p)
        return self._cache[key]

    def select(self, key: int, vel: int) -> tuple[list[Region], float | None, int]:
        """Return (regions to play, rand draw, rr position used)."""
        matched = [r for r in self.regions if r.covers(key, vel)]
        if not matched:
            return [], None, 0
        seq_len = max(r.seq_length for r in matched)
        rr_pos = 0
        if seq_len > 1:
            n = self.rr_counters.get(key, 0)
            rr_pos = n % seq_len + 1
            self.rr_counters[key] = n + 1
            matched = [r for r in matched
                       if r.seq_length == 1 or r.seq_position == rr_pos]
        draw = None
        if any(r.lorand > 0.0 or r.hirand < 1.0 for r in matched):
            draw = self.rand.random()
            matched = [r for r in matched if r.lorand <= draw < r.hirand]
        return matched, draw, rr_pos

    def trigger(self, key: int, vel: int,
                measure: bool = True) -> tuple[list[TriggerEvent], list[float]]:
        """Fire one note; returns (events, rendered audio of this trigger)."""
        self.trigger_count += 1
        picked, draw, rr_pos = self.select(key, vel)
        events: list[TriggerEvent] = []
        tracks: list[list[float]] = []
        for r in picked:
            p = sample_path(self.dir, r)
            transpose = key - r.root
            ev = TriggerEvent(
                seq=self.trigger_count, key=key, vel=vel,
                sample=r.sample, file_exists=p.exists(),
                transpose=transpose,
                rr_position=r.seq_position if r.seq_length > 1 else 0,
                rr_length=r.seq_length, rand_draw=draw,
                note=midi_to_name(key),
                target_f0=audio.midi_to_freq(key),
            )
            loaded = self._load(r)
            if loaded is not None:
                samples, sr = loaded
                shifted = audio.resample_semitones(samples, transpose)
                tracks.append(shifted)
                if measure:
                    f0 = audio.measure_f0(shifted, sr)
                    ev.measured_f0 = f0
                    if f0:
                        import math
                        ev.f0_cents_err = 1200.0 * math.log2(
                            f0 / ev.target_f0)
            events.append(ev)
        if not picked:
            events.append(TriggerEvent(
                seq=self.trigger_count, key=key, vel=vel, sample="",
                file_exists=False, transpose=0, rr_position=rr_pos,
                rr_length=0, rand_draw=draw, note=midi_to_name(key),
                target_f0=audio.midi_to_freq(key)))
        return events, audio.mix(tracks)
