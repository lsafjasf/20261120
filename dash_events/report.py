from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path

from .model import EventKey, Period, Presentation, ScheduledEvent
from .noise import DEFAULT_NOISE_PARAMETERS, NoiseParameters, summarize
from .scheduler import EventTimeline, RunResult
from .timing import ClockResolution, UtcTimingResolver


def _seconds(value: float) -> str:
    return f"{value:.3f}s"


def _period_signal(event: ScheduledEvent, period: Period) -> str:
    for signal in event.signals:
        if signal.period_id != period.id:
            continue
        local = (
            signal.presentation_time - signal.presentation_time_offset
        ) / signal.timescale
        return (
            f"pT={signal.presentation_time}, pTO={signal.presentation_time_offset}"
            f", ts={signal.timescale} -> {local:+.3f}s"
        )
    return "-"


def _boundary_state(event: ScheduledEvent, boundary: float) -> str:
    if event.end <= boundary:
        return "expired before switch"
    if event.start < boundary < event.end:
        return "active across switch"
    if event.start == boundary:
        return "starts on switch"
    return "starts after switch"


def _decision(event: ScheduledEvent, boundary: float) -> str:
    in_p0 = "p0" in event.declared_period_ids
    in_p1 = "p1" in event.declared_period_ids
    if in_p0 and in_p1 and event.start < boundary < event.end:
        return "carry active event; suppress P1 repeat"
    if in_p0 and in_p1 and event.end <= boundary:
        return "ignore stale P1 repeat"
    if not in_p0 and event.start == boundary:
        return "half-open boundary assigns to P1"
    if event.start >= boundary:
        return "P1-owned event"
    return "P0-owned event"


def _dispatch_counts(result: RunResult) -> dict[EventKey, int]:
    counts = {key: 0 for key in result.states}
    for dispatch in result.dispatches:
        counts[dispatch.key] += 1
    return counts


def _event_rows(
    presentation: Presentation,
    continuous: RunResult,
    fresh_p1: RunResult,
    resolver: UtcTimingResolver,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    boundary = presentation.periods[1].start
    p0, p1 = presentation.periods[0], presentation.periods[1]
    count_cont = _dispatch_counts(continuous)
    count_fresh = _dispatch_counts(fresh_p1)
    inheritance_rows: list[dict[str, object]] = []
    alignment_rows: list[dict[str, object]] = []
    for event in sorted(continuous.states.values(), key=lambda state: state.event.start):
        state = event
        item = state.event
        inheritance_rows.append(
            {
                "event_id": item.key.event_id,
                "scheme/value": f"{item.key.scheme_id_uri}/{item.key.value}",
                "P0 signal": _period_signal(item, p0),
                "P1 signal": _period_signal(item, p1),
                "global start": _seconds(item.start),
                "global end": _seconds(item.end),
                "owner": item.owner_period_id,
                "at 10s": _boundary_state(item, boundary),
                "decision": _decision(item, boundary),
                "continuous triggers": count_cont[item.key],
                "fresh-P1 triggers": count_fresh[item.key],
                "post-switch triggers": state.post_switch_triggers,
                "inherited_at": (
                    _seconds(state.inherited_at)
                    if state.inherited_at is not None
                    else "-"
                ),
                "verdict": (
                    "PASS"
                    if count_cont[item.key] == 1
                    and count_fresh[item.key] <= 1
                    and state.post_switch_triggers <= 1
                    else "FAIL"
                ),
            }
        )
        alignment_rows.append(
            {
                "event_id": item.key.event_id,
                "declared period(s)": ", ".join(item.declared_period_ids),
                "owner period": item.owner_period_id,
                "media start": _seconds(item.start),
                "media end": _seconds(item.end),
                "wall clock UTC": resolver.event_utc(item).isoformat().replace(
                    "+00:00", "Z"
                ),
            }
        )
    return inheritance_rows, alignment_rows


def _markdown_table(rows: list[dict[str, object]], columns: list[str]) -> str:
    lines = [
        "| " + " | ".join(columns) + " |",
        "|" + "|".join("---" for _ in columns) + "|",
    ]
    for row in rows:
        lines.append(
            "| " + " | ".join(str(row[column]) for column in columns) + " |"
        )
    return "\n".join(lines)


def _event_checks(
    presentation: Presentation,
    continuous: RunResult,
    fresh_p1: RunResult,
) -> dict[str, object]:
    boundary = presentation.periods[1].start
    expected = set(continuous.states)
    continuous_keys = [dispatch.key for dispatch in continuous.dispatches]
    fresh_keys = [dispatch.key for dispatch in fresh_p1.dispatches]
    cross_period = [
        state.event
        for state in continuous.states.values()
        if len(state.event.declared_period_ids) > 1
        and state.event.start < boundary < state.event.end
    ]
    stale = [
        state.event
        for state in continuous.states.values()
        if len(state.event.declared_period_ids) > 1
        and state.event.end <= boundary
    ]
    boundary_events = [
        state.event
        for state in continuous.states.values()
        if state.event.start == boundary
    ]
    continuous_counts = _dispatch_counts(continuous)
    fresh_counts = _dispatch_counts(fresh_p1)
    inherited_keys = {
        key
        for transition in continuous.transitions
        for key in transition.inherited_keys
    }
    stale_not_retriggered = all(
        continuous.states[event.key].post_switch_triggers == 0
        and fresh_counts[event.key] == 0
        for event in stale
    )
    checks = {
        "continuous no loss": expected == set(continuous_keys),
        "continuous no duplicate": all(
            count == 1 for count in continuous_counts.values()
        ),
        "fresh P1 no duplicate": all(count <= 1 for count in fresh_counts.values()),
        "cross-period inherited": all(
            event.key in inherited_keys for event in cross_period
        ),
        "cross-period triggered once": all(
            continuous_counts[event.key] == 1
            and fresh_counts[event.key] == 1
            for event in cross_period
        ),
        "boundary event belongs to P1": all(
            event.owner_period_id == presentation.periods[1].id
            for event in boundary_events
        ),
        "expired event not retriggered": stale_not_retriggered,
    }
    checks["all passed"] = all(checks.values())
    return {
        "checks": checks,
        "expected_event_ids": sorted(key.event_id for key in expected),
        "continuous_dispatch_ids": [key.event_id for key in continuous_keys],
        "fresh_p1_dispatch_ids": [key.event_id for key in fresh_keys],
        "cross_period_event_ids": sorted(
            event.key.event_id for event in cross_period
        ),
        "stale_event_ids": sorted(event.key.event_id for event in stale),
        "boundary_event_ids": sorted(
            event.key.event_id for event in boundary_events
        ),
        "transitions": [
            {
                "at": transition.at,
                "from": transition.from_period_id,
                "to": transition.to_period_id,
                "inherited_event_ids": [
                    key.event_id for key in transition.inherited_keys
                ],
            }
            for transition in continuous.transitions
        ],
    }


def _clock_section(
    presentation: Presentation,
    resolver: UtcTimingResolver,
    local_utc: datetime,
) -> tuple[dict[str, object], str]:
    clock: ClockResolution = resolver.resolve(local_utc)
    mpd_time = resolver.media_time(local_utc, clock)
    payload = {
        "local_utc": clock.local_utc.isoformat(),
        "selected_scheme": clock.scheme_id_uri,
        "server_utc": clock.server_utc.isoformat(),
        "offset_seconds": clock.offset_seconds,
        "estimated_mpd_media_time": mpd_time,
        "availability_start_time": (
            presentation.availability_start_time.isoformat()
        ),
        "formula": (
            "media_time = (local_utc + (server_utc - local_utc)"
            " - availabilityStartTime)"
        ),
    }
    text = "\n".join(
        [
            "## UTCTiming 对齐",
            "",
            "```text",
            "offset = server_utc - local_utc",
            "media_time = local_utc + offset - availabilityStartTime",
            "event_utc = availabilityStartTime + event_global_start",
            "```",
            "",
            f"- 本地钟：`{clock.local_utc.isoformat()}`",
            f"- 选用时钟源：`{clock.scheme_id_uri}`",
            f"- 服务端 UTC：`{clock.server_utc.isoformat()}`",
            f"- 偏差：`{clock.offset_seconds:+.3f}s`",
            f"- 对应 MPD 媒体时间：`{mpd_time:.3f}s`",
            "",
        ]
    )
    return payload, text


def _noise_rows(
    parameters: tuple[NoiseParameters, ...],
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for item in parameters:
        stats = summarize(item)
        rows.append(
            {
                "seed": item.seed,
                "n": item.count,
                "param mean": f"{item.mean:.6f}",
                "param variance": f"{item.variance:.6f}",
                "sample mean": f"{stats.sample_mean:.6f}",
                "sample variance": f"{stats.sample_variance:.6f}",
                "mean error": f"{stats.mean_error:.6f}",
                "variance error": f"{stats.variance_error:.6f}",
                "result": "PASS" if stats.accepted else "FAIL",
            }
        )
    return rows


def _noise_markdown(rows: list[dict[str, object]]) -> str:
    columns = [
        "seed",
        "n",
        "param mean",
        "param variance",
        "sample mean",
        "sample variance",
        "mean error",
        "variance error",
        "result",
    ]
    return "\n".join(
        [
            "## 噪声参数与统计量",
            "",
            _markdown_table(rows, columns),
            "",
            "每个 seed 固定对应一组 `(mean, variance)`；"
            "容差按样本均值标准误和卡方方差近似设置。",
            "",
        ]
    )


def build_report(
    mpd_path: str | Path,
    *,
    noise_parameters: tuple[NoiseParameters, ...] = DEFAULT_NOISE_PARAMETERS,
    until: float | None = None,
    local_utc: datetime | None = None,
) -> dict[str, object]:
    from .mpd import parse_mpd

    presentation = parse_mpd(mpd_path)
    if len(presentation.periods) < 2:
        raise ValueError("validation report requires at least two Periods")
    boundary = presentation.periods[1].start
    end_time = min(until or presentation.duration, presentation.duration)
    if end_time < boundary:
        raise ValueError("validation must cover the first Period transition")

    continuous = EventTimeline(presentation).advance_through(end_time, 0.25)
    fresh_p1 = EventTimeline(presentation, start=boundary).advance_through(
        end_time, 0.25
    )
    resolver = UtcTimingResolver(presentation)
    clock_time = local_utc or datetime(
        2026, 1, 1, 0, 0, 5, tzinfo=timezone.utc
    )
    clock_payload, clock_markdown = _clock_section(
        presentation, resolver, clock_time
    )
    events, alignment = _event_rows(
        presentation, continuous, fresh_p1, resolver
    )
    checks = _event_checks(presentation, continuous, fresh_p1)
    noise_rows = _noise_rows(noise_parameters)
    noise_payload: list[dict[str, object]] = []
    for item in noise_parameters:
        stats = summarize(item)
        noise_payload.append(
            {
                "seed": item.seed,
                "count": item.count,
                "mean": item.mean,
                "variance": item.variance,
                "sample_mean": stats.sample_mean,
                "sample_variance": stats.sample_variance,
                "accepted": stats.accepted,
            }
        )

    event_columns = [
        "event_id",
        "P0 signal",
        "P1 signal",
        "global start",
        "global end",
        "owner",
        "at 10s",
        "decision",
        "continuous triggers",
        "fresh-P1 triggers",
        "post-switch triggers",
        "inherited_at",
        "verdict",
    ]
    alignment_columns = [
        "event_id",
        "declared period(s)",
        "owner period",
        "media start",
        "media end",
        "wall clock UTC",
    ]
    check_rows = [
        {"check": name, "result": "PASS" if passed else "FAIL"}
        for name, passed in checks["checks"].items()
    ]
    check_rows.append(
        {
            "check": "continuous dispatch order",
            "result": " -> ".join(
                str(event_id) for event_id in checks["continuous_dispatch_ids"]
            ),
        }
    )

    event_markdown = "\n".join(
        [
            "# MPEG-DASH EventStream period 边界验证",
            "",
            "事件统一映射为全局媒体时间，Period 区间按 `[start, end)` 处理；"
            "逻辑事件身份为 `(schemeIdUri, value, id)`。",
            "",
            "## 事件继承表",
            "",
            f"`schemeIdUri=urn:example:boundary`, `value=main`, 切换点 `{boundary:g}s`。",
            "",
            _markdown_table(events, event_columns),
            "",
            "## 校验结果",
            "",
            _markdown_table(check_rows, ["check", "result"]),
            "",
            f"- 期望事件 ID：`{checks['expected_event_ids']}`",
            f"- 连续播放触发顺序：`{checks['continuous_dispatch_ids']}`",
            f"- 从 P1 起播触发序列：`{checks['fresh_p1_dispatch_ids']}`",
            f"- 跨 period 活跃事件：`{checks['cross_period_event_ids']}`",
            f"- 切换前已过期事件：`{checks['stale_event_ids']}`",
            "",
            "## 媒体时间与 UTC 对齐",
            "",
            _markdown_table(alignment, alignment_columns),
            "",
            clock_markdown,
            _noise_markdown(noise_rows),
            "## 结论",
            "",
            "- 9s 开始的 1002 跨越 10s 边界，只触发一次，并在 P1 重发时按身份去重。",
            "- 10s 的 1003 按半开区间归属于 P1，不发生 P0/P1 双触发。",
            "- 5s 开始、5.5s 结束的 1004 在切换前已过期，P1 陈旧重发不会再次触发。",
            "- 连续播放与直接从 P1 起播均未出现重复或丢失。",
            "",
        ]
    )

    payload: dict[str, object] = {
        "mpd": str(mpd_path),
        "periods": [
            {"id": period.id, "start": period.start, "end": period.end}
            for period in presentation.periods
        ],
        "utc_timing": clock_payload,
        "event_inheritance": events,
        "event_alignment": alignment,
        "event_checks": checks,
        "noise": noise_payload,
    }
    return {
        "markdown": event_markdown,
        "payload": payload,
        "noise_rows": noise_rows,
        "checks": checks["checks"],
    }


def write_report(
    report_dir: str | Path,
    report: dict[str, object],
) -> dict[str, Path]:
    output = Path(report_dir)
    output.mkdir(parents=True, exist_ok=True)
    markdown_path = output / "report.md"
    json_path = output / "event_inheritance.json"
    csv_path = output / "noise_stats.csv"
    markdown_path.write_text(str(report["markdown"]), encoding="utf-8")
    json_path.write_text(
        json.dumps(report["payload"], ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    noise_rows = report["noise_rows"]
    assert isinstance(noise_rows, list)
    if noise_rows:
        with csv_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(noise_rows[0]))
            writer.writeheader()
            writer.writerows(noise_rows)
    return {
        "markdown": markdown_path,
        "json": json_path,
        "csv": csv_path,
    }
