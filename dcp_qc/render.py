from __future__ import annotations

from fractions import Fraction

from .model import AssetKind, InspectionReport, TimelineSegment, format_time

_KIND_LABELS = {
    AssetKind.PICTURE: "画面",
    AssetKind.SOUND: "声音",
    AssetKind.SUBTITLE: "字幕",
    AssetKind.CAPTION: "闭路字幕",
    AssetKind.OTHER: "辅助",
}


def _short_id(asset_id: str) -> str:
    value = asset_id.removeprefix("urn:uuid:")
    if len(value) > 12:
        return "..." + value[-8:]
    return value


def render_report(report: InspectionReport, *, width: int = 64) -> str:
    lines: list[str] = []
    status = "PASS" if report.ok else "FAIL"
    lines.append(f"DCP QC {status}")
    lines.append(f"Package: {report.root}")
    if report.cpl:
        lines.append(f"CPL    : {report.cpl.title}")
        lines.append(f"         {report.cpl.cpl_id}")
        lines.append(f"         {report.cpl.path}")
    lines.append(
        f"Playable: {format_time(report.total_duration)}"
        f"  |  edit rates: {', '.join(report.edit_rates) or '(none)'}"
    )
    lines.append(
        f"Assets  : {report.checked_assets} checked, "
        f"{report.checked_bytes} bytes streamed"
    )
    lines.append("")
    lines.extend(_render_timeline(report.timeline, report.total_duration, width))
    if report.diagnostics:
        lines.append("")
        lines.append(f"Errors ({len(report.diagnostics)})")
        for index, diagnostic in enumerate(report.diagnostics, start=1):
            lines.append(
                f"[{index}] {diagnostic.code.value}"
                f" | reel={diagnostic.reel or '-'}"
                f" | asset={diagnostic.asset_id or '-'}"
                f" | at={diagnostic.screening_time or '-'}"
                f" | volume={diagnostic.volume or '-'}"
            )
            lines.append(f"    {diagnostic.message}")
            if diagnostic.asset_path:
                lines.append(f"    path: {diagnostic.asset_path}")
            reel_context = diagnostic.details.get("reels", [])
            if len(reel_context) > 1:
                locations = ", ".join(
                    f"{item['reel']}@{item['screening_start']}"
                    for item in reel_context
                )
                lines.append(f"    occurrences: {locations}")
    else:
        lines.append("")
        lines.append("No errors.")
    counts = report.counts_by_code()
    if any(counts.values()):
        lines.append("")
        lines.append(
            "Summary: "
            + ", ".join(
                f"{code.replace('_', ' ').lower()}={count}"
                for code, count in counts.items()
                if count
            )
        )
    return "\n".join(lines)


def _render_timeline(
    timeline: list[TimelineSegment], playable: Fraction, width: int
) -> list[str]:
    lines = ["Timeline (composition clock, # = asset interval)"]
    if not timeline:
        lines.append("  (no reel timeline available)")
        return lines

    render_end = max(
        [playable, *(segment.end for segment in timeline), Fraction(1)]
    )
    label_width = 8
    scale_width = max(20, width)

    marker_count = 4
    markers = [
        (
            index,
            format_time(render_end * index / marker_count).rsplit(".", 1)[0],
        )
        for index in range(marker_count + 1)
    ]
    marker_line = [" "] * (label_width + scale_width + 1)
    time_line = [" "] * (label_width + scale_width + 1)
    previous_end = 0
    for index, stamp in markers:
        position = label_width + round(scale_width * index / marker_count)
        start = max(
            label_width,
            min(
                position - len(stamp) // 2,
                len(time_line) - len(stamp),
            ),
        )
        start = max(start, previous_end)
        for offset, char in enumerate(stamp):
            target = start + offset
            if target < len(time_line):
                time_line[target] = char
        previous_end = start + len(stamp) + 1
        marker_line[position] = "|"
    lines.append("".join(time_line).rstrip())
    lines.append("".join(marker_line).rstrip())

    for segment in timeline:
        start_ratio = max(Fraction(0), segment.start / render_end)
        end_ratio = max(Fraction(0), segment.end / render_end)
        start_pos = round(scale_width * start_ratio)
        end_pos = round(scale_width * end_ratio)
        if end_pos <= start_pos:
            end_pos = start_pos + 1
        bar = " " * start_pos + "#" * min(end_pos - start_pos, scale_width - start_pos)
        label = (
            f"{_reel_label(segment.reel_id)} {segment.kind.short_label}"
        ).ljust(label_width)
        flag = "!" if segment.error_codes else " "
        lines.append(
            f"{label}{bar:<{scale_width}}{flag}"
            f" {format_time(segment.start)}-{format_time(segment.end)}"
        )

    lines.append("")
    lines.append("Segments")
    for segment in timeline:
        rate = str(segment.edit_rate) if segment.edit_rate else "?"
        entry = "-" if segment.entry_point is None else segment.entry_point
        duration = "-" if segment.duration_frames is None else segment.duration_frames
        intrinsic = (
            "-"
            if segment.intrinsic_frames is None
            else segment.intrinsic_frames
        )
        inherited = " inherited" if segment.inherited else ""
        status = (
            " ERROR:" + ",".join(code.value for code in segment.error_codes)
            if segment.error_codes
            else ""
        )
        lines.append(
            f"  {_reel_label(segment.reel_id)} {_KIND_LABELS[segment.kind]}"
            f" {_short_id(segment.asset_id)}"
            f" @{rate} entry={entry} dur={duration} avail={intrinsic}"
            f" [{segment.volume or '-'}]{inherited}{status}"
        )
    return lines


def _reel_label(reel_id: str) -> str:
    value = reel_id.lower().removeprefix("urn:uuid:")
    if value.startswith("reel-"):
        return "R" + value[5:]
    if len(value) > 4:
        return value[-4:]
    return value
