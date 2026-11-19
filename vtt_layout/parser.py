"""WebVTT parser: file blocks, REGION, STYLE, cue settings, inline tags."""

from __future__ import annotations

import re
from dataclasses import dataclass, field


TIMING_RE = re.compile(
    r"^(?P<start>(?:\d{1,2}:)?\d{1,2}:\d{2}\.\d{1,3})\s*-->\s*"
    r"(?P<end>(?:\d{1,2}:)?\d{1,2}:\d{2}\.\d{1,3})(?P<settings>.*)$"
)
TS_RE = re.compile(r"<((?:\d{1,2}:)?\d{1,2}:\d{2}\.\d{1,3})>")
TAG_RE = re.compile(r"<([0-9:.]+|[^>]+)>")


def parse_timestamp(value: str) -> float:
    """Parse a WebVTT timestamp into seconds."""
    parts = value.strip().split(":")
    if len(parts) == 2:
        hours, minutes, seconds = 0, int(parts[0]), float(parts[1])
    else:
        hours, minutes, seconds = int(parts[0]), int(parts[1]), float(parts[2])
    return hours * 3600 + minutes * 60 + seconds


@dataclass
class Region:
    id: str
    width_pct: float = 100.0
    lines: int = 3
    anchor_x: float = 0.0
    anchor_y: float = 100.0
    viewport_x: float = 0.0
    viewport_y: float = 100.0
    scroll: str | None = None  # None | "up"


@dataclass
class StyleRule:
    selector: str
    declarations: dict[str, str]
    raw: str


@dataclass
class Span:
    text: str
    classes: tuple[str, ...] = ()
    bold: bool = False
    italic: bool = False
    underline: bool = False
    ruby: bool = False


@dataclass
class CueLine:
    spans: list[Span]
    timestamps: list[tuple[int, float]]  # (char offset in joined text, time s)

    @property
    def text(self) -> str:
        return "".join(span.text for span in self.spans)


@dataclass
class Cue:
    id: str
    start: float
    end: float
    settings: dict[str, str]
    lines: list[CueLine]
    region_id: str | None = None


@dataclass
class WebVttDocument:
    regions: dict[str, Region] = field(default_factory=dict)
    styles: list[StyleRule] = field(default_factory=list)
    cues: list[Cue] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    declared_fps: float | None = None


def _parse_region(header: str, body_lines: list[str]) -> Region:
    region_id = ""
    values: dict[str, str] = {}

    def consume(token: str) -> None:
        nonlocal region_id
        for sep in ("=", ":"):
            if sep in token:
                key, raw = token.split(sep, 1)
                break
        else:
            return
        if key == "id":
            region_id = raw
        else:
            values[key] = raw

    for token in header.split()[1:]:
        consume(token)
    for line in body_lines:
        for token in line.split():
            consume(token)
    return Region(
        id=region_id,
        width_pct=float(values.get("width", "100%").rstrip("%")),
        lines=int(values.get("lines", "3")),
        anchor_x=float(values.get("regionanchor", "0%,100%").split(",")[0].rstrip("%")),
        anchor_y=float(values.get("regionanchor", "0%,100%").split(",")[1].rstrip("%")),
        viewport_x=float(values.get("viewportanchor", "0%,100%").split(",")[0].rstrip("%")),
        viewport_y=float(values.get("viewportanchor", "0%,100%").split(",")[1].rstrip("%")),
        scroll=values.get("scroll"),
    )


def _parse_style(body_lines: list[str]) -> list[StyleRule]:
    rules: list[StyleRule] = []
    raw = "\n".join(body_lines)
    for match in re.finditer(r"([^{}]+)\{([^{}]*)\}", raw):
        selector = match.group(1).strip()
        declarations: dict[str, str] = {}
        for decl in match.group(2).split(";"):
            if ":" in decl:
                prop, value = decl.split(":", 1)
                declarations[prop.strip().lower()] = value.strip()
        rules.append(StyleRule(selector=selector, declarations=declarations,
                               raw=match.group(0).strip()))
    return rules


def _parse_inline(line: str) -> CueLine:
    """Parse cue payload line: timestamp tags and nested text tags -> spans."""
    spans: list[Span] = []
    timestamps: list[tuple[int, float]] = []
    classes: list[str] = []
    bold = italic = underline = ruby = False
    buf: list[str] = []
    text_offset = 0

    def flush() -> None:
        nonlocal text_offset
        if buf:
            content = "".join(buf)
            spans.append(Span(content, tuple(classes), bold, italic,
                              underline, ruby))
            text_offset += len(content)
            buf.clear()

    for tag in re.split(r"(<[^>]+>)", line):
        if not tag:
            continue
        if not tag.startswith("<"):
            buf.append(tag)
            continue
        inner = tag[1:-1]
        if TS_RE.fullmatch(tag):
            flush()
            timestamps.append((text_offset, parse_timestamp(inner)))
        elif inner.startswith("/"):
            flush()
            name = inner[1:].split(".")[0]
            if name == "b":
                bold = False
            elif name == "i":
                italic = False
            elif name == "u":
                underline = False
            elif name == "ruby":
                ruby = False
            elif name == "c":
                classes = []
        elif inner in ("b", "i", "u", "ruby") or inner.startswith(("b.", "i.", "u.", "ruby.")):
            flush()
            name = inner.split(".")[0]
            classes = [part for part in inner.split(".")[1:] if part]
            bold = name == "b"
            italic = name == "i"
            underline = name == "u"
            ruby = name == "ruby"
        elif inner == "c" or inner.startswith("c."):
            flush()
            classes = [part for part in inner.split(".")[1:] if part]
        elif inner.startswith("v ") or inner.startswith("v."):
            flush()
        # lang tags (lang:xx) and unknown tags: keep inner text styling context
    flush()
    return CueLine(spans=spans, timestamps=timestamps)


def parse_cue_settings(raw: str) -> dict[str, str]:
    settings: dict[str, str] = {}
    for token in raw.strip().split():
        if ":" in token:
            key, value = token.split(":", 1)
            settings[key.lower()] = value
    return settings


def parse_webvtt(text: str) -> WebVttDocument:
    """Parse a complete WebVTT document (blocks separated by blank lines)."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    blocks = re.split(r"\n[ \t]*\n", text.strip("\n"))
    doc = WebVttDocument()
    if not blocks or not blocks[0].lstrip().startswith("WEBVTT"):
        raise ValueError("missing WEBVTT file header")

    for block in blocks[1:]:
        lines = [line for line in block.split("\n") if line.strip() != ""]
        if not lines:
            continue
        first = lines[0].strip()
        if first.startswith("NOTE"):
            doc.notes.append("\n".join(lines))
            continue
        if first.startswith("REGION"):
            region = _parse_region(first, lines[1:])
            doc.regions[region.id] = region
            continue
        if first.startswith("STYLE"):
            doc.styles.extend(_parse_style(lines[1:]))
            continue

        timing_idx = next((idx for idx, line in enumerate(lines)
                           if TIMING_RE.match(line.strip())), None)
        if timing_idx is None:
            continue
        cue_id = "" if timing_idx == 0 else " ".join(lines[:timing_idx]).strip()
        match = TIMING_RE.match(lines[timing_idx].strip())
        assert match is not None
        settings = parse_cue_settings(match.group("settings"))
        cue = Cue(
            id=cue_id,
            start=parse_timestamp(match.group("start")),
            end=parse_timestamp(match.group("end")),
            settings=settings,
            lines=[_parse_inline(payload) for payload in lines[timing_idx + 1:]],
            region_id=settings.get("region"),
        )
        doc.cues.append(cue)
    doc.cues.sort(key=lambda cue: (cue.start, cue.end, cue.id))
    return doc
