"""WebVTT codec with STYLE blocks, cue settings and inline color."""

import re

from . import markup
from .model import Document, Cue, Style, Color
from .timefmt import parse_vtt_time, format_vtt_time

_TIMING_RE = re.compile(
    r"((?:\d{1,2}:)?\d{1,2}:\d{2}\.\d{1,3})\s*-->\s*"
    r"((?:\d{1,2}:)?\d{1,2}:\d{2}\.\d{1,3})(.*)"
)
_STYLE_RULE_RE = re.compile(
    r"::cue\((?:\.([A-Za-z0-9_-]+))?\)\s*\{([^}]*)\}"
)


def parse(text):
    doc = Document()
    doc.styles["Default"] = Style()
    text = text.replace("\r\n", "\n")
    blocks = re.split(r"\n\n+", text.strip())
    for block in blocks:
        lines = block.split("\n")
        if lines[0].startswith("WEBVTT"):
            doc.headers["signature"] = lines[0]
            continue
        if lines[0].startswith("NOTE"):
            continue
        if lines[0].startswith("STYLE"):
            _parse_style_block(doc, "\n".join(lines[1:]))
            continue
        if lines[0].startswith("REGION"):
            doc.add_loss("document", "vtt-region",
                         "REGION block has no SRT/ASS-passthrough mapping")
            continue
        timing_idx = next(
            (i for i, ln in enumerate(lines) if _TIMING_RE.search(ln)), None)
        if timing_idx is None:
            doc.add_loss("document", "unparsed-block",
                         f"block skipped: {lines[0]!r}")
            continue
        identifier = "\n".join(lines[:timing_idx]).strip()
        m = _TIMING_RE.search(lines[timing_idx])
        start, end, settings = m.groups()
        style_name = _parse_settings(doc, settings.strip(), identifier)
        body = "\n".join(lines[timing_idx + 1:])
        body = markup.html_to_canonical(body, loss_sink=doc.add_loss)
        cue = Cue(parse_vtt_time(start), parse_vtt_time(end), body,
                  identifier=identifier, style_name=style_name)
        doc.cues.append(cue)
    return doc


def _parse_style_block(doc, css_text):
    for name, decls in _STYLE_RULE_RE.findall(css_text.replace("\n", " ")):
        name = name or "Default"
        style = doc.styles.get(name) or Style(name=name)
        for prop, value in re.findall(r"([\w-]+)\s*:\s*([^;]+)", decls):
            _apply_css(style, prop.strip(), value.strip(), doc)
        doc.styles[name] = style


def _apply_css(style, prop, value, doc):
    if prop == "font-family":
        style.font = value.strip("\"'")
    elif prop == "font-size":
        m = re.match(r"(\d+)", value)
        if m:
            style.size = int(m.group(1))
    elif prop == "color":
        style.primary_color = Color.from_css(value)
    elif prop == "font-weight" and value in ("bold", "700"):
        style.bold = True
    elif prop == "font-style" and value == "italic":
        style.italic = True
    elif prop == "text-decoration" and "underline" in value:
        style.underline = True
    elif prop == "background-color":
        style.back_color = Color.from_css(value)
    else:
        doc.add_loss("style", f"vtt-css-{prop}",
                     f"CSS property {prop!r} not mapped")


def _parse_settings(doc, settings, identifier):
    style_name = "Default"
    for token in settings.split():
        key, _, value = token.partition(":")
        if key == "line" or key == "position":
            doc.add_loss("cue", "vtt-positioning",
                         f"cue setting {key}={value} not preserved in SRT")
        elif key == "align":
            mapping = {"start": 1, "center": 2, "end": 3,
                       "left": 1, "right": 3}
            style = doc.default_style()
            style.alignment = mapping.get(value, 2)
        elif key == "vertical":
            doc.add_loss("cue", "vtt-vertical",
                         f"vertical={value} not supported by SRT/ASS map")
    m = re.match(r"([A-Za-z0-9_-]+)$", identifier)
    if m and identifier in doc.styles:
        style_name = identifier
    return style_name


def dump(doc):
    out = ["WEBVTT", ""]
    css = _dump_styles(doc)
    if css:
        out += ["STYLE", css, ""]
    for cue in doc.cues:
        if cue.identifier:
            out.append(cue.identifier)
        out.append(f"{format_vtt_time(cue.start)} --> {format_vtt_time(cue.end)}")
        out.append(markup.canonical_to_html(cue.text, color_syntax="vtt"))
        out.append("")
    return "\n".join(out) + "\n"


def _dump_styles(doc):
    rules = []
    for name, style in doc.styles.items():
        decls = [
            f"font-family: '{style.font}'",
            f"font-size: {style.size}px",
            f"color: {style.primary_color.hex()}",
        ]
        if style.bold:
            decls.append("font-weight: bold")
        if style.italic:
            decls.append("font-style: italic")
        if style.underline:
            decls.append("text-decoration: underline")
        if style.back_color:
            decls.append(
                f"background-color: {style.back_color.css_rgba()}")
        selector = "" if name == "Default" else f".{name}"
        rules.append(f"::cue({selector}) {{ {'; '.join(decls)} }}")
    return "\n".join(rules)
