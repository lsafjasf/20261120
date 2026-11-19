"""Cue-by-cue timestamp and text diff between two canonical Documents."""

import difflib


def diff_documents(before, after, label_before="before", label_after="after"):
    rows = []
    max_len = max(len(before.cues), len(after.cues))
    for idx in range(max_len):
        a = before.cues[idx] if idx < len(before.cues) else None
        b = after.cues[idx] if idx < len(after.cues) else None
        rows.append(_diff_cue(idx, a, b))
    return {
        "label_before": label_before,
        "label_after": label_after,
        "cue_count_before": len(before.cues),
        "cue_count_after": len(after.cues),
        "rows": rows,
    }


def _diff_cue(idx, a, b):
    if a is None:
        return {"index": idx, "kind": "added",
                "start_after": b.start, "end_after": b.end,
                "start_delta_ms": None, "end_delta_ms": None,
                "text_opcodes": [("insert", 0, 0, 0, len(b.text))],
                "text_changed": True}
    if b is None:
        return {"index": idx, "kind": "removed",
                "start_before": a.start, "end_before": a.end,
                "start_delta_ms": None, "end_delta_ms": None,
                "text_opcodes": [("delete", 0, len(a.text), 0, 0)],
                "text_changed": True}
    opcodes = [op for op in difflib.SequenceMatcher(
        a=a.text, b=b.text).get_opcodes() if op[0] != "equal"]
    return {
        "index": idx,
        "kind": "equal-count",
        "start_before": a.start, "start_after": b.start,
        "end_before": a.end, "end_after": b.end,
        "start_delta_ms": b.start - a.start,
        "end_delta_ms": b.end - a.end,
        "text_before": a.text, "text_after": b.text,
        "text_opcodes": opcodes,
        "text_changed": bool(opcodes),
    }


def render_unified(before, after, label_before, label_after):
    a_lines = _flatten(before, label_before)
    b_lines = _flatten(after, label_after)
    lines = list(difflib.unified_diff(
        a_lines, b_lines, fromfile=label_before, tofile=label_after,
        lineterm=""))
    return "\n".join(lines) + ("\n" if lines else "")


def _flatten(doc, label):
    from .timefmt import format_srt_time
    lines = [f"# {label} ({len(doc.cues)} cues)"]
    for cue in doc.cues:
        lines.append(f"{format_srt_time(cue.start)} -> "
                     f"{format_srt_time(cue.end)} | {cue.text!r}")
    return lines


def max_timeline_drift(diff_result):
    deltas = [abs(r["start_delta_ms"]) for r in diff_result["rows"]
              if r.get("start_delta_ms") is not None]
    deltas += [abs(r["end_delta_ms"]) for r in diff_result["rows"]
               if r.get("end_delta_ms") is not None]
    return max(deltas, default=0)


def changed_cue_count(diff_result):
    return sum(1 for r in diff_result["rows"] if r["text_changed"])
