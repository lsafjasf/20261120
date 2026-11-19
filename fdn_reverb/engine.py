"""Sample-accurate feedback-delay-network reverb."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .matrices import build_feedback_matrix, geodesic_path


DEFAULT_DELAY_SECONDS = (
    0.0193,
    0.0239,
    0.0291,
    0.0317,
    0.0379,
    0.0431,
    0.0479,
    0.0533,
)


@dataclass(frozen=True)
class RoomPreset:
    """A room is a feedback topology plus an RT60 target."""

    name: str
    rt60: float
    matrix_kind: str = "householder"
    seed: int = 7

    def __post_init__(self) -> None:
        if self.rt60 <= 0.0:
            raise ValueError("room RT60 must be positive")


@dataclass(frozen=True)
class FDNConfig:
    sample_rate: int = 48_000
    delay_seconds: tuple[float, ...] = DEFAULT_DELAY_SECONDS
    rt60: float = 1.0
    matrix_kind: str = "householder"
    matrix_seed: int = 7
    direct_gain: float = 0.0
    transition_seconds: float = 0.05

    def __post_init__(self) -> None:
        if self.sample_rate <= 0:
            raise ValueError("sample_rate must be positive")
        if self.rt60 <= 0.0:
            raise ValueError("rt60 must be positive")
        if self.transition_seconds < 0.0:
            raise ValueError("transition_seconds cannot be negative")
        if not self.delay_seconds or any(delay <= 0.0 for delay in self.delay_seconds):
            raise ValueError("delay_seconds must contain positive values")


class FDNReverb:
    """An FDN whose per-delay feedback gains track the requested RT60.

    The effective feedback operator is

        A(rt60) = diag(10 ** (-3 * delay_i / (rt60 * fs))) @ M

    where ``M`` is an energy-normalized mixing matrix. Replacing RT60 therefore
    updates every row of the effective feedback matrix. Room changes follow a
    sampled matrix geodesic and a smooth RT60 control path, so both the state and
    the output remain continuous.
    """

    _CONTROL_POINTS = 257

    def __init__(
        self,
        config: FDNConfig | None = None,
        *,
        matrix: np.ndarray | None = None,
    ) -> None:
        self.config = config or FDNConfig()
        self.sample_rate = self.config.sample_rate
        self.delay_samples = np.maximum(
            1,
            np.rint(np.asarray(self.config.delay_seconds) * self.sample_rate).astype(int),
        )
        self.n_delays = len(self.delay_samples)
        self.buffer_length = int(np.max(self.delay_samples)) + 2
        self._buffer = np.zeros((self.n_delays, self.buffer_length), dtype=np.float64)
        self._write_index = 0

        self.matrix = (
            build_feedback_matrix(
                self.config.matrix_kind,
                self.n_delays,
                seed=self.config.matrix_seed,
            )
            if matrix is None
            else np.asarray(matrix, dtype=np.float64).copy()
        )
        if self.matrix.shape != (self.n_delays, self.n_delays):
            raise ValueError("feedback matrix shape does not match delay count")

        coupling_rng = np.random.default_rng(self.config.matrix_seed + 101)
        self.input_coupling = np.full(self.n_delays, 1.0 / np.sqrt(self.n_delays))
        self.output_coupling = coupling_rng.standard_normal(self.n_delays)
        self.output_coupling /= np.linalg.norm(self.output_coupling)

        self.rt60 = float(self.config.rt60)
        self.gains = self._gains_for_rt60(self.rt60)
        self._transition_age = 0
        self._transition_length = 0
        self._matrix_path: np.ndarray | None = None
        self._gain_path: np.ndarray | None = None
        self._rt60_path: np.ndarray | None = None
        self.transition_active = False

    def _gains_for_rt60(self, rt60: float) -> np.ndarray:
        return np.power(
            10.0,
            -3.0 * self.delay_samples / (float(rt60) * self.sample_rate),
        )

    def _read_state(self) -> np.ndarray:
        read_indices = (self._write_index - self.delay_samples) % self.buffer_length
        return self._buffer[np.arange(self.n_delays), read_indices]

    def _write_state(self, state: np.ndarray) -> None:
        self._buffer[:, self._write_index] = state
        self._write_index = (self._write_index + 1) % self.buffer_length

    @staticmethod
    def _smoothstep(value: float) -> float:
        value = float(np.clip(value, 0.0, 1.0))
        return value * value * (3.0 - 2.0 * value)

    def _control_position(self) -> float:
        if not self.transition_active or self._transition_length == 0:
            return 1.0
        position = self._transition_age / self._transition_length
        return self._smoothstep(position)

    def _apply_control_at(self, position: float) -> None:
        assert self._matrix_path is not None
        assert self._gain_path is not None
        assert self._rt60_path is not None
        scaled = position * (self._CONTROL_POINTS - 1)
        left = int(np.floor(scaled))
        right = min(left + 1, self._CONTROL_POINTS - 1)
        fraction = scaled - left
        self.matrix = (
            (1.0 - fraction) * self._matrix_path[left]
            + fraction * self._matrix_path[right]
        )
        self.gains = (
            (1.0 - fraction) * self._gain_path[left]
            + fraction * self._gain_path[right]
        )
        self.rt60 = float(
            (1.0 - fraction) * self._rt60_path[left]
            + fraction * self._rt60_path[right]
        )

    def _advance_control(self) -> None:
        if not self.transition_active:
            return
        position = self._control_position()
        self._apply_control_at(position)
        self._transition_age += 1
        if self._transition_age > self._transition_length:
            self.transition_active = False
            self._apply_control_at(1.0)
            self._matrix_path = None
            self._gain_path = None
            self._rt60_path = None

    def transition_to(
        self,
        matrix: np.ndarray,
        rt60: float,
        seconds: float | None = None,
    ) -> None:
        """Smoothly move to a new feedback matrix and RT60 target."""
        if rt60 <= 0.0:
            raise ValueError("rt60 must be positive")
        target_matrix = np.asarray(matrix, dtype=np.float64)
        if target_matrix.shape != self.matrix.shape:
            raise ValueError("target matrix shape does not match current matrix")

        duration = self.config.transition_seconds if seconds is None else seconds
        if duration <= 0.0:
            self.matrix = target_matrix.copy()
            self.rt60 = float(rt60)
            self.gains = self._gains_for_rt60(self.rt60)
            self.transition_active = False
            return

        start_matrix = self.matrix.copy()
        start_rt60 = self.rt60
        self._matrix_path = geodesic_path(
            start_matrix,
            target_matrix,
            steps=self._CONTROL_POINTS,
        )
        control_positions = np.linspace(0.0, 1.0, self._CONTROL_POINTS)
        rt60_path = np.exp(
            np.log(start_rt60) * (1.0 - control_positions)
            + np.log(rt60) * control_positions
        )
        self._rt60_path = rt60_path
        self._gain_path = np.stack(
            [self._gains_for_rt60(value) for value in rt60_path],
            axis=0,
        )
        self._transition_age = 0
        self._transition_length = max(
            1,
            int(round(duration * self.sample_rate)),
        )
        self.transition_active = True
        self._apply_control_at(0.0)

    def transition_to_room(
        self,
        room: RoomPreset,
        seconds: float | None = None,
    ) -> None:
        matrix = build_feedback_matrix(
            room.matrix_kind,
            self.n_delays,
            seed=room.seed,
        )
        self.transition_to(matrix, room.rt60, seconds=seconds)

    def transition_to_rt60(
        self,
        rt60: float,
        seconds: float | None = None,
    ) -> None:
        """Dynamically retune the effective feedback matrix for a new RT60."""
        self.transition_to(self.matrix, rt60, seconds=seconds)

    def process_sample(self, sample: float) -> float:
        self._advance_control()
        state = self._read_state()
        output = float(self.output_coupling @ state + self.config.direct_gain * sample)
        feedback = self.gains * (self.matrix @ state)
        self._write_state(feedback + self.input_coupling * float(sample))
        return output

    def process_block(self, samples: np.ndarray) -> np.ndarray:
        samples = np.asarray(samples, dtype=np.float64).reshape(-1)
        output = np.empty_like(samples)
        for index, sample in enumerate(samples):
            output[index] = self.process_sample(float(sample))
        return output

    def reset(self) -> None:
        self._buffer.fill(0.0)
        self._write_index = 0

    @property
    def effective_feedback_matrix(self) -> np.ndarray:
        return self.gains[:, None] * self.matrix
