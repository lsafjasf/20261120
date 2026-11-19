"""Render A/B stereo WAV files for direct vs. smoothed head tracking.

The same fast head-turn event stream drives two engines. A static 440 Hz
tone is spatialised per render tick (equal-power pan + Doppler pitch), so
the two files can be compared on headphones:

* ``ab_direct.wav``  - raw mapping: audible jitter at rest, pitch zipper
  during the fast turn.
* ``ab_smoothed.wav`` - One-Euro + slew-limited Doppler: stable image,
  continuous pitch, no added pipeline delay.
"""

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.spatial_audio.engine import SpatialEngine
from src.spatial_audio.filters import DirectFilter, OneEuroFilter
from src.spatial_audio.orientation import (
    OrientationSimulator,
    fast_turn_profile,
)
from src.spatial_audio.wavio import write_wav

ART = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "artifacts")
SAMPLE_RATE = 48000
RENDER_HZ = 120.0
DURATION = 3.2
TONE_HZ = 440.0


def render(filter_obj, slew: bool, yaw_fn):
    engine = SpatialEngine(
        filter_obj, source_yaw=0.0, source_distance=1.0,
        render_hz=RENDER_HZ, doppler_slew_rate=1.5,
        doppler_slew_enabled=slew,
    )
    events = list(OrientationSimulator(yaw_fn).stream(DURATION))
    left, right = [], []
    phase = 0.0
    samples_per_tick = SAMPLE_RATE / RENDER_HZ
    ev_idx = 0

    for tick in range(int(DURATION * RENDER_HZ) + 1):
        now = tick / RENDER_HZ
        while ev_idx < len(events) and events[ev_idx].t <= now:
            engine.push_event(events[ev_idx])
            ev_idx += 1
        state = engine.render(now)
        for _ in range(int(samples_per_tick)):
            phase += 2.0 * math.pi * TONE_HZ * state.doppler_applied / SAMPLE_RATE
            sample = 0.45 * math.sin(phase)
            left.append(sample * state.gain_l)
            right.append(sample * state.gain_r)
    return left, right


def main():
    os.makedirs(ART, exist_ok=True)
    yaw_fn, _, _ = fast_turn_profile()

    dl, dr = render(DirectFilter(), False, yaw_fn)
    write_wav(os.path.join(ART, "ab_direct.wav"), [dl, dr], SAMPLE_RATE)

    sl, sr = render(OneEuroFilter(), True, yaw_fn)
    write_wav(os.path.join(ART, "ab_smoothed.wav"), [sl, sr], SAMPLE_RATE)

    for name, ch in (("ab_direct.wav", dl), ("ab_smoothed.wav", sl)):
        print(name, len(ch) / SAMPLE_RATE, "s,",
              len(ch), "stereo samples/channel")


if __name__ == "__main__":
    main()
