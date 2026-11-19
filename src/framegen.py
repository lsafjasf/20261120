"""Deterministic raw-RGB frame generation with numpy.

Each frame contains:
  * a moving pure white vertical bar on a dark background (true sharp edge);
  * a per-frame index rendered with a built-in 5x7 bitmap font;
  * a per-second horizontal marker band (subtitle sync reference).
Frames are emitted as raw rgb24 bytes on stdout or to a named pipe.
"""
import numpy as np

W, H = 640, 360
FPS = 30
DURATION = 12
NFRAMES = FPS * DURATION

FONT = {
    "0": ["01110", "10001", "10011", "10101", "11001", "10001", "01110"],
    "1": ["00100", "01100", "00100", "00100", "00100", "00100", "01110"],
    "2": ["01110", "10001", "00001", "00010", "00100", "01000", "11111"],
    "3": ["11110", "00001", "00001", "01110", "00001", "00001", "11110"],
    "4": ["00010", "00110", "01010", "10010", "11111", "00010", "00010"],
    "5": ["11111", "10000", "11110", "00001", "00001", "10001", "01110"],
    "6": ["00110", "01000", "10000", "11110", "10001", "10001", "01110"],
    "7": ["11111", "00001", "00010", "00100", "01000", "01000", "01000"],
    "8": ["01110", "10001", "10001", "01110", "10001", "10001", "01110"],
    "9": ["01110", "10001", "10001", "01111", "00001", "00010", "01100"],
}


def _draw_text(frame: np.ndarray, text: str, x0: int, y0: int,
               scale: int = 3, color=255) -> None:
    x = x0
    for ch in text:
        glyph = FONT[ch]
        for row, bits in enumerate(glyph):
            for col, bit in enumerate(bits):
                if bit == "1":
                    frame[y0 + row * scale:y0 + (row + 1) * scale,
                          x + col * scale:x + (col + 1) * scale] = color
        x += 6 * scale  # 5 px glyph + 1 px spacing, scaled


def render_frame(index: int) -> np.ndarray:
    """Return one HxWx3 uint8 RGB frame for frame number `index`."""
    frame = np.full((H, W, 3), 24, dtype=np.uint8)
    # moving sharp white bar, 14 px wide, 8 px/frame, wraps across width
    bar_x = (index * 8) % W
    x_end = min(bar_x + 14, W)
    frame[40:320, bar_x:x_end] = 255
    if bar_x + 14 > W:  # wrap continuation from left edge
        frame[40:320, 0:(bar_x + 14) - W] = 255
    # per-second marker band at the bottom: bright block whose width grows
    # with the second index, gives a visual subtitle/audio sync reference
    second = index // FPS
    frame[336:352, 12:12 + 20 * (second + 1)] = 200
    # static deblocking test patch (frames 30..239, i.e. inside the cut):
    # an 8x8-aligned checker of 16x16 pure black/white squares. It is motion
    # free, so any cross-8px-boundary smearing must come from the codec's
    # in-loop deblocker rather than from temporal motion.
    if 30 <= index < 240:
        x0, y0, cells, cell = 432, 96, 6, 16
        for cy in range(cells):
            for cx in range(cells):
                if (cx + cy) % 2 == 0:
                    frame[y0 + cy * cell:y0 + (cy + 1) * cell,
                          x0 + cx * cell:x0 + (cx + 1) * cell] = 255
        # flat mid-gray ramps (128 -> 138) in the upper-left: smooth content
        # where 8x8 blocking artifacts show most clearly under high CRF
        gx0, gy0, gw, gh = 60, 96, 240, 64
        ramp = np.linspace(128, 138, gw, dtype=np.uint8)
        frame[gy0:gy0 + gh, gx0:gw + gx0] = ramp[None, :, None]
        # 8x8-aligned mid-contrast static checker (80 vs 176): a dedicated
        # deblocking probe whose cell borders coincide with transform blocks
        qx0, qy0, qcells, qsize = 320, 180, 8, 8
        for qy in range(qcells):
            for qx in range(qcells):
                val = 176 if (qx + qy) % 2 == 0 else 80
                frame[qy0 + qy * qsize:qy0 + (qy + 1) * qsize,
                      qx0 + qx * qsize:qx0 + (qx + 1) * qsize] = val
    # frame number with a dark backing box
    label = f"{index:03d}"
    frame[8:34, 10:10 + 3 * 18] = 0
    _draw_text(frame, label, 12, 10)
    return frame


def raw_stream() -> bytes:
    """Concatenate all frames as rgb24 bytes."""
    buf = bytearray()
    for i in range(NFRAMES):
        buf.extend(render_frame(i).tobytes())
    return bytes(buf)
