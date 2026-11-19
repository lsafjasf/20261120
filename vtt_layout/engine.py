"""Cue geometry, overlap stack order and region scrolling simulation."""

from __future__ import annotations

from dataclasses import dataclass, field

from .metrics import Break, TextMetrics, wrap_cue
from .parser import Cue, CueLine, Region, Span, WebVttDocument


LINE_HEIGHT_VH = 5.33          # CSS WebVTT default line step
BASE_FONT_VH = 5.0             # default cue font size relative to viewport
SAFE_MARGIN_PX = 8.0


@dataclass
class Box:
    x: float
    y: float
    width: float
    height: float

    @property
    def right(self) -> float:
        return self.x + self.width

    @property
    def bottom(self) -> float:
        return self.y + self.height

    def intersects(self, other: "Box") -> bool:
        return (self.x < other.right and other.x < self.right
                and self.y < other.bottom and other.y < self.bottom)


@dataclass
class CueLayout:
    cue: Cue
    box: Box
    rendered_lines: list[Break]
    line_slot: int
    layer: int                     # stack order, larger = painted on top
    region_id: str | None
    scroll_direction: str | None   # "up" for rolling region cues


@dataclass
class RegionLayout:
    region: Region
    box: Box
    inside_viewport: bool
    clip_top_px: float = 0.0


@dataclass
class FrameSnapshot:
    time: float
    cue_layouts: list[CueLayout]
    region_layouts: list[RegionLayout]
    overlaps: list[tuple[str, str]] = field(default_factory=list)
    region_clip_events: list[str] = field(default_factory=list)


def _pct(value: str | None, default: float) -> float:
    if value is None:
        return default
    return float(value.rstrip("%"))


class LayoutEngine:
    def __init__(self, doc: WebVttDocument, viewport_width: int,
                 viewport_height: int, base_font_px: float | None = None):
        self.doc = doc
        self.vw = float(viewport_width)
        self.vh = float(viewport_height)
        self.font_px = base_font_px or self.vh * BASE_FONT_VH / 100.0
        self.line_height_px = self.vh * LINE_HEIGHT_VH / 100.0
        self.metrics = TextMetrics(self.font_px)

    def cue_classes(self, cue: Cue) -> set[str]:
        classes: set[str] = set()
        cue_line: CueLine
        for cue_line in cue.lines:
            span: Span
            for span in cue_line.spans:
                classes.update(span.classes)
        return classes

    def cue_font_px(self, cue: Cue) -> tuple[float, float]:
        """Resolve effective (font size, line height) from matching ::cue rules."""
        font_size = self.font_px
        line_height = self.line_height_px
        classes = self.cue_classes(cue)
        for rule in self.doc.styles:
            selector = rule.selector.replace(" ", "")
            if selector == "::cue":
                if "font-size" in rule.declarations:
                    font_size = self._length(rule.declarations["font-size"],
                                              font_size)
                if "line-height" in rule.declarations:
                    line_height = self._length(rule.declarations["line-height"],
                                                line_height)
            elif selector.startswith("::cue(") and classes:
                named = selector[len("::cue("):-1].strip(".\"'")
                if named in classes:
                    if "font-size" in rule.declarations:
                        font_size = self._length(
                            rule.declarations["font-size"], font_size)
                    if "line-height" in rule.declarations:
                        line_height = self._length(
                            rule.declarations["line-height"], line_height)
        return font_size, line_height

    def _length(self, raw: str, fallback: float) -> float:
        raw = raw.strip()
        if raw.endswith("px"):
            return float(raw[:-2])
        if raw.endswith("vh"):
            return self.vh * float(raw[:-2]) / 100.0
        return fallback

    def region_box(self, region: Region) -> RegionLayout:
        width = self.vw * region.width_pct / 100.0
        height = region.lines * self.line_height_px
        anchor_x = width * region.anchor_x / 100.0
        anchor_y = height * region.anchor_y / 100.0
        x = self.vw * region.viewport_x / 100.0 - anchor_x
        y = self.vh * region.viewport_y / 100.0 - anchor_y
        x = max(0.0, min(x, self.vw - width))
        y = max(0.0, min(y, self.vh - height))
        box = Box(x, y, width, height)
        inside = (box.x >= 0 and box.y >= 0
                  and box.right <= self.vw and box.bottom <= self.vh)
        return RegionLayout(region, box, inside)

    def _resolve_size(self, cue: Cue, available_width: float) -> float:
        if "size" in cue.settings:
            raw = cue.settings["size"]
            if raw.endswith("px"):
                return float(raw[:-2])
            return self.vw * float(raw.rstrip("%")) / 100.0
        return available_width

    def _horizontal_origin(self, cue: Cue, box_width: float) -> tuple[float, str]:
        align = cue.settings.get("align", "middle")
        position = _pct(cue.settings.get("position"), None)
        if position is None:
            position = {"start": 0.0, "middle": 50.0, "end": 100.0}[align]
            position_line = cue.settings.get("line", "").endswith(",center")
            if position_line:
                position = 50.0
        center = self.vw * position / 100.0
        if align == "start":
            return center, align
        if align == "end":
            return center - box_width, align
        return center - box_width / 2.0, align

    def _line_slot(self, cue: Cue, line_count: int,
                   line_height_px: float) -> tuple[int, bool]:
        """Return (slot measured from top, line_was_auto)."""
        raw = cue.settings.get("line")
        if raw is None:
            return -1, True
        value = raw.split(",", 1)[0]
        if value.endswith("%"):
            y = self.vh * float(value[:-1]) / 100.0
            return max(0, round(y / line_height_px)), False
        line_no = int(value)
        align = raw.split(",", 1)[1] if "," in raw else "start"
        if align == "end" or line_no < 0:
            total_slots = int(self.vh // line_height_px)
            return total_slots + line_no - line_count + 1, False
        return line_no, False

    def layout_cue(self, cue: Cue, occupied_slots: set[int]) -> CueLayout:
        region = self.doc.regions.get(cue.region_id) if cue.region_id else None
        if region is not None:
            return self._layout_region_cue(cue, region)

        font_px, line_height_px = self.cue_font_px(cue)
        metrics = TextMetrics(font_px)
        margin = SAFE_MARGIN_PX
        available = self.vw - 2 * margin
        requested = self._resolve_size(cue, available)
        width = min(requested, available)
        rendered = wrap_cue(cue.lines, width, metrics, font_px)
        width = max(line.width_px for line in rendered)
        height = len(rendered) * line_height_px
        x, _align = self._horizontal_origin(cue, width)
        x = max(margin, min(x, self.vw - width - margin))

        slot, auto_line = self._line_slot(cue, len(rendered), line_height_px)
        if auto_line:
            total_slots = int(self.vh // line_height_px)
            slot = total_slots - len(rendered)
            while any(s in occupied_slots
                      for s in range(slot, slot + len(rendered))):
                slot -= 1
                if slot < 0:
                    slot = 0
                    break
        occupied_slots.update(range(slot, slot + len(rendered)))
        y = slot * line_height_px
        return CueLayout(cue, Box(x, y, width, height), rendered, slot,
                         layer=0, region_id=None, scroll_direction=None)

    def _layout_region_cue(self, cue: Cue, region: Region) -> CueLayout:
        region_layout = self.region_box(region)
        font_px, line_height_px = self.cue_font_px(cue)
        metrics = TextMetrics(font_px)
        inner_pad = SAFE_MARGIN_PX
        width = region_layout.box.width - 2 * inner_pad
        rendered = wrap_cue(cue.lines, width, metrics, font_px)
        width = max(line.width_px for line in rendered)
        height = len(rendered) * line_height_px
        x = region_layout.box.x + inner_pad
        align = cue.settings.get("align", "start")
        if align in ("middle", "center"):
            x += ((region_layout.box.width - 2 * inner_pad) - width) / 2
        elif align == "end":
            x += (region_layout.box.width - 2 * inner_pad) - width
        y = region_layout.box.bottom - height
        return CueLayout(cue, Box(x, y, width, height), rendered,
                         line_slot=0, layer=1, region_id=region.id,
                         scroll_direction=region.scroll)

    def active_cues(self, time: float) -> list[Cue]:
        return [cue for cue in self.doc.cues if cue.start <= time < cue.end]

    def simulate(self, sample_times: list[float]) -> list[FrameSnapshot]:
        snapshots: list[FrameSnapshot] = []
        region_state: dict[str, list[CueLayout]] = {
            region_id: [] for region_id in self.doc.regions
        }

        for time in sample_times:
            free_cues: list[Cue] = []
            region_cues: dict[str, list[Cue]] = {
                region_id: [] for region_id in self.doc.regions
            }
            for cue in self.active_cues(time):
                if cue.region_id and cue.region_id in region_state:
                    region_cues[cue.region_id].append(cue)
                else:
                    free_cues.append(cue)

            occupied: set[int] = set()
            free_layouts: list[CueLayout] = []
            for order, cue in enumerate(free_cues):
                layout = self.layout_cue(cue, occupied)
                layout.layer = order
                free_layouts.append(layout)

            region_layouts: list[RegionLayout] = []
            all_region_cue_layouts: list[CueLayout] = []
            clip_events: list[str] = []
            for region_id, region in self.doc.regions.items():
                region_layout = self.region_box(region)
                region_layouts.append(region_layout)
                current = region_cues[region_id]
                state = region_state[region_id]

                if region.scroll == "up":
                    known = {layout.cue.id: layout for layout in state}
                    kept = [known[cue.id] for cue in current if cue.id in known]
                    for cue in current:
                        if cue.id not in known:
                            entering = self._layout_region_cue(cue, region)
                            entering.layer = len(state)
                            kept.append(entering)
                    state = kept
                    top = region_layout.box.y
                    bottom = region_layout.box.bottom
                    running = bottom
                    for layout in reversed(state):
                        running -= layout.box.height
                        layout.box.y = running
                        overflow = top - layout.box.y
                        if overflow > 0:
                            clipped_px = min(overflow, layout.box.height)
                            clipped_lines = round(
                                clipped_px / self.line_height_px)
                            clip_events.append(
                                f"t={time:.3f}s region={region_id} cue="
                                f"{layout.cue.id} {clipped_lines} line(s) / "
                                f"{round(clipped_px, 1)}px clipped above top")
                    region_state[region_id] = state
                    all_region_cue_layouts.extend(state)
                else:
                    static = []
                    running = region_layout.box.y
                    for order, cue in enumerate(current):
                        layout = self._layout_region_cue(cue, region)
                        layout.box.y = running
                        running += layout.box.height
                        layout.layer = order
                        if layout.box.bottom > region_layout.box.bottom:
                            clip_events.append(
                                f"t={time:.3f}s region={region_id} cue="
                                f"{layout.cue.id} overflows static region bottom")
                        static.append(layout)
                    all_region_cue_layouts.extend(static)

            all_layouts = free_layouts + all_region_cue_layouts
            region_clip_boxes = {
                layout.region_id: next(
                    region_layout.box for region_layout in region_layouts
                    if region_layout.region.id == layout.region_id)
                for layout in all_region_cue_layouts
            }
            overlaps = self._detect_overlaps(all_layouts, region_clip_boxes)
            snapshots.append(FrameSnapshot(
                time=time,
                cue_layouts=all_layouts,
                region_layouts=region_layouts,
                overlaps=overlaps,
                region_clip_events=clip_events,
            ))
        return snapshots

    def _detect_overlaps(self, layouts: list[CueLayout],
                         region_clip_boxes: dict[str | None, Box]
                         ) -> list[tuple[str, str]]:
        """Geometric intersection after region clipping is applied."""
        pairs: list[tuple[str, str]] = []
        effective: list[tuple[str, Box]] = []
        for layout in layouts:
            box = layout.box
            if layout.region_id and layout.region_id in region_clip_boxes:
                clip = region_clip_boxes[layout.region_id]
                box = Box(
                    max(box.x, clip.x), max(box.y, clip.y),
                    min(box.right, clip.right) - max(box.x, clip.x),
                    min(box.bottom, clip.bottom) - max(box.y, clip.y))
            if box.width > 0 and box.height > 0:
                effective.append((layout.cue.id, box))
        for idx, (left_id, left_box) in enumerate(effective):
            for right_id, right_box in effective[idx + 1:]:
                if left_box.intersects(right_box):
                    pairs.append((left_id, right_id))
        return pairs
