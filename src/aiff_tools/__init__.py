"""AIFF/AIFC chunk inspection and traceability helpers."""

from .chunks import AiffFile, parse_aiff
from .extended80 import decode_extended80, encode_extended80

__all__ = [
    "AiffFile",
    "decode_extended80",
    "encode_extended80",
    "parse_aiff",
]
