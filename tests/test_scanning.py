import unittest

from mp2inspect import scan

from .helpers import read_sample


class SampleRateAndPaddingTests(unittest.TestCase):
    def test_individual_sample_rates(self):
        expected = {
            "stereo_32000_plain.mp2": (32000, 7, 576),
            "stereo_44100_plain.mp2": (44100, 77, 417),
            "stereo_48000_plain.mp2": (48000, 11, 384),
        }
        for name, (sample_rate, total, length) in expected.items():
            with self.subTest(name=name):
                result = scan(read_sample(name))
                self.assertEqual(result.total_frames, total)
                self.assertEqual(result.boundary_conflicts, [])
                self.assertEqual(result.skipped, [])
                self.assertTrue(
                    all(frame.header.sample_rate == sample_rate for frame in result.frames)
                )
                self.assertTrue(
                    all(frame.length == length for frame in result.frames)
                )

    def test_real_padded_stream_uses_each_frames_padding_bit(self):
        result = scan(read_sample("stereo_44100_protected_padding.mp2"))
        self.assertEqual(result.total_frames, 77)
        self.assertEqual(result.boundary_conflicts, [])
        self.assertTrue(any(frame.header.padding == 1 for frame in result.frames))
        for frame in result.frames:
            self.assertEqual(
                frame.length,
                (144000 * 128) // 44100 + frame.header.padding,
            )
            self.assertEqual(frame.offset % 1, 0)

    def test_alternating_padding_does_not_shift_next_header(self):
        result = scan(read_sample("padding_alternating.mp2"))
        self.assertEqual(result.total_frames, 8)
        self.assertEqual(result.boundary_conflicts, [])
        self.assertEqual(
            [frame.header.padding for frame in result.frames],
            [0, 1, 0, 1, 1, 0, 1, 0],
        )
        self.assertEqual(
            [frame.length for frame in result.frames],
            [417, 418, 417, 418, 418, 417, 418, 417],
        )
        expected_offset = 0
        for frame in result.frames:
            self.assertEqual(frame.offset, expected_offset)
            expected_offset += frame.length
        self.assertEqual(expected_offset, len(read_sample("padding_alternating.mp2")))
        self.assertTrue(all(frame.crc_ok for frame in result.frames))

    def test_sample_rate_changes_are_parsed_per_frame(self):
        result = scan(read_sample("rate_padding_switch.mp2"))
        self.assertEqual(result.total_frames, 12)
        self.assertEqual(result.boundary_conflicts, [])
        self.assertEqual(
            [frame.header.sample_rate for frame in result.frames],
            [32000, 32000, 44100, 44100, 48000, 48000] * 2,
        )
        self.assertEqual(
            [frame.length for frame in result.frames],
            [576, 577, 417, 418, 384, 385] * 2,
        )

    def test_encoded_sample_rate_switch_stream(self):
        result = scan(read_sample("rate_switch_32000_44100_48000.mp2"))
        self.assertEqual(result.total_frames, 28)
        self.assertEqual(result.boundary_conflicts, [])
        self.assertEqual(
            [frame.header.sample_rate for frame in result.frames],
            [32000] * 7 + [44100] * 10 + [48000] * 11,
        )


class CrcAndFreeFormatTests(unittest.TestCase):
    def test_crc_protected_frames_are_valid(self):
        result = scan(read_sample("stereo_44100_protected.mp2"))
        self.assertEqual(result.total_frames, 77)
        self.assertEqual(result.crc_error_frames, [])
        self.assertTrue(all(frame.crc_ok is True for frame in result.frames))

    def test_crc_errors_are_located_by_frame_and_byte_offset(self):
        result = scan(read_sample("stereo_44100_crc_errors.mp2"))
        self.assertEqual([frame.index for frame in result.crc_error_frames], [10, 30, 50])
        self.assertEqual(
            [frame.offset for frame in result.crc_error_frames],
            [4170, 12510, 20850],
        )
        self.assertEqual(result.boundary_conflicts, [])
        self.assertEqual(result.skipped, [])

    def test_free_format_is_not_reported_as_corrupt(self):
        result = scan(read_sample("stereo_44100_free.mp2"))
        self.assertEqual(result.total_frames, 77)
        self.assertEqual(len(result.free_format_frames), 77)
        self.assertEqual(result.crc_error_frames, [])
        self.assertEqual(result.boundary_conflicts, [])
        self.assertEqual(result.skipped, [])
        self.assertEqual(
            [frame.offset for frame in result.frames],
            [index * 417 for index in range(77)],
        )
        self.assertTrue(
            all(abs(frame.inferred_bitrate_kbps - 128) < 0.5 for frame in result.frames)
        )


if __name__ == "__main__":
    unittest.main()
