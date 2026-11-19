# -*- coding: utf-8 -*-
"""Verify scheduler scores against exhaustive grid search on samples."""

import itertools
import unittest

from ad_scheduler.models import Interval, Placement
from ad_scheduler.samples import SAMPLES
from ad_scheduler.scheduler import Scheduler

GRID = 0.1


def brute_force_best(tl):
    """Exhaustively enumerate every (version, start) combo on a grid."""
    locked = [c for c in tl.clips if c.locked]
    autos = [c for c in tl.clips if not c.locked]
    locked_iv = [Interval(c.locked_start,
                          c.locked_start + c.duration_of(c.locked_version))
                 for c in locked]
    static = tl.blocked + locked_iv

    def options(clip):
        opts = [None]
        for version in ("long", "short"):
            dur = clip.duration_of(version)
            t = clip.window_start
            while t + dur <= clip.window_end + 1e-9:
                iv = Interval(t, t + dur)
                if all(not iv.overlaps(b) for b in static):
                    opts.append((version, round(t, 9)))
                t += GRID
        return opts

    best = (0, 0)
    for combo in itertools.product(*(options(c) for c in autos)):
        used = []
        imp = longs = 0
        ok = True
        for clip, choice in zip(autos, combo):
            if choice is None:
                continue
            version, start = choice
            iv = Interval(start, start + clip.duration_of(version))
            if any(iv.overlaps(u) for u in used):
                ok = False
                break
            used.append(iv)
            imp += clip.importance
            longs += 1 if version == "long" else 0
        if ok:
            best = max(best, (imp, longs))
    return best


class TestSamples(unittest.TestCase):
    def test_exhaustive_score_match(self):
        for name, (_, fn) in SAMPLES.items():
            with self.subTest(sample=name):
                tl = fn()
                res = Scheduler(tl).schedule()
                bf = brute_force_best(tl)
                self.assertEqual((res.score[0], res.score[1]), bf,
                                 f"{name}: scheduler {res.score[:2]} != "
                                 f"exhaustive {bf}")

    def test_contention_higher_importance_wins(self):
        tl = SAMPLES["contention"][1]()
        res = Scheduler(tl).schedule()
        self.assertIsNotNone(res.placement_for("AD1"))
        self.assertIsNone(res.placement_for("AD2"))
        reasons = {r.clip_id: r.reason for r in res.rejections}
        self.assertIn("竞争失败", reasons["AD2"])

    def test_short_version_fallback(self):
        tl = SAMPLES["short_fits"][1]()
        res = Scheduler(tl).schedule()
        p = res.placement_for("AD2")
        self.assertEqual(p.version, "short")
        self.assertAlmostEqual(p.start, 14.0)  # after locked + sfx zone
        locked = res.placement_for("AD1")
        self.assertTrue(locked.locked)
        self.assertAlmostEqual(locked.start, 10.0)

    def test_no_gap_all_rejected(self):
        tl = SAMPLES["no_gap"][1]()
        res = Scheduler(tl).schedule()
        self.assertEqual(res.placements, [])
        self.assertEqual(len(res.rejections), 2)

    def test_constraints_hold(self):
        for name, (_, fn) in SAMPLES.items():
            with self.subTest(sample=name):
                tl = fn()
                res = Scheduler(tl).schedule()
                ivs = []
                for p in res.placements:
                    iv = p.interval
                    if not p.locked:
                        self.assertGreaterEqual(iv.start, p.clip.window_start)
                        self.assertLessEqual(iv.end, p.clip.window_end)
                        for b in tl.blocked:
                            self.assertFalse(iv.overlaps(b))
                    for u in ivs:
                        self.assertFalse(iv.overlaps(u))
                    ivs.append(iv)


if __name__ == "__main__":
    unittest.main()
