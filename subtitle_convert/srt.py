"""SubRip (SRT) codec. SRT has no style table: one implicit Default style."""

import re

from . import markup
from .model import Document, Cue, Style
from .timefmt import parse_srt_time, format_srt_time

_TIMING_RE = re.compile(
    r"(\d{1,2}:\d{2}:\d{2}[,.]\d{1,3})\s*-->\s*"
    r"(\d{1,2}:\d{2}:\d{2}[,.]\d{1,3})(.*)"
)


def parse(text):
    doc = Document()
    doc.styles["Default"] = Style()
    blocks = re.split(r"\r?\n\r?\n", text.replace("\r\n", "\n").strip())
    for block in blocks:
        lines = [ln for ln in block.split("\n") if ln.strip() != ""]
        if not lines:
            continue
        timing_idx = next(
            (i for i, ln in enumerate(lines) if _TIMING_RE.search(ln)), None)
        if timing_idx is None:
            doc.add_loss("document", "unparsed-block",
                         f"block skipped: {lines[0]!r}")
            continue
        identifier = ""
        if timing_idx == 1 and not lines[0].strip().isdigit():
            identifier = lines[0].strip()
        m = _TIMING_RE.search(lines[timing_idx])
        start, end, settings = m.groups()
        if settings.strip():
            doc.add_loss("cue", "srt-positioning",
                         f"position settings dropped: {settings.strip()!r}")
        body = "\n".join(lines[timing_idx + 1:])
        body = markup.html_to_canonical(
            body, loss_sink=doc.add_loss, allow_color=True)
        doc.cues.append(Cue(parse_srt_time(start), parse_srt_time(end),
                            body, identifier=identifier))
    return doc


def dump(doc):
    lines = []
    for idx, cue in enumerate(doc.cues, 1):
        lines.append(str(idx))
        lines.append(f"{format_srt_time(cue.start)} --> "
                     f"{format_srt_time(cue.end)}")
        lines.append(markup.canonical_to_html(cue.text, color_syntax="font"))
        lines.append("")
    return "\n".join(lines) + "\n"
