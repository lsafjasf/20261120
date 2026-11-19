"""Incremental scanner for arbitrarily packetised Layer II input."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace

from .header import parse_header
from .parser import Frame, ScanResult, scan


class MP2Scanner:
    """Preserve byte order across arbitrary packet boundaries.

    A frame is emitted from ``feed()`` only after its boundary is confirmed.
    ``finish()`` emits the remaining final frame (which may legitimately have
    no following sync word).
    """

    def __init__(self) -> None:
        self._data = bytearray()
        self._all_data = bytearray()
        self._base_offset = 0
        self._frames: list[Frame] = []
        self._finished = False

    def feed(self, packet: bytes) -> list[Frame]:
        if self._finished:
            raise RuntimeError("cannot feed after finish()")
        self._data.extend(packet)
        self._all_data.extend(packet)
        return self._drain(final=False)

    def _confirmed(self, frame: Frame, snapshot: bytes) -> bool:
        end = frame.offset + frame.length
        return (
            end + 4 <= len(snapshot)
            and parse_header(snapshot, end) is not None
        )

    def _drain(self, final: bool) -> list[Frame]:
        snapshot = bytes(self._data)
        parsed = scan(snapshot)
        consumed = 0
        ready: list[Frame] = []

        for frame in parsed.frames:
            if not final and not self._confirmed(frame, snapshot):
                break
            header = replace(
                frame.header, offset=frame.offset + self._base_offset
            )
            adjusted = replace(
                frame,
                index=len(self._frames) + len(ready),
                offset=frame.offset + self._base_offset,
                header=header,
            )
            ready.append(adjusted)
            consumed = frame.offset + frame.length

        if final and not consumed:
            consumed = len(snapshot)
        if consumed:
            del self._data[:consumed]
            self._base_offset += consumed
        self._frames.extend(ready)
        return ready

    def finish(self) -> list[Frame]:
        if self._finished:
            return []
        self._finished = True
        return self._drain(final=True)

    def result(self) -> ScanResult:
        return scan(bytes(self._all_data))


def scan_packets(packets: Iterable[bytes]) -> list[Frame]:
    scanner = MP2Scanner()
    frames: list[Frame] = []
    for packet in packets:
        frames.extend(scanner.feed(packet))
    frames.extend(scanner.finish())
    return frames
