"""Reference renderer: vertical subtitle with bopomofo ruby, using Pillow.

Independently reproduces the intent of subtitle.html:
- vertical-rl layout: base glyphs in 48px em boxes stacked top-to-bottom
- ruby (bopomofo) at 16px stacked vertically in a column to the RIGHT
  of the base glyph, centered on the base glyph's em box
- 8px padding, white background, same font file at weight 400
"""

import os

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(__file__)
FONT_PATH = os.path.join(HERE, "..", "artifacts", "fonts", "NotoSansSC.ttf")
OUT_PATH = os.path.join(HERE, "..", "artifacts", "subtitle_reference.png")

TEXT = [("台", "ㄊㄞˊ"), ("北", "ㄅㄟˇ"), ("車", "ㄔㄜ"), ("站", "ㄓㄢˋ")]
BASE_SIZE = 48
RUBY_SIZE = 16
PAD = 8
RUBY_GAP = 4  # horizontal gap between base em box and ruby column


def main():
    base_font = ImageFont.truetype(FONT_PATH, BASE_SIZE)
    base_font.set_variation_by_axes([400])
    ruby_font = ImageFont.truetype(FONT_PATH, RUBY_SIZE)
    ruby_font.set_variation_by_axes([400])

    n = len(TEXT)
    ruby_col_w = RUBY_SIZE + 4
    width = PAD + BASE_SIZE + RUBY_GAP + ruby_col_w + PAD
    height = PAD + n * BASE_SIZE + PAD
    img = Image.new("L", (width, height), 255)
    draw = ImageDraw.Draw(img)

    for i, (base, ruby) in enumerate(TEXT):
        slot_y = PAD + i * BASE_SIZE
        # base glyph centered in its em box
        draw.text((PAD + BASE_SIZE / 2, slot_y + BASE_SIZE / 2), base,
                  font=base_font, fill=0, anchor="mm")
        # ruby: stack chars vertically, group centered on the em box
        group_h = len(ruby) * RUBY_SIZE
        ry = slot_y + (BASE_SIZE - group_h) / 2
        rx = PAD + BASE_SIZE + RUBY_GAP + ruby_col_w / 2
        for j, ch in enumerate(ruby):
            draw.text((rx, ry + j * RUBY_SIZE + RUBY_SIZE / 2), ch,
                      font=ruby_font, fill=0, anchor="mm")

    img.save(OUT_PATH)
    print("wrote", os.path.abspath(OUT_PATH), img.size)


if __name__ == "__main__":
    main()
