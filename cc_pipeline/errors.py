"""Collects parse errors with absolute byte positions in the source stream."""

from dataclasses import dataclass, field


@dataclass
class ParseError:
    offset: int          # absolute byte offset in the stream file
    frame: int           # frame (picture) index where the error occurred
    layer: str           # e.g. "mpeg2", "cea608", "cea708"
    message: str

    def __str__(self):
        return (f"[{self.layer}] byte=0x{self.offset:08X} "
                f"frame={self.frame} {self.message}")


@dataclass
class ErrorLog:
    errors: list = field(default_factory=list)

    def add(self, offset, frame, layer, message):
        self.errors.append(ParseError(offset, frame, layer, message))

    def __len__(self):
        return len(self.errors)

    def report_lines(self):
        return [str(e) for e in sorted(self.errors, key=lambda e: e.offset)]
