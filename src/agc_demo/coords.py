"""Coordinate conversion for scaled localization labels and hit-area checks.

Models a map/canvas interaction: a logical viewport is rendered at some zoom
scale with pan offset. Label anchor points and rectangular hit areas defined
in logical coordinates must map to screen coordinates, and a screen click
must map back to logical coordinates. Round-trip consistency proves the
click hit region has no offset.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Viewport:
    scale: float
    pan_x: float
    pan_y: float

    def logical_to_screen(self, x: float, y: float) -> tuple[float, float]:
        return x * self.scale + self.pan_x, y * self.scale + self.pan_y

    def screen_to_logical(self, sx: float, sy: float) -> tuple[float, float]:
        return (sx - self.pan_x) / self.scale, (sy - self.pan_y) / self.scale


@dataclass(frozen=True)
class Label:
    name: str
    logical_anchor: tuple[float, float]
    logical_hit_box: tuple[float, float, float, float]  # x0, y0, x1, y1


@dataclass(frozen=True)
class LabelMapping:
    name: str
    screen_anchor: tuple[float, float]
    screen_hit_box: tuple[float, float, float, float]


def map_label(label: Label, viewport: Viewport) -> LabelMapping:
    ax, ay = viewport.logical_to_screen(*label.logical_anchor)
    x0, y0 = viewport.logical_to_screen(label.logical_hit_box[0],
                                        label.logical_hit_box[1])
    x1, y1 = viewport.logical_to_screen(label.logical_hit_box[2],
                                        label.logical_hit_box[3])
    return LabelMapping(label.name, (ax, ay), (x0, y0, x1, y1))


def click_hits(screen_click: tuple[float, float], mapping: LabelMapping,
               tolerance_px: float = 0.0) -> bool:
    x, y = screen_click
    x0, y0, x1, y1 = mapping.screen_hit_box
    return (x0 - tolerance_px <= x <= x1 + tolerance_px
            and y0 - tolerance_px <= y <= y1 + tolerance_px)


def round_trip_error(label: Label, viewport: Viewport,
                     screen_click: tuple[float, float]) -> float:
    """Distance between the logical click and the anchor after both transforms."""
    lx, ly = viewport.screen_to_logical(*screen_click)
    anchor_x, anchor_y = label.logical_anchor
    return ((lx - anchor_x) ** 2 + (ly - anchor_y) ** 2) ** 0.5
