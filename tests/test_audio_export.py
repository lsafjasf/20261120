# -*- coding: utf-8 -*-
"""Verify the exported narration WAV: real onsets must match the plan."""

import json
import os
import tempfile
import unittest
import wave

from ad_scheduler.audio import (SAMPLE_RATE, detect_onsets,
                                export_placement_list, render_narration_track)
from ad_scheduler.samples import SAMPLES
from ad_scheduler.scheduler import Scheduler

TOL = 0.03  # 30 ms onset tolerance


class TestAudioExport(unittest.TestCase):
    def test_onsets_match_scheduled_starts(self):
        for name, (_, fn) in SAMPLES.items():
            with self.subTest(sample=name):
                tl = fn()
                res = Scheduler(tl).schedule()
                with tempfile.TemporaryDirectory() as tmp:
                    wav = os.path.join(tmp, "ad.wav")
                    render_narration_track(res, wav,
                                           total_duration=tl.duration)
                    regions = detect_onsets(wav)
                    starts = sorted(p.start for p in res.placements)
                    self.assertEqual(len(regions), len(starts),
                                     f"{name}: {regions} vs {starts}")
                    for (onset, _), want in zip(regions, starts):
                        self.assertAlmostEqual(onset, want, delta=TOL)
                    with wave.open(wav, "rb") as w:
                        dur = w.getnframes() / w.getframerate()
                    self.assertAlmostEqual(dur, tl.duration, delta=0.01)
                    self.assertEqual(w.getframerate(), SAMPLE_RATE)

    def test_placement_list_json(self):
        tl = SAMPLES["contention"][1]()
        res = Scheduler(tl).schedule()
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "list.json")
            export_placement_list(res, path)
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        self.assertEqual(data["placements"][0]["clip_id"], "AD1")
        self.assertEqual(data["placements"][0]["start"], 10.0)
        self.assertEqual(data["rejections"][0]["clip_id"], "AD2")
        self.assertIn("reason", data["rejections"][0])


if __name__ == "__main__":
    unittest.main()
