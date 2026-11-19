from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from aiff_tools import decode_extended80, encode_extended80, parse_aiff
from aiff_tools.audio import continuity_metrics, decode_channels, wsola_time_stretch
from aiff_tools.fixtures import generate_fixtures


class Extended80Tests(unittest.TestCase):
    def test_known_rates_round_trip(self):
        samples = {
            22050: "400dac44000000000000",
            44100: "400eac44000000000000",
            48000: "400ebb80000000000000",
            96000: "400fbb80000000000000",
        }
        for value, raw_hex in samples.items():
            raw = encode_extended80(value)
            self.assertEqual(raw.hex(), raw_hex)
            decoded = decode_extended80(raw)
            self.assertEqual(decoded["value"], value)
            self.assertEqual(decoded["exact"], str(value))


class ChunkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.files = generate_fixtures(Path(cls.temp.name))
        cls.parsed = {name: parse_aiff(path) for name, path in cls.files.items()}

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_comm_and_ssnd_fields(self):
        expected = {
            "stereo_48k_none.aifc": ("AIFC", "NONE", 16, 48000, 76),
            "stereo_48k_sowt.aifc": ("AIFC", "sowt", 16, 48000, 76),
            "stereo_48k_fl32.aifc": ("AIFC", "FL32", 32, 48000, 76),
            "stereo_48k_ulaw.aifc": ("AIFC", "ulaw", 8, 48000, 76),
            "stereo_48k_ima4.aifc": ("AIFC", "ima4", 16, 48000, 78),
            "stereo_48k_none_ssnd4096.aifc": ("AIFC", "NONE", 16, 48000, 4172),
            "stereo_48k_ima4_ssnd4096.aifc": ("AIFC", "ima4", 16, 48000, 4174),
            "stereo_48k_24bit.aiff": ("AIFF", "NONE", 24, 48000, 54),
        }
        for name, fields in expected.items():
            aiff = self.parsed[name]
            self.assertEqual(
                (aiff.form_type, aiff.comm.compression_type, aiff.comm.sample_size_bits,
                 aiff.comm.sample_rate, aiff.sample_location(0)["absolute_file_offset"]),
                fields,
            )
            self.assertEqual(aiff.expected_audio_bytes(), aiff.ssnd.audio_bytes)

    def test_all_sample_positions_are_recoverable(self):
        for aiff in self.parsed.values():
            if aiff.comm is None or aiff.layout is None:
                continue
            for frame in range(aiff.comm.num_sample_frames):
                for channel in range(aiff.comm.channels):
                    location = aiff.sample_location(frame, channel)
                    recovered = aiff.recover_location(
                        location["relative_byte_offset"], channel, location["nibble"]
                    )
                    self.assertIn(frame, recovered)

    def test_ima4_packet_boundary(self):
        aiff = self.parsed["stereo_48k_ima4.aifc"]
        self.assertEqual(aiff.sample_location(0)["relative_byte_offset"], 2)
        self.assertEqual(aiff.sample_location(1)["relative_byte_offset"], 2)
        self.assertEqual(aiff.sample_location(1)["nibble"], 1)
        self.assertEqual(aiff.sample_location(64)["relative_byte_offset"], 70)
        self.assertEqual(aiff.sample_location(11999)["relative_byte_offset"], 12733)

    def test_timestretch_output_length(self):
        source = self.parsed["timestretch_source_48k.aiff"]
        channels = decode_channels(source)
        output = wsola_time_stretch(channels, round(source.comm.num_sample_frames * 0.8))
        self.assertEqual(len(output[0]), 38400)
        metrics = continuity_metrics(output, 48000, 19456)
        self.assertLess(metrics["max_phase_jump_deg"], 5.0)


if __name__ == "__main__":
    unittest.main()
