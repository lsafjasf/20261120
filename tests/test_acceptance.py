from __future__ import annotations

import unittest

from audio_utils import SAMPLE_RATE
from evaluation import evaluate, public_results


class AcceptanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.results = evaluate()

    def test_all_acceptance_checks_pass(self) -> None:
        self.assertTrue(self.results["checks"]["all"], self.results["checks"])

    def test_sample_rate_and_input_snr(self) -> None:
        self.assertEqual(self.results["config"]["sample_rate_hz"], SAMPLE_RATE)
        self.assertAlmostEqual(self.results["input"]["snr_db"], 5.0, places=2)

    def test_each_algorithm_improves_snr(self) -> None:
        for name, item in self.results["algorithms"].items():
            with self.subTest(algorithm=name):
                self.assertGreater(item["speech"]["snr_improvement_db"], 0.0)
                self.assertTrue(item["speech"]["snr_pass"])

    def test_music_high_frequency_is_preserved(self) -> None:
        for name, item in self.results["algorithms"].items():
            with self.subTest(algorithm=name):
                music = item["music"]
                self.assertLessEqual(abs(music["hf_band_energy_change_db"]), 1.0)
                self.assertLessEqual(abs(music["hf_relative_energy_change_db"]), 1.0)
                self.assertGreaterEqual(music["hf_log_spectral_correlation"], 0.85)
                self.assertGreaterEqual(music["hf_envelope_correlation"], 0.75)

    def test_loudness_target(self) -> None:
        target = self.results["config"]["target_lufs"]
        tolerance = self.results["config"]["loudness_tolerance_lu"]
        for item in self.results["algorithms"].values():
            self.assertLessEqual(abs(item["speech"]["loudness_after_lufs"] - target), tolerance)

    def test_processing_is_realtime(self) -> None:
        for name, item in self.results["algorithms"].items():
            with self.subTest(algorithm=name):
                self.assertLess(item["timing"]["rtf"], 0.25)
                self.assertGreater(item["timing"]["realtime_factor_x"], 4.0)
                self.assertGreater(item["timing"]["measured_frames"], 0)

    def test_buffer_recovers_to_watermark(self) -> None:
        buffer = self.results["buffer"]
        self.assertAlmostEqual(
            buffer["tail_mean_frames"], buffer["target_watermark_frames"], delta=1.0
        )
        self.assertLessEqual(buffer["tail_std_frames"], 0.5)
        self.assertEqual(buffer["underruns"], 0)

    def test_public_results_are_json_ready(self) -> None:
        public = public_results(self.results)
        self.assertNotIn("_speech_output", public["algorithms"]["mmse"])
        self.assertIn("checks", public)


if __name__ == "__main__":
    unittest.main()
