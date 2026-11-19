import math
import unittest

from caf_audio.errors import ExtendedFloatError
from caf_audio.extended import (
    decode_extended,
    decode_extended_hex,
    decode_parts,
    encode_extended,
)


class ExtendedFloatTests(unittest.TestCase):
    REFERENCES = {
        8000.0: "400bfa00000000000000",
        11025.0: "400cac44000000000000",
        22050.0: "400dac44000000000000",
        32000.0: "400dfa00000000000000",
        44100.0: "400eac44000000000000",
        48000.0: "400ebb80000000000000",
        96000.0: "400fbb80000000000000",
        192000.0: "4010bb80000000000000",
    }

    def test_published_sample_rate_references(self):
        for value, expected_hex in self.REFERENCES.items():
            encoded = encode_extended(value)
            self.assertEqual(encoded.hex(), expected_hex)
            self.assertEqual(decode_extended_hex(expected_hex), value)

    def test_exact_integer_roundtrip(self):
        for value in range(0, 192000 + 1):
            encoded = encode_extended(float(value))
            self.assertEqual(decode_extended(encoded), float(value))

    def test_negative_zero_and_negative_values(self):
        negative_zero = encode_extended(-0.0)
        self.assertEqual(negative_zero.hex(), "80000000000000000000")
        self.assertEqual(decode_extended(negative_zero), 0.0)
        self.assertEqual(decode_extended(encode_extended(-44100.0)), -44100.0)

    def test_specials(self):
        self.assertTrue(math.isinf(decode_extended(encode_extended(math.inf))))
        self.assertTrue(math.isnan(decode_extended(encode_extended(math.nan))))
        self.assertEqual(
            decode_parts(bytes.fromhex("00000000000000000000")).classification,
            "zero",
        )

    def test_invalid_length(self):
        with self.assertRaisesRegex(ExtendedFloatError, "10 bytes"):
            decode_extended(b"\x00" * 9)


if __name__ == "__main__":
    unittest.main()
