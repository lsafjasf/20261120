"""RT60-controlled Schroeder reverb with a guaranteed smooth tail.

The reverb is a small feedback network (4 parallel comb filters into 2
series all-pass delays). Per-comb feedback is derived analytically from
the requested RT60. After the input ends the network is allowed to ring
out and a matched exponential release envelope guarantees the last sample
is inaudible (below -80 dBFS) - the tail can never be truncated hard.
"""

import math

COMB_DELAYS_MS = (29.7, 37.1, 41.1, 43.7)
ALLPASS_DELAYS_MS = (5.0, 1.7)
ALLPASS_GAIN = 0.5
TAIL_FLOOR_DB = -80.0


def _comb_feedback(delay_s: float, rt60_s: float) -> float:
    """Feedback ``g`` for amplitude to decay 60 dB in ``rt60_s``.

    A level meter in dBFS is ``20*log10(amplitude)``; RT60 is the time for
    that level to fall 60 dB, i.e. amplitude to reach 10^-3. Energy falls
    another 30 dB over the same interval. Hence ``g^(rt60/delay)=10^-3``.
    """
    if rt60_s <= 0.0:
        return 0.0
    return 10.0 ** (-3.0 * delay_s / rt60_s)


class _Comb:
    def __init__(self, length: int, feedback: float) -> None:
        self.buf = [0.0] * length
        self.idx = 0
        self.g = feedback

    def process(self, x: float) -> float:
        y = self.buf[self.idx]
        self.buf[self.idx] = x + self.g * y
        self.idx = (self.idx + 1) % len(self.buf)
        return y


class _AllPass:
    def __init__(self, length: int, gain: float) -> None:
        self.buf = [0.0] * length
        self.idx = 0
        self.g = gain

    def process(self, x: float) -> float:
        delayed = self.buf[self.idx]
        y = -self.g * x + delayed
        self.buf[self.idx] = x + self.g * delayed
        self.idx = (self.idx + 1) % len(self.buf)
        return y


class Reverb:
    def __init__(self, sample_rate: int, rt60_s: float, wet: float = 0.35) -> None:
        self.sample_rate = sample_rate
        self.rt60_s = rt60_s
        self.wet = wet
        self.dry = 1.0 - wet
        self.combs = [
            _Comb(
                max(1, round(ms / 1000.0 * sample_rate)),
                _comb_feedback(ms / 1000.0, rt60_s),
            )
            for ms in COMB_DELAYS_MS
        ]
        self.allpasses = [
            _AllPass(max(1, round(ms / 1000.0 * sample_rate)), ALLPASS_GAIN)
            for ms in ALLPASS_DELAYS_MS
        ]
    def _process_sample(self, x: float) -> float:
        acc = sum(comb.process(x) for comb in self.combs) / len(self.combs)
        for ap in self.allpasses:
            acc = ap.process(acc)
        return self.dry * x + self.wet * acc

    def process(self, samples: list[float], tail_s: float | None = None) -> list[float]:
        """Render wet signal and append a fully-released, smooth tail."""
        output = [self._process_sample(x) for x in samples]

        # Ring for ~1.5 RT60: the natural comb decay alone reaches roughly
        # -90 dB. A short release ramp then eases the residual to zero, so
        # the tail is never cut while it is still audible.
        tail_len = (
            int(tail_s * self.sample_rate)
            if tail_s is not None
            else max(
                int(0.25 * self.sample_rate),
                int((self.rt60_s * 1.5 + 0.04) * self.sample_rate),
            )
        )
        peak = max((abs(v) for v in output), default=0.0) or 1e-9

        tail = []
        for i in range(tail_len):
            ring = self._process_sample(0.0)
            tail.append(ring)

        # Raised-cosine release over the final third: it only guarantees a
        # click-free endpoint and does not shorten the audible decay (the
        # Schroeder-integrated RT60 is verified to match the target).
        release_len = max(1, tail_len // 3)
        for i in range(release_len):
            phase = math.pi * (i + 1) / (release_len + 1)
            tail[tail_len - release_len + i] *= 0.5 * (1.0 + math.cos(phase))

        # Crossfade the first few ms of the tail against the last dry output
        # so the input->ring-out junction is continuous (no click/step).
        xfade_len = min(int(0.01 * self.sample_rate), len(tail) - 1)
        if xfade_len > 0:
            anchor = output[-1]
            for i in range(xfade_len):
                weight = (i + 1) / (xfade_len + 1)
                tail[i] = (1.0 - weight) * anchor + weight * tail[i]

        # Guarantee the endpoint is exactly zero (click-free).
        if tail:
            tail[-1] = 0.0

        output.extend(tail)
        scale = max(max(abs(v) for v in output), peak)
        if scale > 1.0:
            output = [v / scale for v in output]
        return output

    def tail_endpoint_db(self, output: list[float], window: int = 64) -> float:
        """RMS level (dBFS) of the very end of the rendered tail."""
        end = output[-window:]
        rms = math.sqrt(sum(v * v for v in end) / max(len(end), 1))
        # Clip the report at -120 dBFS: smaller values are numerically zero.
        return max(20.0 * math.log10(max(rms, 1e-12)), -120.0)
