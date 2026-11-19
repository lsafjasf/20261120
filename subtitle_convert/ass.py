"""Advanced SubStation Alpha (ASS / SSA) codec."""

import re

from . import markup
from .model import Document, Cue, Style, Color
from .timefmt import parse_ass_time, format_ass_time

_SECTION_RE = re.compile(r"^\[(.+?)\]\s*$")
_STYLE_FIELDS = ["name", "font", "size", "primary_color", "secondary_color",
                 "outline_color", "back_color", "bold", "italic",
                 "underline", "strike_out", "scale_x", "scale_y", "spacing",
                 "angle", "border_style", "outline", "shadow", "alignment",
                 "margin_l", "margin_r", "margin_v", "encoding"]
_EVENT_FIELDS = ["layer", "start", "end", "style", "name", "margin_l",
                 "margin_r", "margin_v", "effect", "text"]
_STYLE_HEADER = ["Name", "Fontname", "Fontsize", "PrimaryColour",
                 "SecondaryColour", "OutlineColour", "BackColour", "Bold",
                 "Italic", "Underline", "StrikeOut", "ScaleX", "ScaleY",
                 "Spacing", "Angle", "BorderStyle", "Outline", "Shadow",
                 "Alignment", "MarginL", "MarginR", "MarginV", "Encoding"]
_EVENT_HEADER = ["Layer", "Start", "End", "Style", "Name", "MarginL",
                 "MarginR", "MarginV", "Effect", "Text"]
_FIELD_ALIASES = {
    "fontname": "font",
    "fontsize": "size",
    "primarycolour": "primary_color",
    "secondarycolour": "secondary_color",
    "outlinecolour": "outline_color",
    "backcolour": "back_color",
    "strikeout": "strike_out",
    "scalex": "scale_x",
    "scaley": "scale_y",
    "borderstyle": "border_style",
    "marginl": "margin_l",
    "marginr": "margin_r",
    "marginv": "margin_v",
}


def parse(text):
    doc = Document()
    section = None
    style_format = list(_STYLE_FIELDS)
    event_format = list(_EVENT_FIELDS)
    for raw in text.replace("\r\n", "\n").split("\n"):
        line = raw.strip()
        sec = _SECTION_RE.match(line)
        if sec:
            section = sec.group(1).lower()
            continue
        if not line or line.startswith(";"):
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key, value = key.strip().lower(), value.strip()
        if section == "script info":
            doc.headers[key] = value
        elif section in ("v4+ styles", "v4 styles"):
            if key == "format":
                style_format = [_canon_field(fld) for fld in value.split(",")]
            elif key in ("style", "stylerec"):
                _parse_style(doc, value, style_format)
        elif section == "events":
            if key == "format":
                event_format = [_canon_field(fld) for fld in value.split(",")]
            elif key in ("dialogue", "comment"):
                _parse_dialogue(doc, key, value, event_format)
        elif section and "graphics" in section:
            doc.add_loss("document", "ass-graphics",
                         f"[{section}] section has no SRT/VTT mapping")
    if "Default" not in doc.styles:
        doc.styles["Default"] = Style()
    return doc


def _split_fields(value, names):
    parts = value.split(",", len(names) - 1)
    parts += [""] * (len(names) - len(parts))
    return {_canon_field(name): part.strip()
            for name, part in zip(names, parts)}


def _canon_field(name):
    # ASS Format headers vary in case ("Fontsize" vs "fontsize").
    canon = name.strip().lower().replace(" ", "")
    return _FIELD_ALIASES.get(canon, canon)


def _parse_style(doc, value, names):
    f = _split_fields(value, names)
    style = Style(name=f.get("name", "Default") or "Default")
    style.font = f.get("font", style.font) or style.font
    style.size = _int(f.get("size"), style.size)
    style.primary_color = _color(f.get("primary_color"), style.primary_color)
    style.outline_color = _color(f.get("outline_color"), None)
    style.back_color = _color(f.get("back_color"), None)
    style.bold = _int(f.get("bold"), 0) != 0
    style.italic = _int(f.get("italic"), 0) != 0
    style.underline = _int(f.get("underline"), 0) != 0
    style.alignment = _int(f.get("alignment"), style.alignment)
    style.border_style = _int(f.get("border_style"), style.border_style)
    style.outline = _float(f.get("outline"), style.outline)
    style.shadow = _float(f.get("shadow"), style.shadow)
    style.scale_x = _int(f.get("scale_x"), style.scale_x)
    style.scale_y = _int(f.get("scale_y"), style.scale_y)
    style.spacing = _int(f.get("spacing"), style.spacing)
    style.strike_out = _int(f.get("strike_out"), 0) != 0
    style.angle = _float(f.get("angle"), style.angle)
    style.encoding = _int(f.get("encoding"), style.encoding)
    style.margin_l = _int(f.get("margin_l"), style.margin_l)
    style.margin_r = _int(f.get("margin_r"), style.margin_r)
    style.margin_v = _int(f.get("margin_v"), style.margin_v)
    if style.scale_x != 100 or style.scale_y != 100:
        doc.add_loss("style", "ass-scale",
                     f"style {style.name!r}: scale_x/scale_y not mapped")
    if style.strike_out:
        doc.add_loss("style", "ass-strikeout",
                     f"style {style.name!r}: strikeout not mapped")
    if style.spacing:
        doc.add_loss("style", "ass-spacing",
                     f"style {style.name!r}: character spacing not mapped")
    doc.styles[style.name] = style


def _parse_dialogue(doc, key, value, names):
    f = _split_fields(value, names)
    if key == "comment":
        doc.add_loss("cue", "ass-comment",
                     "Comment event dropped", len(doc.cues))
        return
    raw_text = f.get("text", "")
    pos, rotation = _extract_geometry(raw_text, doc, len(doc.cues))
    style_name = f.get("style", "Default") or "Default"
    style = doc.styles.get(style_name)
    text = markup.ass_to_canonical(raw_text, loss_sink=doc.add_loss,
                                   cue_index=len(doc.cues), style=style)
    if style_name not in doc.styles:
        doc.add_loss("cue", "missing-style",
                     f"style {style_name!r} referenced but not defined",
                     len(doc.cues))
    cue = Cue(parse_ass_time(f["start"]), parse_ass_time(f["end"]), text,
              style_name=style_name, pos=pos, rotation=rotation)
    doc.cues.append(cue)


def _extract_geometry(text, doc, idx):
    pos, rotation = None, 0
    m = re.search(r"\\pos\(([-\d.]+),([-\d.]+)\)", text)
    if m:
        pos = (float(m.group(1)), float(m.group(2)))
    if re.search(r"\\move\(", text):
        doc.add_loss("cue", "ass-move",
                     "\\move animation has no SRT/VTT mapping", idx)
    m = re.search(r"\\frz\(([-\d.]+)\)", text)
    if m:
        rotation = int(float(m.group(1)))
    if re.search(r"\\fad\(", text):
        doc.add_loss("cue", "ass-fad",
                     "\\fad animation not representable in SRT/VTT", idx)
    if re.search(r"\\t\(", text):
        doc.add_loss("cue", "ass-animation",
                     "\\t animation not representable in SRT/VTT", idx)
    return pos, rotation


def dump(doc):
    out = ["[Script Info]", "ScriptType: v4.00+", "WrapStyle: 0",
           "ScaledBorderAndShadow: yes", ""]
    skip = {"scripttype", "wrapstyle", "scaledborderandshadow",
            "signature"}
    for key, value in doc.headers.items():
        if key not in skip:
            out.append(f"{_title(key)}: {value}")
    out += ["", "[V4+ Styles]",
            "Format: " + ", ".join(_STYLE_HEADER)]
    for style in doc.styles.values():
        out.append("Style: " + _dump_style(style))
    out += ["", "[Events]",
            "Format: " + ", ".join(_EVENT_HEADER)]
    for cue in doc.cues:
        style = doc.styles.get(cue.style_name) or doc.default_style()
        text = markup.canonical_to_ass(cue.text, style)
        text = _inject_geometry(text, cue)
        fields = ["0", format_ass_time(cue.start), format_ass_time(cue.end),
                  cue.style_name, "", "0", "0", "0", "", text]
        out.append("Dialogue: " + ",".join(fields))
    return "\n".join(out) + "\n"


def _dump_style(style):
    values = {
        "name": style.name, "font": style.font, "size": str(style.size),
        "primary_color": style.primary_color.ass(),
        "secondary_color": "&H0000FF&",
        "outline_color": (style.outline_color or Color(0, 0, 0)).ass(),
        "back_color": (style.back_color or Color(0, 0, 0)).ass(),
        "bold": "-1" if style.bold else "0",
        "italic": "-1" if style.italic else "0",
        "underline": "-1" if style.underline else "0",
        "strike_out": "-1" if style.strike_out else "0",
        "scale_x": str(style.scale_x), "scale_y": str(style.scale_y),
        "spacing": str(style.spacing), "angle": str(style.angle),
        "border_style": str(style.border_style),
        "outline": str(style.outline), "shadow": str(style.shadow),
        "alignment": str(style.alignment), "margin_l": str(style.margin_l),
        "margin_r": str(style.margin_r), "margin_v": str(style.margin_v),
        "encoding": str(style.encoding),
    }
    return ",".join(values[name] for name in _STYLE_FIELDS)


def _inject_geometry(text, cue):
    prefix = ""
    if cue.pos:
        prefix += f"\\pos({cue.pos[0]:g},{cue.pos[1]:g})"
    if cue.rotation:
        prefix += f"\\frz({cue.rotation})"
    if prefix:
        return "{" + prefix + "}" + text
    return text


def _color(raw, default):
    if not raw:
        return default
    try:
        return Color.from_ass(raw)
    except ValueError:
        return default


def _int(raw, default):
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        return default


def _float(raw, default):
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def _title(key):
    return "".join(part.capitalize() for part in key.split("_"))
