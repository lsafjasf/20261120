"""Frame scanning with per-frame resynchronisation and free-format support."""

from __future__ import annotations

from dataclasses import dataclass, field

from .header import Header, infer_free_bitrate, parse_header
from .sideinfo import SideInfo, SideInfoError, parse_side_info

MIN_FREE_FRAME_LENGTH = 16
MAX_FREE_FRAME_LENGTH = 65536


@dataclass(frozen=True, slots=True)
class Frame:
    index: int
    offset: int
    length: int
    header: Header
    side_info: SideInfo | None
    side_info_error: str | None
    boundary_ok: bool
    boundary_detail: str | None
    inferred_bitrate_kbps: float | None

    @property
    def crc_ok(self) -> bool | None:
        return self.side_info.crc_ok if self.side_info else None

    @property
    def free_format(self) -> bool:
        return self.header.free_format

    def to_dict(self) -> dict:
        result = self.header.to_dict()
        result.update(
            {
                "index": self.index,
                "offset": self.offset,
                "length": self.length,
                "side_info": self.side_info.to_dict() if self.side_info else None,
                "side_info_error": self.side_info_error,
                "boundary_ok": self.boundary_ok,
                "boundary_detail": self.boundary_detail,
                "inferred_bitrate_kbps": self.inferred_bitrate_kbps,
                "crc_ok": self.crc_ok,
                "free_format": self.free_format,
            }
        )
        return result


@dataclass(frozen=True, slots=True)
class SkippedRegion:
    offset: int
    length: int


@dataclass(slots=True)
class ScanResult:
    frames: list[Frame] = field(default_factory=list)
    skipped: list[SkippedRegion] = field(default_factory=list)
    trailing_bytes: int = 0
    source_bytes: int = 0

    @property
    def total_frames(self) -> int:
        return len(self.frames)

    @property
    def crc_error_frames(self) -> list[Frame]:
        return [frame for frame in self.frames if frame.crc_ok is False]

    @property
    def free_format_frames(self) -> list[Frame]:
        return [frame for frame in self.frames if frame.free_format]

    @property
    def boundary_conflicts(self) -> list[Frame]:
        return [frame for frame in self.frames if not frame.boundary_ok]

    @property
    def audio_seconds(self) -> float:
        return sum(1152 / frame.header.sample_rate for frame in self.frames)

    def summary(self) -> dict:
        return {
            "source_bytes": self.source_bytes,
            "total_frames": self.total_frames,
            "audio_seconds": self.audio_seconds,
            "crc_error_frames": [f.index for f in self.crc_error_frames],
            "crc_error_offsets": [f.offset for f in self.crc_error_frames],
            "free_format_frames": [f.index for f in self.free_format_frames],
            "free_format_offsets": [f.offset for f in self.free_format_frames],
            "boundary_conflict_frames": [
                f.index for f in self.boundary_conflicts
            ],
            "skipped_regions": [
                {"offset": region.offset, "length": region.length}
                for region in self.skipped
            ],
            "trailing_bytes": self.trailing_bytes,
        }

    def to_dict(self, include_frames: bool = True) -> dict:
        result = {"summary": self.summary()}
        if include_frames:
            result["frames"] = [frame.to_dict() for frame in self.frames]
        return result


def _next_header(data: bytes, start: int) -> Header | None:
    search = max(0, start)
    while True:
        offset = data.find(b"\xff", search)
        if offset < 0 or offset + 4 > len(data):
            return None
        header = parse_header(data, offset)
        if header is not None:
            return header
        search = offset + 1


def _known_boundary(data: bytes, header: Header) -> bool:
    if header.frame_length is None:
        return False
    end = header.offset + header.frame_length
    if end == len(data):
        return True
    if end + 4 > len(data):
        return False
    if parse_header(data, end) is not None:
        return True
    following = _next_header(data, end + 1)
    if following is None:
        return True
    return following.frame_length is not None and (
        following.offset + following.frame_length == len(data)
        or parse_header(
            data, following.offset + (following.frame_length or 0)
        )
        is not None
    )


def _free_frame_end(
    data: bytes,
    offset: int,
    previous_length: int | None,
) -> tuple[int, str]:
    if previous_length is not None:
        for candidate_length in (
            previous_length - 1,
            previous_length,
            previous_length + 1,
        ):
            end = offset + candidate_length
            if end > len(data):
                continue
            if end == len(data) or parse_header(data, end) is not None:
                return end, "previous free-format length"

    best_fallback: int | None = None
    search = offset + MIN_FREE_FRAME_LENGTH
    limit = min(len(data), offset + MAX_FREE_FRAME_LENGTH)
    while search + 4 <= limit:
        candidate = parse_header(data, search)
        if candidate is None:
            search = data.find(b"\xff", search + 1)
            if search < 0:
                break
            continue
        gap = search - offset
        if candidate.free_format:
            for lookahead in (gap - 1, gap, gap + 1):
                if lookahead <= 0:
                    continue
                header = parse_header(data, search + lookahead)
                if header is not None:
                    return search, "subsequent free-format sync"
        elif _known_boundary(data, candidate):
            return search, "subsequent regular sync"
        elif best_fallback is None:
            best_fallback = search
        search = data.find(b"\xff", search + 1)
        if search < 0:
            break

    if previous_length is not None and offset + previous_length <= len(data):
        return offset + previous_length, "previous free-format length (fallback)"
    if best_fallback is not None:
        return best_fallback, "unconfirmed free-format sync"
    return len(data), "end of stream"


def _id3v2_size(data: bytes, offset: int) -> int:
    if offset + 10 > len(data) or data[offset : offset + 3] != b"ID3":
        return 0
    tag_size = 0
    for byte in data[offset + 6 : offset + 10]:
        if byte & 0x80:
            return 0
        tag_size = (tag_size << 7) | byte
    footer = 10 if data[offset + 5] & 0x10 else 0
    return min(len(data) - offset, 10 + tag_size + footer)


def scan(data: bytes) -> ScanResult:
    """Scan an MPEG-1 Layer II byte stream without trusting its container."""
    result = ScanResult(source_bytes=len(data))
    offset = 0
    previous_free_length: int | None = None

    while offset + 4 <= len(data):
        tag_size = _id3v2_size(data, offset)
        if tag_size:
            result.skipped.append(SkippedRegion(offset, tag_size))
            offset += tag_size
            continue

        header = parse_header(data, offset)
        if header is None:
            following = _next_header(data, offset + 1)
            if following is None:
                break
            result.skipped.append(
                SkippedRegion(offset, following.offset - offset)
            )
            offset = following.offset
            continue

        if header.free_format:
            frame_end, boundary_detail = _free_frame_end(
                data, offset, previous_free_length
            )
            length = frame_end - offset
            if length < MIN_FREE_FRAME_LENGTH:
                following = _next_header(data, offset + 1)
                if following is None:
                    break
                result.skipped.append(
                    SkippedRegion(offset, following.offset - offset)
                )
                offset = following.offset
                continue
            inferred = infer_free_bitrate(
                length, header.sample_rate, header.padding
            )
            previous_free_length = length
        else:
            assert header.frame_length is not None
            length = header.frame_length
            frame_end = offset + length
            inferred = None
            previous_free_length = None
            if frame_end > len(data):
                frame_end = len(data)
                length = frame_end - offset
                boundary_detail = "truncated frame"
            elif frame_end == len(data):
                boundary_detail = "end of stream"
            elif parse_header(data, frame_end) is not None:
                boundary_detail = "next header at computed boundary"
            else:
                boundary_detail = "no valid header at computed boundary"

        side_info: SideInfo | None = None
        side_error: str | None = None
        try:
            side_info = parse_side_info(
                data, header, frame_end, inferred_bitrate_kbps=inferred
            )
        except SideInfoError as exc:
            side_error = str(exc)

        next_header_ok = (
            frame_end == len(data)
            or (
                frame_end + 4 <= len(data)
                and parse_header(data, frame_end) is not None
            )
        )
        boundary_ok = next_header_ok and side_error is None
        result.frames.append(
            Frame(
                index=len(result.frames),
                offset=offset,
                length=length,
                header=header,
                side_info=side_info,
                side_info_error=side_error,
                boundary_ok=boundary_ok,
                boundary_detail=boundary_detail,
                inferred_bitrate_kbps=inferred,
            )
        )
        offset = frame_end

    if offset < len(data):
        result.trailing_bytes = len(data) - offset
        result.skipped.append(SkippedRegion(offset, result.trailing_bytes))
    return result
