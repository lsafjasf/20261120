import json
import tempfile
import unittest
from pathlib import Path

import amr_nb_analysis as amr


class TocTests(unittest.TestCase):
    def test_all_speech_mode_bits(self):
        for mode in amr.MODES:
            toc = amr.parse_toc(amr.make_toc(mode.ft, 1))
            self.assertEqual(toc.ft, mode.ft)
            self.assertIs(toc.mode, mode)
            self.assertFalse(toc.bad)
            bad = amr.parse_toc(amr.make_toc(mode.ft, 0))
            self.assertTrue(bad.bad)

    def test_p_bit_must_be_zero(self):
        with self.assertRaises(ValueError):
            amr.parse_toc(0x80)

    def test_sid_and_no_data_lengths(self):
        self.assertEqual(amr.payload_size(8), 5)
        self.assertEqual(amr.payload_size(15), 0)


class AllocationTests(unittest.TestCase):
    def test_tables_are_consistent(self):
        amr.validate_tables()
        for mode in amr.MODES:
            summary = amr.allocation_summary(mode)
            groups = summary["groups"]
            self.assertEqual(sum(groups.values()), mode.bits)
            self.assertEqual(summary["min_total_bytes"], mode.total_bytes)
            self.assertEqual(summary["max_total_bytes"], mode.total_bytes)

    def test_expected_frame_lengths(self):
        expected = {
            "12.2k": (244, 31, 32),
            "10.2k": (204, 26, 27),
            "7.95k": (159, 20, 21),
            "7.40k": (148, 19, 20),
            "6.70k": (134, 17, 18),
            "5.90k": (118, 15, 16),
            "5.15k": (103, 13, 14),
            "4.75k": (95, 12, 13),
        }
        for mode in amr.MODES:
            self.assertEqual(
                (mode.bits, mode.payload_bytes, mode.total_bytes),
                expected[mode.name],
            )


class BitstreamTests(unittest.TestCase):
    def test_round_trip_and_bad_frame_marking(self):
        frames = []
        for mode in amr.MODES:
            frames.append(
                (amr.make_toc(mode.ft, 1), bytes((0xA5,)) * mode.payload_bytes)
            )
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.amr"
            damaged = Path(directory) / "damaged.amr"
            amr.write_amr(source, frames)
            parsed = amr.parse_amr(source)
            self.assertEqual(len(parsed), len(amr.MODES))
            amr.corrupt_amr(source, damaged, [3])
            damaged_frames = amr.parse_amr(damaged)
            self.assertEqual(damaged_frames[3]["toc"].ft, 4)
            self.assertEqual(damaged_frames[3]["toc"].quality, 0)
            self.assertNotEqual(
                damaged_frames[3]["payload"], parsed[3]["payload"]
            )


class ArtifactTests(unittest.TestCase):
    def test_experiment_summary(self):
        path = amr.ROOT / "artifacts" / "amr_nb_analysis.json"
        if not path.exists():
            self.skipTest("run amr_nb_analysis.py first")
        data = json.loads(path.read_text())
        stats = data["frame_statistics"]
        self.assertEqual(stats["total_frames"], 640)
        self.assertEqual(stats["bad_frames"], 32)
        for row in stats["by_mode"]:
            self.assertEqual(row["frames"], 80)
            self.assertEqual(row["bad_frames"], 4)
            self.assertEqual(row["min_total_bytes"], row["max_total_bytes"])
        switch = data["mode_switch"]
        self.assertTrue(switch["pass"])
        self.assertTrue(switch["detector_sensitive_to_click"])
        self.assertLess(switch["clean"]["boundary_max_jump_ratio"], 2.0)
        self.assertGreater(
            switch["reset_decoder_control"]["boundary_max_jump_ratio"], 2.0
        )
        self.assertGreater(
            data["correlation"]["concealed_vs_source"], 0.8
        )
        self.assertGreater(
            data["correlation"]["concealed_vs_clean_decoded"], 0.9
        )


if __name__ == "__main__":
    unittest.main()
