"""Canonical in-memory subtitle model shared by every format codec.

Text uses canonical markup tags: <b>, <i>, <u>, <c #RRGGBB>, </c>.
All timestamps are integer milliseconds.
"""

from dataclasses import dataclass, field


@dataclass
class Color:
    r: int
    g: int
    b: int
    alpha: int = 0  # 0 = fully opaque, 255 = fully transparent (ASS scale)

    @classmethod
    def from_hex(cls, text):
        text = text.strip().lstrip("#")
        if len(text) == 3:
            text = "".join(ch * 2 for ch in text)
        if len(text) != 6:
            raise ValueError(f"bad color: {text!r}")
        return cls(int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16))

    @classmethod
    def from_css(cls, text):
        text = text.strip()
        if text.startswith("#"):
            return cls.from_hex(text)
        if text.startswith("rgb"):
            nums = text[text.index("(") + 1:text.index(")")].split(",")
            r, g, b = (int(float(n)) for n in nums[:3])
            alpha = 0
            if len(nums) == 4:
                alpha = round((1 - float(nums[3])) * 255)
            return cls(r, g, b, alpha)
        return cls.from_hex(text)

    @classmethod
    def from_ass(cls, text):
        text = text.strip().lstrip("&H").rstrip("&")
        alpha = 0
        if len(text) == 8:  # &HAABBGGRR&: leading alpha byte
            alpha = int(text[0:2], 16)
            text = text[2:]
        if len(text) == 6:  # ASS is &HBBGGRR&
            bb, gg, rr = text[0:2], text[2:4], text[4:6]
            return cls(int(rr, 16), int(gg, 16), int(bb, 16), alpha)
        return cls.from_hex(text)

    def hex(self):
        return f"#{self.r:02X}{self.g:02X}{self.b:02X}"

    def css_rgba(self):
        opacity = round(1 - self.alpha / 255, 3)
        return f"rgba({self.r}, {self.g}, {self.b}, {opacity})"

    def ass(self):
        return f"&H{self.alpha:02X}{self.b:02X}{self.g:02X}{self.r:02X}&"


@dataclass
class Style:
    name: str = "Default"
    font: str = "Arial"
    size: int = 20
    primary_color: Color = field(default_factory=lambda: Color(255, 255, 255))
    bold: bool = False
    italic: bool = False
    underline: bool = False
    back_color: Color | None = None
    outline_color: Color | None = None
    # ASS alignment 1..11 (numpad); canonical default bottom-center = 2
    alignment: int = 2
    margin_l: int = 10
    margin_r: int = 10
    margin_v: int = 10
    border_style: int = 1
    outline: float = 2.0
    shadow: float = 2.0
    # ASS-only typography (no SRT/VTT mapping; retained for ASS identity)
    scale_x: int = 100
    scale_y: int = 100
    spacing: int = 0
    strike_out: bool = False
    angle: float = 0.0
    encoding: int = 1


@dataclass
class Cue:
    start: int
    end: int
    text: str
    identifier: str = ""
    style_name: str = "Default"
    # ASS-only geometry that has no SRT/WebVTT equivalent
    pos: tuple | None = None  # (x, y)
    rotation: int = 0


@dataclass
class LossEntry:
    scope: str          # "style" | "cue" | "document"
    name: str           # feature that could not be mapped losslessly
    detail: str
    cue_index: int | None = None


@dataclass
class Document:
    cues: list = field(default_factory=list)
    styles: dict = field(default_factory=dict)
    # free-form header info (e.g. ASS Script Info), kept for reference
    headers: dict = field(default_factory=dict)
    language: str = ""
    losses: list = field(default_factory=list)

    def add_loss(self, scope, name, detail, cue_index=None):
        self.losses.append(LossEntry(scope, name, detail, cue_index))

    def default_style(self):
        if "Default" in self.styles:
            return self.styles["Default"]
        style = Style()
        self.styles["Default"] = style
        return style
