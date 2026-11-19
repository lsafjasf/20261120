# -*- coding: utf-8 -*-
"""Constraint solver: pick version + start for each clip.

Score is lexicographic:
  1. total importance of placed clips (keep key visual info first)
  2. number of long versions used (prefer the fuller take)
  3. earlier starts preferred (negative sum of start times)
Locked (manual) placements are honoured as fixed constraints.
"""

from __future__ import annotations

from .models import (
    DescriptionClip,
    Interval,
    Placement,
    Rejection,
    ScheduleResult,
    Timeline,
)

EPS = 1e-9


def _fits(interval: Interval, blockers: list[Interval]) -> bool:
    return all(not interval.overlaps(b) for b in blockers)


def _candidate_starts(
    clip: DescriptionClip, duration: float, blockers: list[Interval]
) -> list[float]:
    """Starts worth trying: window edges and ends of blocking intervals."""
    lo, hi = clip.window_start, clip.window_end - duration
    if hi < lo - EPS:
        return []
    cands = {round(lo, 9), round(hi, 9)}
    for b in blockers:
        s = b.end
        if lo - EPS <= s <= hi + EPS:
            cands.add(round(min(max(s, lo), hi), 9))
    out = []
    for s in sorted(cands):
        if _fits(Interval(s, s + duration), blockers):
            out.append(s)
    return out


class Scheduler:
    def __init__(self, timeline: Timeline):
        self.tl = timeline
        self.static_blockers = timeline.blocked
        self.result = ScheduleResult()
        self._locked_intervals: list[Interval] = []

    def schedule(self) -> ScheduleResult:
        self._apply_locked()
        autos = [c for c in self.tl.clips if not c.locked]
        autos.sort(key=lambda c: (-c.importance,
                                  c.window_end - c.window_start, c.clip_id))
        best = self._search(autos)
        self.result.placements = self._locked_placements + best[1]
        self.result.placements.sort(key=lambda p: (p.start, p.clip.clip_id))
        self.result.score = best[0]
        self._explain(autos, best[1])
        return self.result

    def _apply_locked(self) -> None:
        self._locked_placements: list[Placement] = []
        for c in self.tl.clips:
            if not c.locked:
                continue
            dur = c.duration_of(c.locked_version)
            iv = Interval(c.locked_start, c.locked_start + dur, c.clip_id)
            win = Interval(c.window_start, c.window_end)
            if not win.contains(iv):
                self.result.notes.append(
                    f"[警告] 锁定解说 {c.clip_id} 超出其时间窗，仍按人工安排保留")
            for b in self.static_blockers:
                if iv.overlaps(b):
                    self.result.notes.append(
                        f"[警告] 锁定解说 {c.clip_id} 与保留区重叠"
                        f"({b.label or '被占用区间'})，仍按人工安排保留")
            for other in self._locked_intervals:
                if iv.overlaps(other):
                    self.result.notes.append(
                        f"[警告] 锁定解说 {c.clip_id} 与锁定解说 "
                        f"{other.label} 互相重叠，均按人工安排保留")
            self._locked_placements.append(
                Placement(c, c.locked_version, c.locked_start, locked=True))
            self._locked_intervals.append(iv)

    # -- branch and bound search ------------------------------------------

    def _search(self, clips: list[DescriptionClip]):
        best_score = (0, 0, 0.0)
        best_plan: list[Placement] = []
        remaining_imp = [0] * (len(clips) + 1)
        for i in range(len(clips) - 1, -1, -1):
            remaining_imp[i] = remaining_imp[i + 1] + clips[i].importance

        def rec(i: int, placed: list[Placement], score: tuple) -> None:
            nonlocal best_score, best_plan
            if score[0] + remaining_imp[i] < best_score[0]:
                return
            if i == len(clips):
                if score > best_score:
                    best_score, best_plan = score, list(placed)
                return
            clip = clips[i]
            blockers = (self.static_blockers + self._locked_intervals
                        + [p.interval for p in placed])
            for version in ("long", "short"):
                dur = clip.duration_of(version)
                for s in _candidate_starts(clip, dur, blockers):
                    p = Placement(clip, version, s)
                    long_bonus = 1 if version == "long" else 0
                    rec(i + 1, placed + [p],
                        (score[0] + clip.importance,
                         score[1] + long_bonus,
                         score[2] - s))
            rec(i + 1, placed, score)  # skip this clip

        rec(0, [], (0, 0, 0.0))
        return best_score, best_plan

    # -- explanations ------------------------------------------------------

    def _static_feasible(self, clip: DescriptionClip, version: str) -> bool:
        dur = clip.duration_of(version)
        return bool(_candidate_starts(clip, dur, self.static_blockers))

    def _explain(self, autos: list[DescriptionClip],
                 plan: list[Placement]) -> None:
        chosen = {p.clip.clip_id: p for p in plan}
        for clip in autos:
            p = chosen.get(clip.clip_id)
            if p is None:
                self.result.rejections.append(
                    Rejection(clip.clip_id, self._reject_reason(clip)))
            elif p.version == "short" and \
                    clip.short_duration < clip.long_duration:
                if self._static_feasible(clip, "long"):
                    why = "长版与更重要的解说/锁定安排冲突，改用短版"
                else:
                    why = "空档容纳不下长版，选用短版以保留信息"
                self.result.notes.append(f"[降级] {clip.clip_id}: {why}")

    def _reject_reason(self, clip: DescriptionClip) -> str:
        win = clip.window_end - clip.window_start
        if win < clip.short_duration - EPS:
            return (f"时间窗仅 {win:.2f}s，短版 "
                    f"{clip.short_duration:.2f}s 也放不下")
        if not self._static_feasible(clip, "short"):
            return "窗口内空档被对白/音效保留区切碎，无连续可用空档"
        return "可用空档被更重要或已锁定的解说占用，竞争失败"
