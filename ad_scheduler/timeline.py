# -*- coding: utf-8 -*-
"""ASCII timeline rendering with rejection / conflict reasons."""

from __future__ import annotations

from .models import ScheduleResult, Timeline

LANES = (("对白", "D"), ("音效保留", "S"), ("解说轨", "A"))


def _bar(row: list[str], start: float, end: float, t0: float,
         scale: float, ch: str, tag: str) -> None:
    a = int(round((start - t0) * scale))
    b = max(a + 1, int(round((end - t0) * scale)))
    for i in range(a, min(b, len(row))):
        row[i] = ch
    label = tag[: max(1, b - a)]
    row[a:a + len(label)] = label


def render(tl: Timeline, res: ScheduleResult, width: int = 96) -> str:
    """Draw lanes for dialogue / sfx / narration plus a reasons block."""
    t0, t1 = 0.0, tl.duration
    scale = (width - 12) / max(t1 - t0, 1e-9)
    lines = []
    header = "时间(s)    " + "".join(
        str(int(t0 + i / scale))[-1] if i % max(1, int(5 * scale)) == 0
        else " " for i in range(width - 12))
    lines.append(header)
    grid = {name: ["·"] * (width - 12) for name, _ in LANES}
    for iv in tl.dialogue:
        _bar(grid["对白"], iv.start, iv.end, t0, scale, "D", "")
    for iv in tl.sfx_zones:
        _bar(grid["音效保留"], iv.start, iv.end, t0, scale, "S", "")
    for p in res.placements:
        mark = "L" if p.version == "long" else "s"
        tag = f"{p.clip.clip_id}{mark}{'*' if p.locked else ''}"
        _bar(grid["解说轨"], p.start, p.end, t0, scale, "#", tag)
    for name, _ in LANES:
        lines.append(f"{name:<8}  " + "".join(grid[name]))
    lines.append("")
    lines.append("图例: D=对白  S=音效保留区  #=解说(L长版/s短版/*锁定)  ·=空")
    lines.append("")
    lines.append("落点明细:")
    for p in res.placements:
        lock = " [锁定]" if p.locked else ""
        lines.append(
            f"  {p.clip.clip_id:<6} {p.version:<5} "
            f"{p.start:7.2f} -> {p.end:7.2f}  重要度={p.clip.importance}{lock}")
    if res.rejections:
        lines.append("")
        lines.append("落选原因:")
        for r in res.rejections:
            lines.append(f"  {r.clip_id}: {r.reason}")
    if res.notes:
        lines.append("")
        lines.append("提示/冲突:")
        for n in res.notes:
            lines.append(f"  {n}")
    imp, longs, _ = res.score if res.score else (0, 0, 0)
    lines.append("")
    lines.append(f"得分: 重要度合计={imp}  长版数={longs}")
    return "\n".join(lines)
