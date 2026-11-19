import struct
import unittest

from caf_audio.caf import (
    AudioDescription,
    CAFFile,
    InvalidEndianFlagError,
    PCMDecoder,
    parse_channel_layout,
    validate_big_endian_flag,
)
from caf_audio.errors import CafFormatError
from caf_audio.fixtures import (
    SAMPLE_FRAMES,
    build_caf,
    build_invalid_endian_flag,
    build_little_endian,
    build_misordered_big_endian,
    chunk,
    description_payload,
)


class CafParserTests(unittest.TestCase):
    def test_misordered_big_endian_file(self):
        caf = CAFFile(build_misordered_big_endian())
        self.assertEqual(
            [item.chunk_type for item in caf.chunks],
            ["chan", "free", "data", "pakt", "desc"],
        )
        self.assertEqual(caf.frame_count, len(SAMPLE_FRAMES))
        self.assertEqual(caf.container_frame_count, len(SAMPLE_FRAMES))
        self.assertTrue(caf.description.is_big_endian)
        self.assertFalse(caf.description.is_little_endian)
        self.assertEqual(caf.description.sample_rate, 48000.0)
        self.assertEqual(caf.channel_layout.channel_names, ("Left", "Right"))
        self.assertEqual(caf.data_start, 64)
        self.assertEqual(caf.edit_count, 0)

    def test_big_endian_samples_are_not_swapped(self):
        caf = CAFFile(build_misordered_big_endian())
        self.assertEqual(caf.read_frames(), list(SAMPLE_FRAMES))
        first_raw = caf.analysis(1)["sample_frames"][0]["raw_hex"]
        self.assertEqual(first_raw, "03e8fc18")
        wrong_little = struct.unpack("<hh", bytes.fromhex(first_raw))
        self.assertEqual(wrong_little, (-6141, 6396))
        self.assertNotEqual(wrong_little, SAMPLE_FRAMES[0])

    def test_little_endian_control_file(self):
        caf = CAFFile(build_little_endian())
        self.assertFalse(caf.description.is_big_endian)
        self.assertTrue(caf.description.is_little_endian)
        self.assertEqual(caf.read_frames(), list(SAMPLE_FRAMES))

    def test_invalid_endian_flag_is_explicit(self):
        with self.assertRaises(InvalidEndianFlagError) as caught:
            CAFFile(build_invalid_endian_flag())
        message = str(caught.exception)
        self.assertIn("big-endian flag", message)
        self.assertIn("bit 1", message)
        self.assertIn("0x00000004", message)

    def test_validate_big_endian_flag(self):
        self.assertTrue(validate_big_endian_flag(1))
        self.assertFalse(validate_big_endian_flag(0))
        with self.assertRaisesRegex(InvalidEndianFlagError, "exactly 0 or 1"):
            validate_big_endian_flag(2)

    def test_endianness_expectation_mismatch_is_explicit(self):
        caf = CAFFile(build_misordered_big_endian())
        caf.assert_endianness("big")
        with self.assertRaisesRegex(
            InvalidEndianFlagError, "header says big-endian"
        ):
            caf.assert_endianness("little")

    def test_sample_frame_offsets_include_each_channel(self):
        caf = CAFFile(build_misordered_big_endian())
        self.assertEqual(
            caf.sample_frame_offset(0),
            {
                "frame": 0,
                "file_offset": 64,
                "data_relative_offset": 0,
                "channels": [
                    {"index": 0, "name": "Left", "file_offset": 64},
                    {"index": 1, "name": "Right", "file_offset": 66},
                ],
            },
        )
        self.assertEqual(caf.sample_frame_offset(3)["file_offset"], 76)
        self.assertEqual(
            caf.sample_frame_offset(2, channel_index=1),
            {
                "frame": 2,
                "file_offset": 72,
                "data_relative_offset": 8,
                "channels": [
                    {"index": 1, "name": "Right", "file_offset": 74}
                ],
            },
        )

    def test_bitmap_and_description_layout(self):
        bitmap = parse_channel_layout(
            struct.pack(">III", 0x00010000, 0b11, 0), 2
        )
        self.assertEqual(bitmap.channel_names, ("Left", "Right"))
        descriptions = b"".join(
            [
                struct.pack(">IIfff", 1, 0, -1.0, 0.0, 0.0),
                struct.pack(">IIfff", 2, 0, 1.0, 0.0, 0.0),
            ]
        )
        custom = parse_channel_layout(
            struct.pack(">III", 0, 0, 2) + descriptions, 2
        )
        self.assertEqual(custom.channel_names, ("Left", "Right"))
        self.assertEqual(custom.source, "descriptions")

    def test_duplicate_description_chunk_is_rejected(self):
        desc = chunk(b"desc", description_payload())
        data = chunk(b"data", struct.pack(">I", 0) + b"\x00" * 4)
        raw = build_caf(description=b"", chunks=[desc, desc, data])
        with self.assertRaisesRegex(CafFormatError, "more than once"):
            CAFFile(raw)

    def test_truncated_chunk_is_rejected(self):
        raw = b"caff" + struct.pack(">HH", 1, 0)
        raw += struct.pack(">4sq", b"desc", 32) + b"\x00" * 8
        with self.assertRaisesRegex(CafFormatError, "extends past EOF"):
            CAFFile(raw)

    def test_big_endian_float_and_24_bit_pcm(self):
        float32 = AudioDescription(
            48000.0, "lpcm", 0x1, 8, 1, 2, 32, b"\x00" * 8
        )
        self.assertEqual(
            PCMDecoder(float32).decode_frame(
                bytes.fromhex("3f800000bf800000")
            ),
            (1.0, -1.0),
        )
        packed24 = AudioDescription(
            48000.0, "lpcm", 0, 6, 1, 2, 24, b"\x00" * 8
        )
        self.assertEqual(
            PCMDecoder(packed24).decode_frame(
                bytes.fromhex("123456fffffe")
            ),
            (0x123456, -2),
        )

    def test_header_analysis_does_not_require_pcm_decoding(self):
        desc = struct.pack(
            ">d4sIIIII",
            48000.0,
            b"aac ",
            2,
            0,
            1024,
            2,
            0,
        )
        pakt = struct.pack(">qqii", 1, 1000, 0, 24)
        data = struct.pack(">I", 0) + b"\x00" * 16
        raw = build_caf(
            description=b"",
            chunks=[
                chunk(b"desc", desc),
                chunk(b"pakt", pakt),
                chunk(b"data", data),
            ],
        )
        analysis = CAFFile(raw).analysis()
        self.assertEqual(analysis["audio_description"]["sample_kind"], "compressed")
        self.assertEqual(analysis["frame_count"], 1000)
        self.assertEqual(analysis["sample_frames"], [])
        self.assertIn("requires lpcm", analysis["sample_extraction_error"])
        high_aligned24 = AudioDescription(
            48000.0, "lpcm", 0, 8, 1, 2, 24, b"\x00" * 8
        )
        self.assertEqual(
            PCMDecoder(high_aligned24).decode_frame(
                bytes.fromhex("12345600fffffe00")
            ),
            (0x123456, -2),
        )


if __name__ == "__main__":
    unittest.main()
