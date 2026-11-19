"""Recompute subtitle wrap points across player-width changes and verify the
scrolling region never overlaps the main picture."""

from __future__ import annotations

import json
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from PIL import Image, ImageDraw, ImageFont

from .wrap import Layout, WrapResult, wrap_subtitle

ROOT = os.path.dirname(os.path.dirname(__file__))
OUT_DIR = os.path.join(ROOT, "outputs")
FONT_PATH = os.path.join(ROOT, "subtitle", "fonts", "NotoSansSC-Regular.ttf")

CANVAS_HEIGHT = 720
SIDE_MARGIN = 24
REGION_HEIGHT = 170
LINE_GAP = 10
FONT_SIZE = 30

SUBTITLE = (
    "当播放器宽度发生变化时，滚动字幕区域内的文本必须按照新的内容宽度"
    "重新计算换行点，并且所有行始终保持在底部滚动区域内，不遮挡主画面。"
)
PLAYER_WIDTHS = [1280, 1024, 800, 640, 480, 360]


def make_layout(width: int) -> Layout:
    return Layout(
        player_width_px=width,
        canvas_height_px=CANVAS_HEIGHT,
        side_margin_px=SIDE_MARGIN,
        region_height_px=REGION_HEIGHT,
        line_gap_px=LINE_GAP,
        font_path=FONT_PATH,
        font_size_px=FONT_SIZE,
    )


def render_mockup(result: WrapResult, path: str) -> None:
    """Draw the player canvas, scroll region and wrapped subtitle lines."""
    layout = result.layout
    img = Image.new("RGB", (layout.player_width_px, layout.canvas_height_px),
                    color=(24, 26, 32))
    draw = ImageDraw.Draw(img)
    font = ImageFont.truetype(FONT_PATH, layout.font_size_px)

    # Main picture area (everything above the effective scroll region).
    draw.rectangle([0, 0, layout.player_width_px - 1, result.region_top_px - 1],
                   fill=(52, 88, 128))
    draw.text((16, 12), "MAIN PICTURE AREA", fill=(210, 225, 245), font=font)
    # Effective scroll region and its top boundary.
    draw.rectangle(
        [0, result.region_top_px, layout.player_width_px - 1,
         layout.canvas_height_px - 1],
        fill=(18, 18, 18),
    )
    boundary_color = (255, 170, 0) if result.region_expanded else (255, 80, 80)
    draw.line([0, result.region_top_px, layout.player_width_px - 1,
               result.region_top_px], fill=boundary_color, width=2)
    # Dashed reference for the originally configured region top.
    base_region_top = layout.canvas_height_px - layout.region_height_px
    for x in range(0, layout.player_width_px, 16):
        draw.line([x, base_region_top, min(x + 8, layout.player_width_px - 1),
                   base_region_top], fill=(120, 120, 120), width=1)

    line_height = layout.font_size_px + layout.line_gap_px
    y = result.block_top_px
    for line in result.lines:
        draw.text((layout.side_margin_px, y), line.text,
                  fill=(255, 255, 255), font=font)
        y += line_height
    img.save(path)


def main() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    font_prop = font_manager.FontProperties(fname=FONT_PATH)
    results: list[WrapResult] = [
        wrap_subtitle(SUBTITLE, make_layout(w)) for w in PLAYER_WIDTHS
    ]

    # Composite mock-up figure for the report.
    fig, axes = plt.subplots(len(results), 1, figsize=(11, 14))
    report_rows = []
    for ax, result in zip(axes, results):
        png_path = os.path.join(
            OUT_DIR,
            f"07_subtitle_w{result.layout.player_width_px}.png",
        )
        render_mockup(result, png_path)
        ax.imshow(plt.imread(png_path))
        ax.set_title(
            f"player width = {result.layout.player_width_px} px, "
            f"content width = {result.layout.content_width_px} px, "
            f"lines = {len(result.lines)}, "
            f"block top = {result.block_top_px} px, "
            f"effective region top = {result.region_top_px} px, "
            f"region expanded = {result.region_expanded}, "
            f"occludes = {result.occludes_picture}",
            fontproperties=font_prop, fontsize=10,
        )
        ax.axis("off")
        report_rows.append({
            "player_width_px": result.layout.player_width_px,
            "content_width_px": result.layout.content_width_px,
            "line_count": len(result.lines),
            "lines": [line.text for line in result.lines],
            "line_widths_px": [line.width_px for line in result.lines],
            "block_height_px": result.block_height_px,
            "base_region_top_px": CANVAS_HEIGHT - REGION_HEIGHT,
            "effective_region_height_px": result.effective_region_height_px,
            "region_top_px": result.region_top_px,
            "block_top_px": result.block_top_px,
            "fits_scroll_region": result.fits_scroll_region,
            "occludes_picture": result.occludes_picture,
            "region_expanded": result.region_expanded,
            "clearance_to_picture_px": result.block_top_px - result.region_top_px,
        })
    fig.tight_layout()
    fig.savefig(os.path.join(OUT_DIR, "07_subtitle_resize_wrap.png"), dpi=120)
    plt.close(fig)

    # Wrap-point summary: number of lines and clearance vs player width.
    fig, ax1 = plt.subplots(figsize=(9, 5))
    widths = [r["player_width_px"] for r in report_rows]
    ax1.plot(widths, [r["line_count"] for r in report_rows], "o-",
             label="line count")
    ax1.set_xlabel("player width (px)")
    ax1.set_ylabel("wrapped line count")
    ax1.invert_xaxis()
    ax1.grid(alpha=0.4)
    ax2 = ax1.twinx()
    ax2.plot(widths, [r["clearance_to_picture_px"] for r in report_rows],
             "s--", color="crimson", label="block-top clearance to picture (px)")
    ax2.axhline(0, color="k", lw=0.8)
    ax2.set_ylabel("clearance (px), >= 0 means no occlusion")
    lines1, labels1 = ax1.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax1.legend(lines1 + lines2, labels1 + labels2, loc="upper center")
    ax1.set_title("wrap points and picture clearance vs player width")
    fig.tight_layout()
    fig.savefig(os.path.join(OUT_DIR, "08_wrap_points_vs_width.png"), dpi=130)
    plt.close(fig)

    report = {
        "canvas_height_px": CANVAS_HEIGHT,
        "scroll_region_height_px": REGION_HEIGHT,
        "base_scroll_region_top_px": CANVAS_HEIGHT - REGION_HEIGHT,
        "max_region_fraction_of_canvas": 0.45,
        "font_size_px": FONT_SIZE,
        "subtitle": SUBTITLE,
        "scenarios": report_rows,
        "all_scenarios_no_occlusion": all(
            not r["occludes_picture"] and r["fits_scroll_region"]
            for r in report_rows
        ),
    }
    with open(os.path.join(OUT_DIR, "subtitle_report.json"), "w",
              encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    print(json.dumps(report, indent=2, ensure_ascii=False)[:3000])


if __name__ == "__main__":
    main()
