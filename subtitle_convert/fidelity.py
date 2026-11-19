"""Round-trip conversion measurement and bilingual offset checks."""

from .convert import parse, dump
from .diff import diff_documents, max_timeline_drift, changed_cue_count
from .timefmt import (format_srt_time, format_ass_time,
                      ms_to_centiseconds)


def run_chain(seed_text, seed_fmt, chain, cycles=1):
    """Run e.g. chain=("vtt","ass","vtt") for `cycles` repetitions.

    Returns a report with per-leg diffs and cumulative drift against the
    original canonical document.
    """
    original = parse(seed_text, seed_fmt)
    current_text = seed_text
    current_fmt = seed_fmt
    legs = []
    all_losses = []
    for cycle in range(cycles):
        for dst_fmt in chain:
            before = parse(current_text, current_fmt)
            dst_text = dump(before, dst_fmt)
            after = parse(dst_text, dst_fmt)
            all_losses.extend((cycle, dst_fmt, loss)
                              for loss in after.losses)
            leg_diff = diff_documents(before, after,
                                      current_fmt, dst_fmt)
            legs.append({
                "cycle": cycle,
                "from": current_fmt,
                "to": dst_fmt,
                "text": dst_text,
                "diff": leg_diff,
                "leg_drift_ms": max_timeline_drift(leg_diff),
                "text_changed_cues": changed_cue_count(leg_diff),
            })
            current_text, current_fmt = dst_text, dst_fmt
    final = parse(current_text, current_fmt)
    cumulative = diff_documents(original, final, seed_fmt, current_fmt)
    return {
        "seed_fmt": seed_fmt,
        "final_fmt": current_fmt,
        "cycles": cycles,
        "legs": legs,
        "cumulative_diff": cumulative,
        "cumulative_drift_ms": max_timeline_drift(cumulative),
        "text_changed_cues": changed_cue_count(cumulative),
        "losses": all_losses,
        "final_text": current_text,
    }


def timeline_quantization(fmt, ms):
    """The representable grid for a format, in ms."""
    if fmt == "ass":
        cs = ms_to_centiseconds(ms)
        return format_ass_time(ms), abs(cs * 10 - ms)
    return format_srt_time(ms), 0


def roundtrip_error_table(report):
    rows = []
    diff = report["cumulative_diff"]
    for row in diff["rows"]:
        if row["kind"] != "equal-count":
            rows.append({"index": row["index"], "status": row["kind"]})
            continue
        rows.append({
            "index": row["index"],
            "start_before": format_srt_time(row["start_before"]),
            "start_after": format_srt_time(row["start_after"]),
            "start_delta_ms": row["start_delta_ms"],
            "end_before": format_srt_time(row["end_before"]),
            "end_after": format_srt_time(row["end_after"]),
            "end_delta_ms": row["end_delta_ms"],
            "text_changed": row["text_changed"],
        })
    return rows


def bilingual_offsets(primary, secondary):
    """Per-cue start/end offset (secondary - primary) for paired tracks."""
    if len(primary.cues) != len(secondary.cues):
        raise ValueError("tracks have different cue counts; align them first")
    return [{
        "index": idx,
        "start_offset_ms": secondary.cues[idx].start -
                           primary.cues[idx].start,
        "end_offset_ms": secondary.cues[idx].end -
                         primary.cues[idx].end,
    } for idx in range(len(primary.cues))]


def verify_bilingual_offsets(primary_before, secondary_before,
                             primary_after, secondary_after,
                             tolerance_ms=0):
    before = bilingual_offsets(primary_before, secondary_before)
    after = bilingual_offsets(primary_after, secondary_after)
    mismatches = []
    for b, a in zip(before, after):
        for key in ("start_offset_ms", "end_offset_ms"):
            if abs(b[key] - a[key]) > tolerance_ms:
                mismatches.append({"index": b["index"], "field": key,
                                   "before_ms": b[key], "after_ms": a[key]})
    return {
        "tolerance_ms": tolerance_ms,
        "offset_before": before,
        "offset_after": after,
        "preserved": not mismatches,
        "mismatches": mismatches,
    }
