from __future__ import annotations

import csv
import subprocess
import tempfile
import time
from pathlib import Path

import numpy as np
from scipy.special import exp1

from audio_utils import FRAME_SIZE, SAMPLE_RATE

ROOT = Path(__file__).resolve().parents[1]
WEBRTC_BINARY = ROOT / "bin" / "webrtc_denoise"
WEBRTC_DELAY = 334


class StreamingSTFT:
    def __init__(self, n_fft: int = 1024, win_length: int = 960, hop: int = 480):
        self.n_fft = n_fft
        self.win_length = win_length
        self.hop = hop
        self.window = np.sqrt(np.hanning(win_length))
        self.previous_input = np.zeros(hop, dtype=np.float64)
        self.previous_tail = np.zeros(hop, dtype=np.float64)

    def transform(self, frame: np.ndarray) -> np.ndarray:
        joined = np.concatenate([self.previous_input, frame])
        self.previous_input = frame.copy()
        return np.fft.rfft(joined * self.window, n=self.n_fft)

    def inverse(self, spectrum: np.ndarray) -> np.ndarray:
        reconstructed = np.fft.irfft(spectrum, n=self.n_fft)[: self.win_length]
        reconstructed *= self.window
        output = reconstructed[: self.hop] + self.previous_tail
        self.previous_tail = reconstructed[self.hop :].copy()
        return output


class STFTDenoiser:
    delay = 480
    name = "base"

    def __init__(
        self,
        bootstrap_frames: int = 30,
        hf_floor: float = 0.60,
        hf_start_hz: float = 8_000.0,
    ):
        self.stft = StreamingSTFT()
        self.bootstrap_frames = bootstrap_frames
        self.bootstrap_sum = np.zeros(513, dtype=np.float64)
        self.frames_seen = 0
        self.noise_psd: np.ndarray | None = None
        self.previous_clean_power = np.zeros(513, dtype=np.float64)
        self.frequencies = np.fft.rfftfreq(self.stft.n_fft, 1.0 / SAMPLE_RATE)
        self.hf_bins = self.frequencies >= hf_start_hz
        self.hf_floor = hf_floor

    def _update_noise(self, power: np.ndarray) -> None:
        if self.noise_psd is None:
            return
        faster = 0.70 * self.noise_psd + 0.30 * power
        slower = 0.998 * self.noise_psd + 0.002 * power
        self.noise_psd = np.where(power < self.noise_psd, faster, slower)

    def _finish_bootstrap(self, power: np.ndarray) -> bool:
        if self.noise_psd is not None:
            return False
        self.bootstrap_sum += power
        self.frames_seen += 1
        if self.frames_seen >= self.bootstrap_frames:
            self.noise_psd = self.bootstrap_sum / self.frames_seen
        return True

    def process_frame(self, frame: np.ndarray) -> np.ndarray:
        spectrum = self.stft.transform(frame)
        power = np.abs(spectrum) ** 2
        if self._finish_bootstrap(power):
            return self.stft.inverse(spectrum)
        assert self.noise_psd is not None
        gain = self.gain(power)
        gain[self.hf_bins] = np.maximum(gain[self.hf_bins], self.hf_floor)
        self._update_noise(power)
        return self.stft.inverse(spectrum * gain)

    def gain(self, power: np.ndarray) -> np.ndarray:
        raise NotImplementedError


class SpectralSubtraction(STFTDenoiser):
    name = "spectral_subtraction"

    def __init__(self, alpha: float = 1.00, floor: float = 0.15):
        super().__init__()
        self.alpha = alpha
        self.floor = floor

    def gain(self, power: np.ndarray) -> np.ndarray:
        assert self.noise_psd is not None
        subtraction = self.alpha * np.sqrt(self.noise_psd)
        magnitude = np.sqrt(np.maximum(power, 1e-20))
        clean_magnitude = np.maximum(magnitude - subtraction, self.floor * magnitude)
        return np.minimum(clean_magnitude / magnitude, 1.0)


class MMSE(STFTDenoiser):
    name = "mmse"

    def __init__(self, prior_floor: float = 0.08, gain_floor: float = 0.06):
        super().__init__()
        self.prior_floor = prior_floor
        self.gain_floor = gain_floor

    def gain(self, power: np.ndarray) -> np.ndarray:
        assert self.noise_psd is not None
        posterior = power / np.maximum(self.noise_psd, 1e-20)
        decision_directed = (
            0.94 * self.previous_clean_power / np.maximum(self.noise_psd, 1e-20)
            + 0.06 * np.maximum(posterior - 1.0, 0.0)
        )
        prior = np.maximum(decision_directed, self.prior_floor)
        nu = prior / (1.0 + prior) * posterior
        gain = prior / (1.0 + prior) * np.exp(0.5 * exp1(np.maximum(nu, 1e-8)))
        gain = np.clip(gain, self.gain_floor, 1.0)
        self.previous_clean_power = gain**2 * power
        return gain


def frame_signal(signal: np.ndarray) -> tuple[list[np.ndarray], int]:
    padded_length = int(np.ceil(len(signal) / FRAME_SIZE) * FRAME_SIZE)
    padded = np.zeros(padded_length, dtype=np.float64)
    padded[: len(signal)] = signal
    return [padded[i : i + FRAME_SIZE] for i in range(0, padded_length, FRAME_SIZE)], len(signal)


def process_python(denoiser: STFTDenoiser, signal: np.ndarray) -> tuple[np.ndarray, list[float]]:
    frames, original_length = frame_signal(signal)
    output = np.empty(len(frames) * FRAME_SIZE, dtype=np.float64)
    elapsed_ns: list[float] = []
    for index, frame in enumerate(frames):
        start = time.perf_counter_ns()
        processed = denoiser.process_frame(frame)
        elapsed_ns.append(float(time.perf_counter_ns() - start))
        output[index * FRAME_SIZE : (index + 1) * FRAME_SIZE] = processed
    return output[:original_length], elapsed_ns


def process_webrtc(
    signal: np.ndarray,
    level: str = "low",
) -> tuple[np.ndarray, list[float]]:
    if not WEBRTC_BINARY.exists():
        raise FileNotFoundError(f"WebRTC bridge not built: {WEBRTC_BINARY}")
    frames, original_length = frame_signal(signal)
    with tempfile.TemporaryDirectory(prefix="webrtc-ns-") as directory:
        directory = Path(directory)
        input_path = directory / "input.f32"
        output_path = directory / "output.f32"
        timing_path = directory / "timing.csv"
        np.concatenate(frames).astype("<f4").tofile(input_path)
        subprocess.run(
            [
                str(WEBRTC_BINARY),
                str(input_path),
                str(output_path),
                str(timing_path),
                level,
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        output = np.fromfile(output_path, dtype="<f4").astype(np.float64)
        elapsed_ns: list[float] = []
        with timing_path.open(newline="") as handle:
            for row in csv.DictReader(handle):
                elapsed_ns.append(float(row["ns"]))
    return output[:original_length], elapsed_ns


def make_denoiser(name: str):
    if name == "spectral_subtraction":
        return SpectralSubtraction()
    if name == "mmse":
        return MMSE()
    raise ValueError(name)
