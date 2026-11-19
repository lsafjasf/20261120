from __future__ import annotations

from fractions import Fraction

from .model import (
    AssetKind,
    CompositionPlaylist,
    CplResource,
    Diagnostic,
    DiagnosticCode,
    TimelineSegment,
    format_time,
)
from .resolver import AssetResolver

_TIME_EPSILON = Fraction(1, 1_000_000)


def _playback_seconds(resource: CplResource) -> Fraction | None:
    if resource.duration is None or resource.edit_rate is None:
        return None
    return resource.edit_rate.seconds(resource.duration)


def _resource_volume(
    resolver: AssetResolver, resource: CplResource
) -> tuple[str | None, str | None, bool]:
    location = resolver.locate(resource.asset_id)
    return location.volume, location.path, location.inherited


def build_timeline(
    cpl: CompositionPlaylist,
    resolver: AssetResolver,
    diagnostics: list[Diagnostic],
) -> tuple[list[TimelineSegment], Fraction]:
    segments: list[TimelineSegment] = []
    reel_start = Fraction(0)

    for reel in cpl.reels:
        picture_seconds: Fraction | None = None
        for resource in reel.resources:
            if resource.kind is not AssetKind.PICTURE:
                continue
            candidate = _playback_seconds(resource)
            if candidate is not None and candidate > 0:
                picture_seconds = candidate
                break

        reel_spans: list[Fraction] = []
        for resource in reel.resources:
            volume, path, inherited = _resource_volume(resolver, resource)
            span = _playback_seconds(resource)
            if span is None:
                span = Fraction(0)
            reel_spans.append(span)
            segment = TimelineSegment(
                reel_id=reel.reel_id,
                kind=resource.kind,
                asset_id=resource.asset_id or "(无 Id)",
                start=reel_start,
                end=reel_start + span,
                edit_rate=resource.edit_rate,
                entry_point=resource.entry_point,
                duration_frames=resource.duration,
                intrinsic_frames=resource.intrinsic_duration,
                path=path,
                volume=volume,
                inherited=inherited,
            )
            segments.append(segment)

            _check_resource_fields(
                reel.reel_id, resource, segment, reel_start, diagnostics
            )
            _check_source_window(
                reel.reel_id, resource, segment, reel_start, diagnostics
            )
            _check_reel_window(
                reel.reel_id,
                resource,
                segment,
                reel_start,
                picture_seconds,
                diagnostics,
            )

        if picture_seconds is not None:
            reel_duration = picture_seconds
        elif reel_spans:
            reel_duration = max(reel_spans)
        else:
            reel_duration = Fraction(0)
        reel_start += reel_duration

    return segments, reel_start


def _diagnostic_for_segment(
    code: DiagnosticCode,
    message: str,
    reel_id: str,
    asset_id: str,
    segment: TimelineSegment,
    screening_time: Fraction,
    details: dict,
) -> Diagnostic:
    return Diagnostic(
        code=code,
        message=message,
        reel=reel_id,
        asset_id=asset_id,
        asset_path=segment.path,
        volume=segment.volume,
        screening_time=format_time(screening_time),
        details=details,
    )


def _check_resource_fields(
    reel_id: str,
    resource: CplResource,
    segment: TimelineSegment,
    reel_start: Fraction,
    diagnostics: list[Diagnostic],
) -> None:
    label = f"{resource.kind.value} 资产 {resource.asset_id or '(无 Id)'}"
    missing = []
    if resource.entry_point is None:
        missing.append("EntryPoint")
    if resource.duration is None:
        missing.append("Duration")
    if resource.intrinsic_duration is None:
        missing.append("IntrinsicDuration")
    if missing:
        diagnostic = _diagnostic_for_segment(
            DiagnosticCode.REFERENCE_MISMATCH,
            f"卷 {reel_id} 的{label} 缺少 {', '.join(missing)}",
            reel_id,
            resource.asset_id,
            segment,
            reel_start,
            {"missing_fields": missing},
        )
        diagnostics.append(diagnostic)
        segment.error_codes.append(diagnostic.code)
        return

    values = {
        "EntryPoint": resource.entry_point,
        "Duration": resource.duration,
        "IntrinsicDuration": resource.intrinsic_duration,
    }
    invalid = [name for name, value in values.items() if value is not None and value < 0]
    if invalid or (resource.duration is not None and resource.duration == 0):
        if not invalid:
            invalid = ["Duration"]
        diagnostic = _diagnostic_for_segment(
            DiagnosticCode.REFERENCE_MISMATCH,
            f"卷 {reel_id} 的{label} 含非法帧值: {', '.join(invalid)}",
            reel_id,
            resource.asset_id,
            segment,
            reel_start,
            {"invalid_fields": invalid},
        )
        diagnostics.append(diagnostic)
        segment.error_codes.append(diagnostic.code)


def _check_source_window(
    reel_id: str,
    resource: CplResource,
    segment: TimelineSegment,
    reel_start: Fraction,
    diagnostics: list[Diagnostic],
) -> None:
    if (
        resource.edit_rate is None
        or resource.entry_point is None
        or resource.duration is None
        or resource.intrinsic_duration is None
    ):
        return
    source_end = resource.entry_point + resource.duration
    if source_end <= resource.intrinsic_duration:
        return

    available_frames = max(0, resource.intrinsic_duration - resource.entry_point)
    available_seconds = resource.edit_rate.seconds(available_frames)
    overflow_at = reel_start + available_seconds
    diagnostic = _diagnostic_for_segment(
        DiagnosticCode.PLAYABLE_TIME_OVERFLOW,
        (
            f"卷 {reel_id} 的 {resource.kind.value} 资产 {resource.asset_id} "
            f"在源素材 {format_time(overflow_at)} 越过可用时长: "
            f"入口帧 {resource.entry_point} + 时长 {resource.duration} > "
            f"IntrinsicDuration {resource.intrinsic_duration} "
            f"({resource.edit_rate} 编辑单位/秒)"
        ),
        reel_id,
        resource.asset_id,
        segment,
        overflow_at,
        {
            "boundary": "source_available_duration",
            "entry_point": resource.entry_point,
            "duration_frames": resource.duration,
            "intrinsic_duration": resource.intrinsic_duration,
            "source_end_frame": source_end,
            "edit_rate": str(resource.edit_rate),
            "screening_end": format_time(segment.end),
        },
    )
    diagnostics.append(diagnostic)
    segment.error_codes.append(diagnostic.code)


def _check_reel_window(
    reel_id: str,
    resource: CplResource,
    segment: TimelineSegment,
    reel_start: Fraction,
    picture_seconds: Fraction | None,
    diagnostics: list[Diagnostic],
) -> None:
    if (
        resource.kind is AssetKind.PICTURE
        or picture_seconds is None
        or resource.edit_rate is None
        or resource.duration is None
    ):
        return
    resource_seconds = resource.edit_rate.seconds(resource.duration)
    if resource_seconds <= picture_seconds + _TIME_EPSILON:
        return

    boundary = reel_start + picture_seconds
    diagnostic = _diagnostic_for_segment(
        DiagnosticCode.PLAYABLE_TIME_OVERFLOW,
        (
            f"卷 {reel_id} 的 {resource.kind.value} 资产 {resource.asset_id} "
            f"在 {format_time(boundary)} 越过本卷画面终点: "
            f"{resource.duration} / {resource.edit_rate} = "
            f"{float(resource_seconds):.6f}s，画面仅 {float(picture_seconds):.6f}s"
        ),
        reel_id,
        resource.asset_id,
        segment,
        boundary,
        {
            "boundary": "reel_picture_duration",
            "resource_seconds": float(resource_seconds),
            "picture_seconds": float(picture_seconds),
            "edit_rate": str(resource.edit_rate),
            "duration_frames": resource.duration,
            "screening_end": format_time(segment.end),
        },
    )
    diagnostics.append(diagnostic)
    segment.error_codes.append(diagnostic.code)
