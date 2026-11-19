"""Small, dependency-free CSV and SVG writers for audio evidence."""

from __future__ import annotations

import csv
from pathlib import Path


def write_seam_csv(path: str | Path, named_channels: dict[str, list[list[float]]], edge: int) -> None:
    with Path(path).open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["signal", "frame", "position", "ch0", "ch1", "ch0_delta", "ch1_delta"])
        for name, channels in named_channels.items():
            first = max(0, edge - 4)
            last = min(len(channels[0]), edge + 4)
            for frame in range(first, last):
                position = "before" if frame < edge else "after"
                delta0 = channels[0][frame] - channels[0][frame - 1] if frame else 0.0
                delta1 = channels[1][frame] - channels[1][frame - 1] if frame else 0.0
                writer.writerow(
                    [
                        name,
                        frame,
                        position,
                        f"{channels[0][frame]:.17g}",
                        f"{channels[1][frame]:.17g}",
                        f"{delta0:.17g}",
                        f"{delta1:.17g}",
                    ]
                )


def _panel(
    channels: list[list[float]],
    boundary: int,
    radius: int,
    top: float,
    height: float,
    width: float,
) -> list[str]:
    first = max(0, boundary - radius)
    last = min(len(channels[0]), boundary + radius)
    span = max(1, last - first)
    elements = [
        f'<line x1="0" y1="{top + height / 2:.1f}" x2="{width}" y2="{top + height / 2:.1f}" '
        'stroke="#cccccc" stroke-width="0.5"/>',
        f'<line x1="{width * radius / span:.1f}" y1="{top:.1f}" '
        f'x2="{width * radius / span:.1f}" y2="{top + height:.1f}" '
        'stroke="#888888" stroke-dasharray="4,3"/>',
    ]
    colors = ("#1f77b4", "#d62728")
    for channel_index, values in enumerate(channels):
        points = []
        for offset, frame in enumerate(range(first, last)):
            x = offset / span * width
            y = top + height / 2 - values[frame] * height * 0.45
            points.append(f"{x:.2f},{y:.2f}")
        elements.append(
            f'<polyline points="{" ".join(points)}" fill="none" '
            f'stroke="{colors[channel_index % 2]}" stroke-width="1.5"/>'
        )
    return elements


def write_comparison_svg(
    path: str | Path,
    named_channels: dict[str, list[list[float]]],
    boundary: int,
    title: str,
    radius: int = 200,
) -> None:
    panel_height = 210
    width = 900
    height = 56 + panel_height * len(named_channels)
    elements = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        'font-family="monospace" font-size="12">',
        f'<rect width="{width}" height="{height}" fill="white"/>',
        f'<text x="10" y="16">{title}</text>',
        f'<text x="10" y="36">boundary frame {boundary} (ch0 blue, ch1 red)</text>',
    ]
    for index, (name, channels) in enumerate(named_channels.items()):
        top = 56 + index * panel_height
        elements.append(f'<text x="10" y="{top + 12}">{name}</text>')
        elements.extend(_panel(channels, boundary, radius, top + 18, panel_height - 24, width))
    elements.append("</svg>")
    Path(path).write_text("\n".join(elements) + "\n", encoding="utf-8")
