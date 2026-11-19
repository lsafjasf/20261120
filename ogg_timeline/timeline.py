"""Per-page sample positions, time mappings, and discontinuity detection."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .codecs import CodecInfo, OpusInfo, VorbisInfo, analyze_codec
from .ogg import NO_GRANULE, LogicalStream, OggFile, OggPage, Packet, parse_pages


@dataclass
class PageTimeline:
    page_index: int
    byte_offset: int
    byte_length: int
    serial: int
    sequence: int
    kind: str
    granule_position: int | None
    previous_granule_position: int | None
    next_granule_position: int | None
    sample_start: int | None
    sample_end: int | None
    time_start_s: float | None
    time_end_s: float | None
    pcm_sample_start: int | None
    pcm_sample_end: int | None
    pcm_time_start_s: float | None
    pcm_time_end_s: float | None
    completed_packets: int
    completed_audio_packets: int
    completed_audio_samples: int
    continued_packet: bool
    eos: bool
    crc_ok: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Discontinuity:
    kind: str
    severity: str
    page_index: int
    byte_offset: int
    sequence: int
    previous_page_index: int | None
    previous_granule_position: int | None
    granule_position: int | None
    next_page_index: int | None
    next_granule_position: int | None
    expected_delta_samples: int | None = None
    actual_delta_samples: int | None = None
    signed_error_samples: int | None = None
    previous_time_s: float | None = None
    time_s: float | None = None
    next_time_s: float | None = None
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class MappingCheck:
    finite_granules: int
    non_decreasing_granules: bool
    no_backward_jump: bool
    no_granule_regression: bool
    mapping_is_affine: bool
    mapping_continuous: bool
    timeline_continuous: bool
    end_trim_pages: list[int] = field(default_factory=list)
    end_trim_samples: int = 0
    max_backward_jump_samples: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class StreamAnalysis:
    serial: int
    codec: str | None
    codec_info: CodecInfo | None
    pages: list[PageTimeline]
    discontinuities: list[Discontinuity]
    mapping: MappingCheck
    warnings: list[str]
    packet_count: int
    audio_packet_count: int

    @property
    def audio_pages(self) -> list[PageTimeline]:
        return [page for page in self.pages if page.kind == "audio"]

    def to_dict(self) -> dict[str, Any]:
        info = asdict(self.codec_info) if self.codec_info else None
        return {
            "serial": self.serial,
            "codec": self.codec,
            "codec_info": info,
            "pages": [page.to_dict() for page in self.pages],
            "discontinuities": [
                item.to_dict() for item in self.discontinuities
            ],
            "mapping": self.mapping.to_dict(),
            "warnings": list(self.warnings),
            "packet_count": self.packet_count,
            "audio_packet_count": self.audio_packet_count,
        }


def _sample_clock(codec_info: CodecInfo | None) -> tuple[int, int]:
    """Return ``(encoder_clock, pcm_pre_skip)`` for a codec."""

    if isinstance(codec_info, OpusInfo):
        return 48_000, codec_info.pre_skip
    if isinstance(codec_info, VorbisInfo):
        return codec_info.sample_rate, 0
    return 48_000, 0


def _audio_packet_indices(stream: LogicalStream, codec: CodecInfo | None) -> set[int]:
    if codec is None:
        return set()
    return {
        packet.index
        for packet in stream.packets
        if not packet._header and packet.index >= codec.header_packet_count
    }


def _first_audio_page(
    stream: LogicalStream, codec: CodecInfo | None
) -> int | None:
    if codec is None:
        return None
    for packet in stream.packets:
        if packet.index >= codec.header_packet_count:
            return packet.start_page
    return None


def _packet_duration(packet: Packet) -> int:
    return packet._duration_samples or 0


def _sum_completed_audio_samples(
    stream: LogicalStream, audio_indices: set[int], page: OggPage
) -> int:
    total = 0
    for packet_index in page.completed_packet_indices:
        if packet_index in audio_indices:
            total += _packet_duration(stream.packets[packet_index])
    return total


def _sum_samples_between(
    stream: LogicalStream,
    audio_indices: set[int],
    previous_page_index: int,
    page_index: int,
) -> int:
    total = 0
    for packet_index in audio_indices:
        packet = stream.packets[packet_index]
        if previous_page_index < packet.end_page <= page_index:
            total += _packet_duration(packet)
    return total


def _last_completed_audio_duration(
    stream: LogicalStream, audio_indices: set[int], page: OggPage
) -> int:
    for packet_index in reversed(page.completed_packet_indices):
        if packet_index in audio_indices:
            return _packet_duration(stream.packets[packet_index])
    return 0


def _page_kind(
    page: OggPage, first_audio_page: int | None, codec: CodecInfo | None
) -> str:
    if codec is None:
        return "unknown"
    if first_audio_page is None:
        return "header"
    return "audio" if page.index >= first_audio_page else "header"


def _build_page_timeline(
    stream: LogicalStream,
    codec: CodecInfo | None,
    first_audio_page: int | None,
    audio_indices: set[int],
) -> list[PageTimeline]:
    clock, pre_skip = _sample_clock(codec)
    rows: list[PageTimeline] = []
    previous_audio_gp: int | None = None

    for page in stream.pages:
        kind = _page_kind(page, first_audio_page, codec)
        completed_audio = sum(
            1 for index in page.completed_packet_indices if index in audio_indices
        )
        completed_samples = _sum_completed_audio_samples(
            stream, audio_indices, page
        )
        granule = page.granule_position if page.has_granule else None
        sample_start: int | None = None
        sample_end: int | None = None

        if kind == "audio" and granule is not None:
            if previous_audio_gp is None:
                sample_start = granule - completed_samples
            else:
                sample_start = previous_audio_gp
            sample_end = granule
            previous_audio_gp = granule
        pcm_start = (
            sample_start - pre_skip if sample_start is not None else None
        )
        pcm_end = sample_end - pre_skip if sample_end is not None else None
        rows.append(
            PageTimeline(
                page_index=page.index,
                byte_offset=page.offset,
                byte_length=page.size,
                serial=page.serial,
                sequence=page.sequence,
                kind=kind,
                granule_position=granule,
                previous_granule_position=None,
                next_granule_position=None,
                sample_start=sample_start,
                sample_end=sample_end,
                time_start_s=(
                    sample_start / clock if sample_start is not None else None
                ),
                time_end_s=(
                    sample_end / clock if sample_end is not None else None
                ),
                pcm_sample_start=pcm_start,
                pcm_sample_end=pcm_end,
                pcm_time_start_s=(
                    pcm_start / clock if pcm_start is not None else None
                ),
                pcm_time_end_s=(
                    pcm_end / clock if pcm_end is not None else None
                ),
                completed_packets=len(page.completed_packet_indices),
                completed_audio_packets=completed_audio,
                completed_audio_samples=completed_samples,
                continued_packet=page.continued,
                eos=page.eos,
                crc_ok=page.crc_ok,
            )
        )

    finite = [row for row in rows if row.granule_position is not None]
    for position, row in enumerate(finite):
        row.previous_granule_position = (
            finite[position - 1].granule_position if position else None
        )
        row.next_granule_position = (
            finite[position + 1].granule_position
            if position + 1 < len(finite)
            else None
        )
    return rows


def _discontinuity(
    kind: str,
    severity: str,
    page: OggPage,
    *,
    expected: int | None = None,
    actual: int | None = None,
    detail: str = "",
) -> Discontinuity:
    return Discontinuity(
        kind=kind,
        severity=severity,
        page_index=page.index,
        byte_offset=page.offset,
        sequence=page.sequence,
        previous_page_index=None,
        previous_granule_position=None,
        granule_position=(
            page.granule_position if page.has_granule else None
        ),
        next_page_index=None,
        next_granule_position=None,
        expected_delta_samples=expected,
        actual_delta_samples=actual,
        signed_error_samples=(
            actual - expected if actual is not None and expected is not None else None
        ),
        detail=detail,
    )


def _scan_discontinuities(
    stream: LogicalStream,
    pages: list[PageTimeline],
    audio_indices: set[int],
    codec: CodecInfo | None,
) -> tuple[list[Discontinuity], list[int], int]:
    findings: list[Discontinuity] = []
    end_trim_pages: list[int] = []
    end_trim_samples = 0
    previous_sequence: int | None = None
    previous_finite_page: OggPage | None = None

    timeline_by_index = {row.page_index: row for row in pages}
    for page in stream.pages:
        row = timeline_by_index[page.index]
        if page.version != 0:
            findings.append(
                _discontinuity(
                    "invalid_ogg_version",
                    "error",
                    page,
                    detail=f"unsupported Ogg version {page.version}",
                )
            )
        if not page.crc_ok:
            findings.append(
                _discontinuity(
                    "crc_mismatch",
                    "error",
                    page,
                    detail=(
                        f"stored CRC 0x{page.stored_crc:08x}, "
                        f"computed 0x{page.computed_crc:08x}"
                    ),
                )
            )
        if previous_sequence is not None:
            expected_sequence = (previous_sequence + 1) & 0xFFFFFFFF
            if page.sequence != expected_sequence:
                findings.append(
                    _discontinuity(
                        "page_sequence_gap",
                        "warning",
                        page,
                        detail=(
                            f"expected sequence {expected_sequence}, "
                            f"found {page.sequence}"
                        ),
                    )
                )
        previous_sequence = page.sequence

        if row.kind != "audio":
            continue
        completed_audio = row.completed_audio_packets
        if completed_audio and not page.has_granule:
            findings.append(
                _discontinuity(
                    "missing_granule_with_completed_packets",
                    "error",
                    page,
                    detail=f"{completed_audio} audio packet(s) completed",
                )
            )
        if page.has_granule and page.completed_packet_count == 0:
            findings.append(
                _discontinuity(
                    "granule_without_completed_packet",
                    "error",
                    page,
                    detail="a finite granule requires a completed packet",
                )
            )
        if not page.has_granule:
            continue

        if previous_finite_page is None:
            if isinstance(codec, OpusInfo) and page.granule_position < row.completed_audio_samples and not page.eos:
                findings.append(
                    _discontinuity(
                        "opus_initial_granule_too_small",
                        "error",
                        page,
                        expected=row.completed_audio_samples,
                        actual=page.granule_position,
                        detail=(
                            "the first Opus granule may be larger than the "
                            "completed packet duration, but not smaller"
                        ),
                    )
                )
            previous_finite_page = page
            continue

        expected_delta = _sum_samples_between(
            stream,
            audio_indices,
            previous_finite_page.index,
            page.index,
        )
        actual_delta = (
            page.granule_position - previous_finite_page.granule_position
        )
        allowed_eos_trim = 0
        if page.eos and 0 <= expected_delta - actual_delta:
            last_duration = _last_completed_audio_duration(
                stream, audio_indices, page
            )
            trim = expected_delta - actual_delta
            if trim <= last_duration:
                allowed_eos_trim = trim
        if actual_delta != expected_delta and allowed_eos_trim == 0:
            kind = (
                "timeline_granule_backward"
                if actual_delta < 0
                else "timeline_granule_gap"
            )
            findings.append(
                _discontinuity(
                    kind,
                    "error",
                    page,
                    expected=expected_delta,
                    actual=actual_delta,
                    detail=(
                        "granule delta does not equal the completed packet "
                        "durations"
                    ),
                )
            )
        elif allowed_eos_trim:
            end_trim_pages.append(page.index)
            end_trim_samples += allowed_eos_trim
        previous_finite_page = page

    return findings, end_trim_pages, end_trim_samples


def _attach_neighbors(
    findings: list[Discontinuity], pages: list[PageTimeline], clock: int, pre_skip: int
) -> None:
    finite = [row for row in pages if row.granule_position is not None]
    for finding in findings:
        previous = None
        following = None
        for row in finite:
            if row.page_index < finding.page_index:
                previous = row
            elif row.page_index > finding.page_index:
                following = row
                break
        if previous:
            finding.previous_page_index = previous.page_index
            finding.previous_granule_position = previous.granule_position
            finding.previous_time_s = (
                (previous.granule_position - pre_skip) / clock
            )
        if following:
            finding.next_page_index = following.page_index
            finding.next_granule_position = following.granule_position
            finding.next_time_s = (following.granule_position - pre_skip) / clock
        if finding.granule_position is not None:
            finding.time_s = (finding.granule_position - pre_skip) / clock


def _mapping_check(
    pages: list[PageTimeline],
    findings: list[Discontinuity],
    end_trim_pages: list[int],
    end_trim_samples: int,
) -> MappingCheck:
    finite = [
        row for row in pages
        if row.kind == "audio" and row.granule_position is not None
    ]
    granules = [int(row.granule_position) for row in finite]
    deltas = [
        granules[index] - granules[index - 1]
        for index in range(1, len(granules))
    ]
    max_backward = abs(min([0, *deltas]))
    timeline_errors = [
        item for item in findings if item.kind.startswith("timeline_")
    ]
    no_backward = all(delta >= 0 for delta in deltas)
    return MappingCheck(
        finite_granules=len(granules),
        non_decreasing_granules=no_backward,
        no_backward_jump=no_backward,
        no_granule_regression=max_backward == 0,
        mapping_is_affine=True,
        mapping_continuous=no_backward and max_backward == 0,
        timeline_continuous=not timeline_errors and no_backward,
        end_trim_pages=end_trim_pages,
        end_trim_samples=end_trim_samples,
        max_backward_jump_samples=max_backward,
    )


def analyze_pages(ogg: OggFile) -> list[StreamAnalysis]:
    """Analyze every logical stream in an already parsed Ogg file."""

    results: list[StreamAnalysis] = []
    for serial, stream in ogg.streams.items():
        codec = analyze_codec(stream)
        audio_indices = _audio_packet_indices(stream, codec)
        first_audio_page = _first_audio_page(stream, codec)
        pages = _build_page_timeline(
            stream, codec, first_audio_page, audio_indices
        )
        findings, trim_pages, trim_samples = _scan_discontinuities(
            stream, pages, audio_indices, codec
        )
        clock, pre_skip = _sample_clock(codec)
        _attach_neighbors(findings, pages, clock, pre_skip)
        mapping = _mapping_check(pages, findings, trim_pages, trim_samples)
        warnings = list(stream.warnings)
        if codec:
            warnings.extend(codec.warnings)
        results.append(
            StreamAnalysis(
                serial=serial,
                codec=codec.codec if codec else None,
                codec_info=codec,
                pages=pages,
                discontinuities=findings,
                mapping=mapping,
                warnings=warnings,
                packet_count=len(stream.packets),
                audio_packet_count=len(audio_indices),
            )
        )
    return results


def analyze_bytes(data: bytes) -> list[StreamAnalysis]:
    return analyze_pages(parse_pages(data))


def analyze_file(path: str | Path) -> list[StreamAnalysis]:
    return analyze_bytes(Path(path).read_bytes())
