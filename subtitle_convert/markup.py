"""Inline markup translation.

Canonical tags: <b></b> <i></i> <u></u> <c #RRGGBB></c>
SRT/VTT input accepts <b>/<i>/<u>/<font color>/<c.class-or-color>.
ASS override blocks are translated where a mapping exists; anything else is
recorded as a loss and stripped from the text.
"""

import re

from .model import Color

_TAG_RE = re.compile(r"</?([a-zA-Z0-9]+)([^>]*)>")
_ASS_BLOCK_RE = re.compile(r"\{([^{}]*)\}")
_ASS_TAG_RE = re.compile(r"\\([1-4]?[a-zA-Z]+)([^\\]*)")


def html_to_canonical(text, loss_sink=None, cue_index=None, allow_color=True):
    # WebVTT uses <c.FFFFFF>...</c> class colors; SRT uses <font color>.
    text = re.sub(r"<c\.([0-9a-fA-F]{6})>",
                  lambda m: f'<c #{m.group(1).upper()}>', text)

    def repl(match):
        tag = match.group(1).lower()
        attrs = match.group(2)
        closing = match.group(0).startswith("</")
        if tag in ("b", "i", "u"):
            return f"</{tag}>" if closing else f"<{tag}>"
        if tag in ("c", "font"):
            if closing:
                return "</c>"
            color = _extract_color(attrs)
            if color is None:
                if loss_sink is not None:
                    loss_sink("cue", "inline-color",
                              f"unmappable <{tag}> attrs {attrs.strip()!r}",
                              cue_index)
                return ""
            if not allow_color:
                if loss_sink is not None:
                    loss_sink("cue", "inline-color",
                              "color dropped in target format", cue_index)
                return ""
            return f"<c {color.hex()}>"
        if loss_sink is not None:
            loss_sink("cue", "inline-tag",
                      f"unknown inline tag <{tag}> dropped", cue_index)
        return ""

    return _TAG_RE.sub(repl, text)


def _extract_color(attrs):
    m = re.fullmatch(r"\.([0-9a-fA-F]{6})", attrs.strip())
    if m:
        return Color.from_hex(m.group(1))
    m = re.search(r"color\s*=\s*[\"']?\s*(#[0-9a-fA-F]{3,6}|[0-9a-fA-F]{6})",
                  attrs)
    if not m:
        m = re.search(r"#([0-9a-fA-F]{6})", attrs)
        if not m:
            return None
        return Color.from_hex(m.group(1))
    raw = m.group(1)
    return Color.from_hex(raw.lstrip("#"))


def canonical_to_html(text, color_syntax="vtt"):
    def repl(match):
        inner = match.group(0)
        m = re.fullmatch(r"<c (#(?:[0-9a-fA-F]{6}))>", inner)
        if m:
            if color_syntax == "font":
                return f'<font color="{m.group(1)}">'
            return f'<c.{m.group(1).lstrip("#")}>'
        if inner == "</c>":
            return "</font>" if color_syntax == "font" else "</c>"
        return inner

    return _TAG_RE.sub(repl, text)


def ass_to_canonical(text, loss_sink=None, cue_index=None, style=None):
    state = {"b": False, "i": False, "u": False, "color": None}

    def emit_tag(name, on):
        if on and not state[name]:
            state[name] = True
            return f"<{name}>"
        if not on and state[name]:
            state[name] = False
            return f"</{name}>"
        return ""

    def block_repl(match):
        emitted = []
        body = match.group(1)
        for tag, value in _ASS_TAG_RE.findall("\\" + body.lstrip("\\")):
            name = tag.lower()
            value = value.strip()
            if name in ("b", "i", "u"):
                emitted.append(emit_tag(name, value not in ("0", "")))
            elif name in ("c", "1c"):
                if not value:
                    if state["color"]:
                        emitted.append("</c>")
                        state["color"] = None
                else:
                    try:
                        color = Color.from_ass(value)
                    except ValueError:
                        if loss_sink:
                            loss_sink("cue", "inline-color",
                                      f"bad ASS color {value!r}", cue_index)
                        continue
                    default_hex = (style.primary_color.hex()
                                   if style is not None else None)
                    previous = state["color"]
                    if previous:
                        emitted.append("</c>")
                    if previous and default_hex and color.hex() == default_hex:
                        state["color"] = None
                        continue
                    emitted.append(f"<c {color.hex()}>")
                    state["color"] = color
            elif name == "r":
                if state["color"]:
                    emitted.append("</c>")
                    state["color"] = None
                for toggle in ("u", "i", "b"):
                    emitted.append(emit_tag(toggle, False))
            elif name in ("pos", "move", "frz", "fr", "fn", "fs", "an",
                          "alpha", "bord", "shad", "fad", "t"):
                if loss_sink:
                    loss_sink("cue", f"ass-{name}",
                              f"ASS override \\{name}{value} has no "
                              "SRT/VTT mapping", cue_index)
            else:
                if loss_sink:
                    loss_sink("cue", "ass-unknown-tag",
                              f"unknown ASS override \\{name} dropped",
                              cue_index)
        return "".join(emitted)

    text = _ASS_BLOCK_RE.sub(block_repl, text)
    tail = []
    if state["color"]:
        tail.append("</c>")
    for toggle in ("u", "i", "b"):
        if state[toggle]:
            tail.append(f"</{toggle}>")
    text = text + "".join(tail)
    return (text.replace("\\h", "\u00a0")
                .replace("\\N", "\n")
                .replace("\\n", "\n"))


def canonical_to_ass(text, style):
    out = []
    stack = []
    pos = 0
    for m in _TAG_RE.finditer(text):
        out.append(_ass_escape(text[pos:m.start()]))
        token = m.group(0)
        if token in ("<b>", "<i>", "<u>"):
            tag = token[1]  # "b" | "i" | "u"
            out.append("{%s1}" % ("\\" + tag))
            stack.append(tag)
        elif token in ("</b>", "</i>", "</u>"):
            tag = token[2]
            out.append("{%s0}" % ("\\" + tag))
            stack = [t for t in stack if t != tag]
        elif token.startswith("<c "):
            color = Color.from_hex(token[4:-1])
            out.append("{%s}" % ("\\1c" + color.ass()))
            stack.append("c")
        elif token == "</c>":
            out.append(r"{\c}")
            stack = [t for t in stack if t != "c"]
        pos = m.end()
    out.append(_ass_escape(text[pos:]))
    rendered = "".join(out)
    return rendered.replace("\n", "\\N")


def _ass_escape(text):
    return text.replace("{", "(").replace("}", ")")
