from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from wavpack_blocks import parse_header, scan_file, verify_metadata_and_checksum


FIXTURES = ROOT / "fixtures"


class WavPackBlockTests(unittest.TestCase):
    def scan(self, name: str, with_pcm: bool = True):
        pcm_path = FIXTURES / f"{name}.decoded.raw"
        pcm = pcm_path.read_bytes() if with_pcm else None
        return scan_file(FIXTURES / f"{name}.wv", pcm)

    def test_header_fields_and_modes(self):
        cases = {
            "mono": ("mono", 1, False),
            "stereo": ("stereo", 2, False),
            "hybrid": ("hybrid", 2, True),
        }
        for name, (mode, channels, hybrid) in cases.items():
            with self.subTest(name=name):
                header = parse_header(
                    (FIXTURES / f"{name}.wv").read_bytes(), 0
                )
                self.assertEqual(header.block_id, b"wvpk")
                self.assertEqual(header.mode, mode)
                self.assertEqual(header.channels, channels)
                self.assertEqual(header.hybrid, hybrid)
                self.assertEqual(header.sample_rate, 44100)
                self.assertEqual(header.total_samples, 2200)

    def test_clean_files_have_no_checksum_failures(self):
        for name in ("mono", "stereo", "hybrid"):
            with self.subTest(name=name):
                result = self.scan(name)
                self.assertEqual(result.checksum_failures, 0)
                self.assertEqual(result.skipped_blocks, 0)
                self.assertEqual(
                    result.recovered_sample_indices,
                    [0, 512, 1024, 1536, 1868],
                )
                self.assertTrue(result.total_samples_consistent)
                self.assertEqual(result.declared_total_samples, 2200)
                self.assertTrue(
                    all(record.header_crc_ok for record in result.records)
                )
                self.assertTrue(
                    all(record.metadata.checksum_ok for record in result.records)
                )

    def test_corrupted_payload_is_skipped_and_resynchronized(self):
        clean = self.scan("stereo")
        damaged = self.scan("corrupt_stereo")

        self.assertEqual(damaged.checksum_failures, 1)
        self.assertEqual(damaged.skipped_blocks, 1)
        bad = next(record for record in damaged.records if not record.accepted)
        self.assertEqual(bad.header.offset, 0x58E)
        self.assertEqual(bad.header.block_index, 1024)
        self.assertEqual(bad.resume_offset, 0x836)
        self.assertEqual(bad.skipped_bytes, 0x836 - 0x58E)
        self.assertFalse(bad.header_crc_ok)
        self.assertFalse(bad.metadata.checksum_ok)

        later_clean = clean.records[3:]
        later_damaged = damaged.records[3:]
        self.assertEqual(
            [record.header.block_index for record in later_damaged],
            [1536, 1868],
        )
        self.assertEqual(
            [record.header.block_index for record in later_damaged],
            [record.header.block_index for record in later_clean],
        )
        self.assertTrue(all(record.accepted for record in later_damaged))
        self.assertTrue(all(record.header_crc_ok for record in later_damaged))
        self.assertEqual(damaged.declared_total_samples, 2200)
        self.assertTrue(damaged.total_samples_consistent)

    def test_broken_length_does_not_hide_following_blocks(self):
        result = self.scan("corrupt_length_stereo")
        self.assertEqual(result.checksum_failures, 1)
        bad = next(record for record in result.records if not record.accepted)
        self.assertEqual(bad.header.offset, 0x58E)
        self.assertEqual(bad.resume_offset, 0x836)
        self.assertEqual(
            [record.header.block_index for record in result.accepted_blocks],
            [0, 512, 1536, 1868],
        )
        self.assertEqual(result.declared_total_samples, 2200)

    def test_metadata_checksum_detects_damage_without_decoding(self):
        data = (FIXTURES / "corrupt_stereo.wv").read_bytes()
        header = parse_header(data, 0x58E)
        check = verify_metadata_and_checksum(data, header)
        self.assertTrue(check.valid)
        self.assertTrue(check.checksum_present)
        self.assertFalse(check.checksum_ok)


if __name__ == "__main__":
    unittest.main()
