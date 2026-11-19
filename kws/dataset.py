"""Build the full evaluation corpus (clean, noisy, speed-shifted, negatives)."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from . import synth

SNR_GRID = (20.0, 10.0, 5.0, 0.0)
SPEED_GRID = (0.9, 1.1)


@dataclass
class Corpus:
    enroll: dict[int, list[np.ndarray]]
    dev_pos: dict[int, list[np.ndarray]]
    test_pos: dict[int, list[tuple[str, np.ndarray]]]
    dev_neg: list[np.ndarray]
    test_neg: list[tuple[str, np.ndarray]]
    far_events: list[np.ndarray]


def build_corpus(seed: int = 20261120) -> Corpus:
    rng = np.random.default_rng(seed)

    enroll, dev_pos = {0: [], 1: []}, {0: [], 1: []}
    test_pos: dict[int, list[tuple[str, np.ndarray]]] = {0: [], 1: []}

    for kind in (0, 1):
        for _ in range(30):
            enroll[kind].append(synth.generate_keyword(kind, rng))
        for _ in range(20):
            dev_pos[kind].append(synth.generate_keyword(kind, rng))
        for snr in (10.0, 5.0):
            for _ in range(10):
                base = synth.generate_keyword(kind, rng)
                dev_pos[kind].append(
                    synth.add_noise_at_snr(
                        base, synth.generate_noise_segment(rng), snr, rng)
                )

        for _ in range(60):
            base = synth.generate_keyword(kind, rng)
            test_pos[kind].append(("clean", base))
        for snr in SNR_GRID:
            for _ in range(30):
                base = synth.generate_keyword(kind, rng)
                noise = synth.generate_noise_segment(rng)
                test_pos[kind].append(
                    (f"snr{int(snr)}", synth.add_noise_at_snr(base, noise, snr, rng))
                )
        for rate in SPEED_GRID:
            for _ in range(30):
                base = synth.generate_keyword(kind, rng)
                shifted = synth.time_stretch(base, rate)
                test_pos[kind].append((f"speed{rate}", shifted))

    dev_neg = [synth.generate_oov(rng) for _ in range(60)]
    dev_neg += [synth.generate_noise_segment(rng) for _ in range(40)]

    test_neg: list[tuple[str, np.ndarray]] = []
    for _ in range(60):
        test_neg.append(("oov", synth.generate_oov(rng)))
    for _ in range(60):
        test_neg.append(("noise", synth.generate_noise_segment(rng)))

    # Long FAR-style negative stream: 60 two-second pseudo-events.
    far_events = []
    for _ in range(60):
        parts = [synth.generate_oov(rng) if rng.random() < 0.6
                 else synth.generate_noise_segment(rng)
                 for _ in range(2)]
        gap = np.zeros(int(rng.uniform(0.1, 0.3) * synth.FS))
        far_events.append(np.concatenate([parts[0], gap, parts[1]]))

    return Corpus(enroll, dev_pos, test_pos, dev_neg, test_neg, far_events)
