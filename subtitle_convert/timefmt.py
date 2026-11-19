"""Timestamp parsing/formatting. The canonical unit everywhere is integer ms."""

import re


class TimeFormatError(ValueError):
    pass


def _to_ms(hours, minutes, seconds, frac, frac_digits):
    frac_ms = int(round(frac * 1000 / (10 ** frac_digits)))
    return ((hours * 60 + minutes) * 60 + seconds) * 1000 + frac_ms


_SRT_RE = re.compile(r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})")
_VTT_RE = re.compile(r"(?:(\d{1,2}):)?(\d{1,2}):(\d{2})\.(\d{1,3})")
_ASS_RE = re.compile(r"(\d{1,2}):(\d{2}):(\d{2})\.(\d{1,2})")


def parse_srt_time(text):
    m = _SRT_RE.fullmatch(text.strip())
    if not m:
        raise TimeFormatError(f"bad SRT timestamp: {text!r}")
    h, mn, s, frac = m.groups()
    return _to_ms(int(h), int(mn), int(s), int(frac), len(frac))


def parse_vtt_time(text):
    m = _VTT_RE.fullmatch(text.strip())
    if not m:
        raise TimeFormatError(f"bad WebVTT timestamp: {text!r}")
    h, mn, s, frac = m.groups()
    return _to_ms(int(h or 0), int(mn), int(s), int(frac), len(frac))


def parse_ass_time(text):
    m = _ASS_RE.fullmatch(text.strip())
    if not m:
        raise TimeFormatError(f"bad ASS timestamp: {text!r}")
    h, mn, s, frac = m.groups()
    return _to_ms(int(h), int(mn), int(s), int(frac), len(frac))


def parse_any(text):
    for parser in (parse_srt_time, parse_vtt_time, parse_ass_time):
        try:
            return parser(text)
        except TimeFormatError:
            continue
    raise TimeFormatError(f"unrecognized timestamp: {text!r}")


def format_srt_time(ms):
    h, rem = divmod(int(ms), 3_600_000)
    mn, rem = divmod(rem, 60_000)
    s, milli = divmod(rem, 1000)
    return f"{h:02d}:{mn:02d}:{s:02d},{milli:03d}"


def format_vtt_time(ms):
    h, rem = divmod(int(ms), 3_600_000)
    mn, rem = divmod(rem, 60_000)
    s, milli = divmod(rem, 1000)
    return f"{h:02d}:{mn:02d}:{s:02d}.{milli:03d}"


def format_ass_time(ms):
    # ASS resolution is centiseconds (10 ms); round-half-to-even is avoided,
    # use deterministic round-half-up so quantization is bounded by +/- 5 ms.
    cs = (int(ms) + 5) // 10
    total_seconds, cs = divmod(cs, 100)
    h, rem = divmod(total_seconds, 3600)
    mn, s = divmod(rem, 60)
    return f"{h:d}:{mn:02d}:{s:02d}.{cs:02d}"


def ms_to_centiseconds(ms):
    return (int(ms) + 5) // 10


def centiseconds_to_ms(cs):
    return int(cs) * 10
