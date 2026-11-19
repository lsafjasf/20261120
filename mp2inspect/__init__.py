"""Inspect MPEG-1 Audio Layer II framing and side information."""

from .header import Header, parse_header
from .parser import Frame, ScanResult, scan
from .scanner import MP2Scanner

__all__ = [
    "Frame",
    "Header",
    "MP2Scanner",
    "ScanResult",
    "parse_header",
    "scan",
]

