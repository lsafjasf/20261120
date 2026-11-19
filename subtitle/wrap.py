"""Word-wrap point calculation for subtitles rendered in a scrolling region.

The player layout is modelled as a video canvas of fixed height and a
player width W that changes on resize. The subtitle scroll region is the
bottom strip of the canvas; text must wrap at the region's content width and
must never extend above the region's top edge (which borders the main
picture), otherwise it occludes the video.
"""

from __future__ import annotations

from dataclasses import dataclass

from PIL import ImageFont


@dataclass
class Layout:
    player_width_px: int
    canvas_height_px: int
    side_margin_px: int
    region_height_px: int
    line_gap_px: int
    font_path: str
    font_size_px: int
    max_region_fraction: float = 0.45

    @property
    def content_width_px(self) -> int:
        return self.player_width_px - 2 * self.side_margin_px

    @property
    def region_top_px(self) -> int:
        return self.canvas_height_px - self.region_height_px


@dataclass
class Line:
    text: str
    width_px: int


@dataclass
class WrapResult:
    layout: Layout
    lines: list[Line]
    block_height_px: int
    requested_region_height_px: int
    effective_region_height_px: int
    region_top_px: int
    block_top_px: int
    fits_scroll_region: bool
    occludes_picture: bool
    region_expanded: bool


def _load_font(layout: Layout) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(layout.font_path, layout.font_size_px)
    except OSError:
        return ImageFont.load_default()


def tokenize(text: str) -> list[str]:
    """Split on spaces while keeping CJK runs as individual tokens.

    CJK characters have no inter-word spaces, so each CJK ideograph becomes
    its own wrap opportunity; Latin words stay atomic."""
    tokens: list[str] = []
    for word in text.split(" "):
        if not word:
            tokens.append(" ")
            continue
        run = ""
        for ch in word:
            if _is_cjk(ch):
                if run:
                    tokens.append(run)
                    run = ""
                tokens.append(ch)
            else:
                run += ch
        if run:
            tokens.append(run)
        tokens.append(" ")
    if tokens and tokens[-1] == " ":
        tokens.pop()
    return tokens


def _is_cjk(ch: str) -> bool:
    code = ord(ch)
    return (
        0x4E00 <= code <= 0x9FFF
        or 0x3400 <= code <= 0x4DBF
        or 0x3000 <= code <= 0x303F
        or 0xFF00 <= code <= 0xFFEF
    )


def wrap_subtitle(text: str, layout: Layout) -> WrapResult:
    """Greedy wrap at measured pixel widths for the given player width."""
    font = _load_font(layout)
    max_width = layout.content_width_px

    def measure(piece: str) -> int:
        if not piece:
            return 0
        box = font.getbbox(piece)
        return box[2] - box[0]

    line_height = layout.font_size_px + layout.line_gap_px
    lines: list[Line] = []
    current = ""
    for token in tokenize(text):
        candidate = current + token
        if current and measure(candidate) > max_width:
            lines.append(Line(current.rstrip(), measure(current.rstrip())))
            current = "" if token == " " else token
        else:
            current = candidate
    if current.strip():
        lines.append(Line(current.rstrip(), measure(current.rstrip())))

    block_height = len(lines) * line_height - layout.line_gap_px
    inner_pad = layout.line_gap_px
    needed_region = block_height + 2 * inner_pad
    # The scroll region starts at its configured height but may grow upward
    # (shrinking the picture) up to a capped fraction of the canvas so wrapped
    # text never paints over the picture boundary.
    max_region = int(layout.canvas_height_px * layout.max_region_fraction)
    effective_region = min(max(layout.region_height_px, needed_region),
                           max_region)
    region_top = layout.canvas_height_px - effective_region
    # Bottom-anchored inside the (possibly expanded) scroll region.
    block_top = layout.canvas_height_px - inner_pad - block_height
    fits = needed_region <= max_region
    occludes = block_top < region_top

    return WrapResult(
        layout=layout,
        lines=lines,
        block_height_px=block_height,
        requested_region_height_px=layout.region_height_px,
        effective_region_height_px=effective_region,
        region_top_px=region_top,
        block_top_px=block_top,
        fits_scroll_region=fits,
        occludes_picture=occludes,
        region_expanded=effective_region > layout.region_height_px,
    )
