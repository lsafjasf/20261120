import unittest

from mp2inspect import scan
from mp2inspect.header import parse_header

from .helpers import read_sample


class HeaderTests(unittest.TestCase):
    def test_mpeg1_layer2_header_fields(self):
        data = read_sample("stereo_44100_plain.mp2")
        header = parse_header(data)
        self.assertIsNotNone(header)
        assert header is not None
        self.assertEqual(header.version, 0b11)
        self.assertEqual(header.layer, 0b10)
        self.assertEqual(header.bitrate_index, 8)
        self.assertEqual(header.bitrate_kbps, 128)
        self.assertEqual(header.sample_rate_index, 0)
        self.assertEqual(header.sample_rate, 44100)
        self.assertEqual(header.padding, 0)
        self.assertEqual(header.mode_name, "stereo")
        self.assertEqual(header.channels, 2)
        self.assertFalse(header.has_crc)
        self.assertFalse(header.free_format)
        self.assertEqual(header.frame_length, 417)

    def test_free_format_header_has_no_table_bitrate(self):
        header = parse_header(read_sample("stereo_44100_free.mp2"))
        self.assertIsNotNone(header)
        assert header is not None
        self.assertTrue(header.free_format)
        self.assertIsNone(header.bitrate_kbps)
        self.assertIsNone(header.frame_length)

    def test_rejects_non_layer2_and_reserved_fields(self):
        data = bytearray(read_sample("stereo_44100_plain.mp2"))
        data[1] = (data[1] & ~0x04) | 0x02  # layer III
        self.assertIsNone(parse_header(bytes(data)))
        self.assertNotEqual(scan(bytes(data)).total_frames, 77)

        data = bytearray(read_sample("stereo_44100_plain.mp2"))
        data[2] |= 0x0C  # invalid sample-rate index
        self.assertIsNone(parse_header(bytes(data)))

    def test_header_word_round_trip(self):
        header = parse_header(read_sample("stereo_44100_protected.mp2"))
        self.assertIsNotNone(header)
        assert header is not None
        self.assertEqual(header.header_word.to_bytes(4, "big")[0], 0xFF)
        self.assertTrue(header.has_crc)
        self.assertEqual(header.protection_bit, 0)


if __name__ == "__main__":
    unittest.main()
