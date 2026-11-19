"""Compare browser vs reference subtitle renders: per-character pixel
offsets of the vertical base glyphs and the bopomofo ruby column.

Method:
- binarize both renders
- browser: slot boxes and base/ruby split come from the DOM geometry
  dumped by screenshot.js; reference: from its layout constants
- align the two images globally by the first base glyph centroid
- report per-slot centroid offsets (dx, dy) for base and ruby
"""

import json
import os

import numpy as np
from PIL import Image

HERE = os.path.dirname(__file__)
ART = os.path.join(HERE, "..", "artifacts")
CHARS = ["台", "北", "車", "站"]
# reference renderer layout constants (must match reference.py)
REF_PAD = 8
REF_SLOT = 48
REF_SPLIT_X = REF_PAD + REF_SLOT + 4  # base box end + ruby gap


def ink(path):
    return np.asarray(Image.open(path).convert("L")) < 128


def centroid(mask):
    ys, xs = np.nonzero(mask)
    if len(xs) == 0:
        return None
    return float(xs.mean()), float(ys.mean())


def box_centroids(img, boxes):
    """boxes: list of (y0, y1, split_x); base = x < split, ruby = x >= split."""
    out = []
    for y0, y1, split_x in boxes:
        slot = img[y0:y1, :]
        base = slot.copy()
        base[:, split_x:] = False
        ruby = slot.copy()
        ruby[:, :split_x] = False
        out.append((centroid(base), centroid(ruby)))
    return out


def main():
    browser = ink(os.path.join(ART, "subtitle_browser.png"))
    reference = ink(os.path.join(ART, "subtitle_reference.png"))
    with open(os.path.join(ART, "browser_geometry.json")) as f:
        geom = json.load(f)
    # screenshot clip origin = min over all boxes
    clip_x = min(min(g["base"]["x"], g["ruby"]["x"]) for g in geom)
    clip_y = min(min(g["base"]["y"], g["ruby"]["y"]) for g in geom)
    b_boxes = []
    for g in geom:
        y0 = round(g["base"]["y"] - clip_y)
        y1 = y0 + round(g["base"]["h"])
        split_x = round(g["ruby"]["x"] - clip_x)
        b_boxes.append((y0, y1, split_x))
    r_boxes = [(REF_PAD + i * REF_SLOT, REF_PAD + (i + 1) * REF_SLOT,
                REF_SPLIT_X) for i in range(len(CHARS))]
    cb = box_centroids(browser, b_boxes)
    cr = box_centroids(reference, r_boxes)

    # global alignment: first base glyph centroid
    ax = cr[0][0][0] - cb[0][0][0]
    ay = cr[0][0][1] - cb[0][0][1]

    report = {"global_alignment": {"dx": ax, "dy": ay}, "characters": []}
    print(f"global alignment (ref - browser): dx={ax:.2f} dy={ay:.2f}")
    print(f"{'char':<4} {'base_dx':>8} {'base_dy':>8} "
          f"{'ruby_dx':>8} {'ruby_dy':>8}")
    for i, ch in enumerate(CHARS):
        (bbx, bby), (brx, bry) = cb[i]
        (rbx, rby), (rrx, rry) = cr[i]
        row = {
            "char": ch,
            "base_dx": (rbx - ax) - bbx, "base_dy": (rby - ay) - bby,
            "ruby_dx": (rrx - ax) - brx, "ruby_dy": (rry - ay) - bry,
        }
        report["characters"].append(row)
        print(f"{ch:<4} {row['base_dx']:8.2f} {row['base_dy']:8.2f} "
              f"{row['ruby_dx']:8.2f} {row['ruby_dy']:8.2f}")

    for key in ("base", "ruby"):
        dxs = [abs(r[f"{key}_dx"]) for r in report["characters"]]
        dys = [abs(r[f"{key}_dy"]) for r in report["characters"]]
        report[f"{key}_summary"] = {
            "mean_abs_dx": float(np.mean(dxs)), "max_abs_dx": float(max(dxs)),
            "mean_abs_dy": float(np.mean(dys)), "max_abs_dy": float(max(dys)),
        }
        s = report[f"{key}_summary"]
        print(f"{key}: mean|dx|={s['mean_abs_dx']:.2f} max|dx|={s['max_abs_dx']:.2f} "
              f"mean|dy|={s['mean_abs_dy']:.2f} max|dy|={s['max_abs_dy']:.2f}")

    with open(os.path.join(ART, "subtitle_compare.json"), "w") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    print("wrote", os.path.abspath(os.path.join(ART, "subtitle_compare.json")))


if __name__ == "__main__":
    main()
