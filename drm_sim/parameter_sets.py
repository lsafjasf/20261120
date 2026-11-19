"""Annex-B parameter-set validation, including a VPS/SPS-only HEVC fixture."""

from __future__ import annotations

from dataclasses import dataclass

HEVC_NAMES = {32: "VPS", 33: "SPS", 34: "PPS"}
H264_NAMES = {7: "SPS", 8: "PPS"}


class ParameterSetError(ValueError):
    """Raised when an Annex-B stream cannot be parsed."""


@dataclass(frozen=True)
class NALUnit:
    offset: int
    start_code_size: int
    nal_type: int
    payload_size: int


@dataclass(frozen=True)
class ParameterSetReport:
    codec: str
    nal_types: tuple[int, ...]
    counts: dict[int, int]
    required_types: tuple[int, ...]
    missing_types: tuple[int, ...]
    complete: bool
    message: str

    @property
    def found(self) -> list[dict[str, object]]:
        names = HEVC_NAMES if self.codec == "hevc" else H264_NAMES
        return [
            {"type": nal_type, "name": names.get(nal_type, f"NAL_{nal_type}"), "count": count}
            for nal_type, count in sorted(self.counts.items())
        ]

    def to_dict(self) -> dict[str, object]:
        return {
            "codec": self.codec,
            "complete": self.complete,
            "nal_types": list(self.nal_types),
            "nal_count": len(self.nal_types),
            "found": self.found,
            "required_types": list(self.required_types),
            "missing_types": list(self.missing_types),
            "error_code": (
                "MISSING_" + "_".join(
                    (HEVC_NAMES if self.codec == "hevc" else H264_NAMES).get(
                        item, str(item)
                    )
                    for item in self.missing_types
                )
                if self.missing_types
                else None
            ),
            "message": self.message,
        }


class MissingParameterSetError(ParameterSetError):
    def __init__(self, report: ParameterSetReport) -> None:
        super().__init__(report.message)
        self.report = report
        names = HEVC_NAMES if report.codec == "hevc" else H264_NAMES
        self.code = "MISSING_" + "_".join(
            names.get(item, str(item)) for item in report.missing_types
        )


def split_annex_b(data: bytes) -> tuple[NALUnit, ...]:
    starts: list[tuple[int, int]] = []
    index = 0
    while index + 3 < len(data):
        if data[index : index + 4] == b"\x00\x00\x00\x01":
            starts.append((index, 4))
            index += 4
        elif data[index : index + 3] == b"\x00\x00\x01":
            starts.append((index, 3))
            index += 3
        else:
            index += 1
    if not starts:
        raise ParameterSetError("stream has no Annex-B start code")
    if any(data[index] != 0 for index in range(starts[0][0])):
        raise ParameterSetError("non-zero bytes precede the first start code")
    units: list[NALUnit] = []
    for position, (offset, start_size) in enumerate(starts):
        end = starts[position + 1][0] if position + 1 < len(starts) else len(data)
        payload_size = end - offset - start_size
        if payload_size <= 0:
            raise ParameterSetError(f"empty NAL unit at offset {offset}")
        units.append(NALUnit(offset, start_size, data[offset + start_size], payload_size))
    return tuple(units)


def _nal_type(unit: NALUnit, codec: str) -> int:
    if codec == "hevc":
        return (unit.nal_type >> 1) & 0x3F
    if codec == "h264":
        return unit.nal_type & 0x1F
    raise ParameterSetError(f"unsupported codec {codec!r}")


def inspect_parameter_sets(data: bytes, codec: str = "hevc") -> ParameterSetReport:
    codec = codec.lower()
    names = HEVC_NAMES if codec == "hevc" else H264_NAMES if codec == "h264" else None
    if names is None:
        raise ParameterSetError(f"unsupported codec {codec!r}")
    types = tuple(_nal_type(unit, codec) for unit in split_annex_b(data))
    counts: dict[int, int] = {}
    for nal_type in types:
        counts[nal_type] = counts.get(nal_type, 0) + 1
    required = tuple(names)
    missing = tuple(item for item in required if counts.get(item, 0) == 0)
    found_text = ", ".join(
        f"{names.get(item, f'NAL_{item}')}={counts[item]}"
        for item in sorted(counts)
        if item in names
    )
    if missing:
        missing_text = ", ".join(
            f"{names[item]} (NAL {item})" for item in missing
        )
        message = (
            f"{codec.upper()} parameter-set validation failed: missing {missing_text}; "
            f"stream contains [{found_text}]. The decoder cannot initialize without PPS."
            if missing == (34,)
            else f"{codec.upper()} parameter-set validation failed: missing {missing_text}; "
            f"stream contains [{found_text}]."
        )
    else:
        message = (
            f"{codec.upper()} parameter sets complete: [{found_text}]; "
            "VPS, SPS, and PPS are all present."
        )
    return ParameterSetReport(
        codec=codec,
        nal_types=types,
        counts=counts,
        required_types=required,
        missing_types=missing,
        complete=not missing,
        message=message,
    )


def require_parameter_sets(data: bytes, codec: str = "hevc") -> ParameterSetReport:
    report = inspect_parameter_sets(data, codec)
    if not report.complete:
        raise MissingParameterSetError(report)
    return report
