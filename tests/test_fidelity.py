import unittest

from subtitle_convert import convert
from subtitle_convert.fidelity import (
    run_chain, verify_bilingual_offsets, bilingual_offsets,
)


SRT_SEED = """1
00:00:01,000 --> 00:00:03,500
<b>bold</b> and <font color="#FF3030">red</font>

2
00:00:04,000 --> 00:00:06,250
<i>italic</i> and <u>under</u>
"""


class FidelityTests(unittest.TestCase):
    def test_srt_vtt_ass_srt_lossless_repeated(self):
        report = run_chain(SRT_SEED, "srt", ("vtt", "ass", "srt"), cycles=3)
        self.assertEqual(report["cumulative_drift_ms"], 0)
        self.assertEqual(report["text_changed_cues"], 0)
        for leg in report["legs"]:
            self.assertEqual(leg["leg_drift_ms"], 0)

    def test_ass_centisecond_quantization_is_one_shot(self):
        seed = """1
00:00:01,234 --> 00:00:03,567
hello
"""
        report = run_chain(seed, "srt", ("ass", "srt"), cycles=2)
        self.assertLessEqual(report["cumulative_drift_ms"], 5)
        # after the first snap to the 10 ms grid, every later leg is exact
        self.assertEqual(report["legs"][1]["leg_drift_ms"], 0)
        self.assertEqual(report["legs"][2]["leg_drift_ms"], 0)
        row = report["cumulative_diff"]["rows"][0]
        self.assertEqual(row["start_after"], 1230)

    def test_vtt_srt_pair_is_exact(self):
        report = run_chain(SRT_SEED, "srt", ("vtt", "srt"), cycles=5)
        self.assertEqual(report["cumulative_drift_ms"], 0)
        self.assertEqual(report["text_changed_cues"], 0)

    def test_bilingual_offsets_preserved(self):
        zh = open("samples/bilingual.zh.srt", encoding="utf-8").read()
        en = open("samples/bilingual.en.srt", encoding="utf-8").read()
        primary_before = convert.parse(zh, "srt")
        secondary_before = convert.parse(en, "srt")
        z_text, e_text = zh, en
        for dst in ("vtt", "ass", "srt"):
            z_text = convert.dump(convert.parse(z_text), dst)
            e_text = convert.dump(convert.parse(e_text), dst)
        result = verify_bilingual_offsets(
            primary_before, secondary_before,
            convert.parse(z_text), convert.parse(e_text))
        self.assertTrue(result["preserved"])
        self.assertTrue(all(o["start_offset_ms"] == 150
                            for o in result["offset_after"]))

    def test_offsets_helper_requires_equal_length(self):
        with open("samples/bilingual.zh.srt", encoding="utf-8") as handle:
            zh = convert.parse(handle.read(), "srt")
        short = convert.parse("1\n00:00:01,000 --> 00:00:02,000\nx\n", "srt")
        with self.assertRaises(ValueError):
            bilingual_offsets(zh, short)


if __name__ == "__main__":
    unittest.main()
