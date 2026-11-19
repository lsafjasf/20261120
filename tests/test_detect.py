from __future__ import annotations

import unittest

import cv2
import numpy as np

from aspect_audit.detect import detect_frame_layout


def make_frame(
    layout: str,
    width: int = 640,
    height: int = 360,
    subtitle: bool = False,
) -> np.ndarray:
    canvas = np.zeros((height, width, 3), dtype=np.uint8)
    if layout == "full":
        x, y, active_width, active_height = 0, 0, width, height
    elif layout == "letterbox":
        active_height = int(round(width / 2.39))
        x, y, active_width = 0, (height - active_height) // 2, width
    elif layout == "pillarbox":
        active_width = int(round(height * 4 / 3))
        x, y, active_height = (width - active_width) // 2, 0, height
    elif layout == "fourside":
        active_width, active_height = int(width * 0.72), int(height * 0.72)
        x, y = (width - active_width) // 2, (height - active_height) // 2
    else:
        raise ValueError(layout)
    yy, xx = np.mgrid[y : y + active_height, x : x + active_width]
    active = np.empty((active_height, active_width, 3), dtype=np.uint8)
    active[:, :, 0] = np.clip(70 + xx / 5, 0, 255)
    active[:, :, 1] = np.clip(130 + yy / 4, 0, 255)
    active[:, :, 2] = np.clip(170 - xx / 8, 0, 255)
    canvas[y : y + active_height, x : x + active_width] = active
    if subtitle and layout == "letterbox":
        cv2.putText(
            canvas, "CAPTION IN BAR", (width // 3, y + active_height + 28),
            cv2.FONT_HERSHEY_SIMPLEX, 0.65, (240, 240, 230), 2, cv2.LINE_AA,
        )
    return canvas


class DetectFrameTests(unittest.TestCase):
    def test_full_frame(self) -> None:
        result = detect_frame_layout(make_frame("full"))
        self.assertEqual(result["layout"], "full")
        self.assertEqual(set(result["bars"].values()), {0})

    def test_letterbox(self) -> None:
        result = detect_frame_layout(make_frame("letterbox"))
        self.assertEqual(result["layout"], "letterbox")
        self.assertGreaterEqual(result["bars"]["top"], 25)
        self.assertGreaterEqual(result["bars"]["bottom"], 25)
        self.assertEqual(result["bars"]["left"], 0)

    def test_pillarbox(self) -> None:
        result = detect_frame_layout(make_frame("pillarbox"))
        self.assertEqual(result["layout"], "pillarbox")
        self.assertGreaterEqual(result["bars"]["left"], 55)
        self.assertGreaterEqual(result["bars"]["right"], 55)

    def test_four_side_black(self) -> None:
        result = detect_frame_layout(make_frame("fourside"))
        self.assertEqual(result["layout"], "fourside")
        self.assertTrue(all(value > 0 for value in result["bars"].values()))

    def test_subtitle_does_not_break_letterbox_depth(self) -> None:
        clean = detect_frame_layout(make_frame("letterbox"))
        captioned = detect_frame_layout(make_frame("letterbox", subtitle=True))
        self.assertEqual(captioned["layout"], "letterbox")
        self.assertEqual(captioned["bars"], clean["bars"])
        self.assertIn("bottom", captioned["subtitle_edges"])
        self.assertGreater(captioned["subtitle_edges"]["bottom"], 0.4)

    def test_uniform_black_is_not_letterbox(self) -> None:
        black = np.zeros((360, 640, 3), dtype=np.uint8)
        result = detect_frame_layout(black)
        self.assertEqual(result["layout"], "black")

    def test_low_light_is_excluded(self) -> None:
        dark = np.full((360, 640, 3), 25, dtype=np.uint8)
        result = detect_frame_layout(dark)
        self.assertEqual(result["layout"], "unknown")


if __name__ == "__main__":
    unittest.main()
