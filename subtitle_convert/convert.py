"""Format-agnostic conversion facade."""

from . import srt, vtt, ass

CODECS = {
    "srt": srt,
    "vtt": vtt,
    "ass": ass,
}


def detect_format(text, filename=None):
    head = text.lstrip("\ufeff")[:4096]
    if head.startswith("WEBVTT"):
        return "vtt"
    if "[Script Info]" in head or "Dialogue:" in head:
        return "ass"
    if "-->" in head:
        return "srt"
    if filename:
        ext = filename.lower().rsplit(".", 1)[-1]
        if ext in CODECS:
            return ext
    raise ValueError("cannot detect subtitle format")


def parse(text, fmt=None, filename=None):
    fmt = fmt or detect_format(text, filename)
    return CODECS[fmt].parse(text)


def dump(doc, fmt):
    return CODECS[fmt].dump(doc)


def convert(text, src_fmt, dst_fmt):
    """Parse, serialize to dst, and re-parse so the result's own losses are
    visible. Returns (dst_text, canonical_doc, re_parsed_doc)."""
    doc = parse(text, src_fmt)
    dst_text = dump(doc, dst_fmt)
    reparsed = parse(dst_text, dst_fmt)
    return dst_text, doc, reparsed
