"""Side-by-side preview: center instant vs uniform vs triangle shutter.

Builds a contact sheet with one row per output frame and three
columns (center instant, uniform shutter, triangle shutter).  Frames
affected by missing subframes get a red banner so problems are
visible at a glance.
"""

from __future__ import annotations

from typing import List, Optional, Sequence

import numpy as np
from PIL import Image, ImageDraw

from .imageio import to_display

LABEL_H = 18
PAD = 4


def _label(draw: ImageDraw.ImageDraw, x: int, y: int, text: str, fill):
    draw.text((x + 2, y + 3), text, fill=fill)


def _column(
    frame: np.ndarray,
    label: str,
    flagged: bool,
    flag_text: str = "",
    min_width: int = 0,
) -> Image.Image:
    rgb = to_display(frame)[..., :3]
    img = Image.fromarray(rgb, "RGB")
    w, h = img.size
    w = max(w, min_width)
    canvas = Image.new("RGB", (w, h + LABEL_H), (16, 16, 16))
    canvas.paste(img, (0, LABEL_H))
    draw = ImageDraw.Draw(canvas)
    if flagged:
        draw.rectangle([0, 0, w - 1, h + LABEL_H - 1], outline=(220, 40, 40))
        label = f"{label} [{flag_text or 'MISSING'}]"
        _label(draw, 0, 0, label, (255, 80, 80))
    else:
        _label(draw, 0, 0, label, (220, 220, 220))
    return canvas


def contact_sheet(
    rows: Sequence[Sequence[np.ndarray]],
    labels: Sequence[Sequence[str]],
    flagged: Optional[Sequence[Sequence[bool]]] = None,
    flag_texts: Optional[Sequence[Sequence[str]]] = None,
) -> Image.Image:
    """Assemble a grid of labeled frames into one image."""
    n_rows = len(rows)
    n_cols = len(rows[0])
    flagged = flagged or [[False] * n_cols for _ in range(n_rows)]
    flag_texts = flag_texts or [[""] * n_cols for _ in range(n_rows)]
    cells: List[List[Image.Image]] = []
    min_width = max(200, max(r[0].shape[1] for r in rows))
    for r in range(n_rows):
        row_imgs = []
        for c in range(n_cols):
            row_imgs.append(
                _column(rows[r][c], labels[r][c], flagged[r][c],
                        flag_texts[r][c], min_width)
            )
        cells.append(row_imgs)
    cw = max(img.width for row in cells for img in row)
    ch = max(img.height for row in cells for img in row)
    sheet = Image.new(
        "RGB",
        (n_cols * cw + (n_cols + 1) * PAD, n_rows * ch + (n_rows + 1) * PAD),
        (8, 8, 8),
    )
    for r, row in enumerate(cells):
        for c, img in enumerate(row):
            x = PAD + c * (cw + PAD)
            y = PAD + r * (ch + PAD)
            sheet.paste(img, (x, y))
    return sheet


def center_instant(
    frames: np.ndarray, times: Sequence[float], t_open: float, t_close: float
) -> np.ndarray:
    """The subframe nearest the shutter center (no motion blur)."""
    mid = 0.5 * (t_open + t_close)
    idx = int(np.argmin([abs(t - mid) for t in times]))
    return frames[idx]
