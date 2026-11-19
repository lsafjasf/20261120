"""Ogg granule-position and playback-timeline analysis.

Public API:

    from ogg_timeline import analyze_file, analyze_bytes, parse_pages

The parser is self-contained and does not require libogg, libvorbis, or
libopus.  Codec packet durations are derived from the Vorbis setup header and
the Opus TOC byte respectively.
"""

from .codecs import CodecInfo, OpusInfo, VorbisInfo
from .ogg import OggFile, OggPage, Packet, parse_pages
from .predictor import PredictorConfig, predict_stall
from .timeline import StreamAnalysis, analyze_bytes, analyze_file, analyze_pages

__all__ = [
    "CodecInfo",
    "OggFile",
    "OggPage",
    "OpusInfo",
    "Packet",
    "PredictorConfig",
    "StreamAnalysis",
    "VorbisInfo",
    "analyze_bytes",
    "analyze_file",
    "analyze_pages",
    "parse_pages",
    "predict_stall",
]
