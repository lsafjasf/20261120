from __future__ import annotations

from collections import deque
from dataclasses import dataclass


@dataclass(frozen=True)
class BufferConfig:
    frame_samples: int = 480
    target_frames: int = 5
    low_watermark_frames: int = 2
    capacity_frames: int = 10
    initial_frames: int = 2
    initial_recovery_tick: int = 20
    outage_start_tick: int = 120
    outage_frames: int = 4
    simulation_ticks: int = 600
    tail_start_tick: int = 150


class WatermarkBuffer:
    def __init__(self, config: BufferConfig):
        self.config = config
        self.frames: deque[int] = deque()
        self.underruns = 0

    def push(self, count: int = 1) -> None:
        room = self.config.capacity_frames - len(self.frames)
        for _ in range(max(0, min(count, room))):
            self.frames.append(1)

    def pull(self) -> bool:
        if self.frames:
            self.frames.popleft()
            return True
        self.underruns += 1
        return False

    def level(self) -> int:
        return len(self.frames)


def simulate_watermark_recovery(
    config: BufferConfig | None = None,
) -> dict[str, float | int | list[int]]:
    config = config or BufferConfig()
    buffer = WatermarkBuffer(config)
    buffer.push(config.initial_frames)
    levels: list[int] = []
    outage_stop = config.outage_start_tick + config.outage_frames

    for tick in range(config.simulation_ticks):
        if tick == config.initial_recovery_tick:
            wanted = max(0, config.target_frames - buffer.level())
            buffer.push(wanted + 1)
        elif config.outage_start_tick <= tick < outage_stop:
            pass
        elif tick == outage_stop:
            catch_up = 1 + config.outage_frames
            buffer.push(catch_up)
        elif tick > outage_stop and tick % 30 == 0:
            pass
        elif tick > outage_stop and tick % 30 == 1:
            buffer.push(2)
        else:
            buffer.push(1)
        buffer.pull()
        levels.append(buffer.level())

    tail = levels[config.tail_start_tick :]
    mean_level = sum(tail) / len(tail)
    variance = sum((level - mean_level) ** 2 for level in tail) / len(tail)
    return {
        "frame_samples": config.frame_samples,
        "frame_ms": 10.0,
        "target_watermark_frames": config.target_frames,
        "target_watermark_samples": config.target_frames * config.frame_samples,
        "target_watermark_ms": config.target_frames * 10.0,
        "initial_level_frames": config.initial_frames,
        "initial_recovery_tick": config.initial_recovery_tick,
        "outage_start_tick": config.outage_start_tick,
        "outage_frames": config.outage_frames,
        "tail_start_tick": config.tail_start_tick,
        "tail_mean_frames": mean_level,
        "tail_min_frames": min(tail),
        "tail_max_frames": max(tail),
        "tail_std_frames": variance**0.5,
        "tail_mean_samples": mean_level * config.frame_samples,
        "underruns": buffer.underruns,
        "levels": levels,
    }
