"""Validate the Python synthesizer against dav1d's grain application.

Synthesizes grain onto the grain-off decode and compares to the grain-on
decode produced by dav1d. Expects bit-exact match for 8-bit 4:2:0.
"""
import sys
import time

import numpy as np

sys.path.insert(0, "scripts")
from common import load_grain_params, read_yuv
from film_grain import apply_grain_frame

tier = sys.argv[1] if len(sys.argv) > 1 else "20"
params = load_grain_params(f"results/params_l{tier}.json")
off = read_yuv(f"dec/l{tier}_off.yuv")
on = read_yuv(f"dec/l{tier}_on.yuv")
assert len(params) == len(off) == len(on)

mismatch = 0
t0 = time.perf_counter()
for i, (p, fo, fn) in enumerate(zip(params, off, on)):
    assert p is not None, f"frame {i} lacks film_grain_params"
    syn = apply_grain_frame(fo, p)
    for pl, name in enumerate("YUV"):
        diff = np.asarray(syn[pl], np.int16) - np.asarray(fn[pl], np.int16)
        n_bad = int(np.count_nonzero(diff))
        if n_bad:
            mismatch += n_bad
            print(f"frame {i} plane {name}: {n_bad} mismatching samples, "
                  f"max|d|={np.abs(diff).max()}")
dt = time.perf_counter() - t0
print(f"tier l{tier}: {len(off)} frames, mismatched samples = {mismatch} "
      f"({'BIT-EXACT' if mismatch == 0 else 'DIFFERS'}), "
      f"synth time {dt:.2f}s ({dt / len(off) * 1000:.1f} ms/frame)")
