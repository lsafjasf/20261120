from __future__ import annotations

import base64
import unittest

from drm_sim.media import changed_ranges
from drm_sim.pssh import (
    PSSHBox,
    PSSHError,
    build_pssh,
    clearkey_kid_b64,
    clear_key_payload_kids,
    playready_header_xml,
    playready_payload_kids,
    rewrite_pssh,
)

OLD = "00112233445566778899aabbccddeeff"
NEW = "ffeeddccbbaa99887766554433221100"


class PSSHTests(unittest.TestCase):
    def test_clearkey_round_trip_and_kid_layers(self) -> None:
        box = build_pssh("clearkey", OLD)
        decoded = PSSHBox.from_bytes(box.serialize())
        self.assertEqual(decoded.system_name, "clearkey")
        self.assertEqual(decoded.kids[0].hex(), OLD)
        self.assertEqual(decoded.provider_kids()[0].hex(), OLD)
        self.assertEqual(
            clear_key_payload_kids(decoded.data),
            (bytes.fromhex(OLD),),
        )
        self.assertEqual(clearkey_kid_b64(OLD), "ABEiM0RVZneImaq7zN3u_w")

    def test_playready_object_and_utf16_xml(self) -> None:
        box = build_pssh("playready", OLD)
        decoded = PSSHBox.from_bytes(box.serialize())
        self.assertEqual(decoded.system_name, "playready")
        self.assertEqual(decoded.kids[0].hex(), OLD)
        self.assertEqual(playready_payload_kids(decoded.data)[0].hex(), OLD)
        xml = playready_header_xml(decoded.data)
        self.assertIn('version="4.0.0.0"', xml)
        self.assertIn(
            base64.b64encode(bytes.fromhex(OLD)).decode("ascii"),
            xml,
        )

    def test_rewrite_changes_only_kid_regions(self) -> None:
        for provider in ("clearkey", "playready"):
            with self.subTest(provider=provider):
                old = build_pssh(provider, OLD)
                new = rewrite_pssh(old, NEW)
                self.assertEqual(len(old.serialize()), len(new.serialize()))
                decoded = PSSHBox.from_bytes(new.serialize())
                self.assertEqual(decoded.kids, (bytes.fromhex(NEW),))
                self.assertEqual(decoded.provider_kids(), (bytes.fromhex(NEW),))
                differences = changed_ranges(old.serialize(), new.serialize())
                self.assertTrue(differences)
                self.assertIn(32, [item["offset"] for item in differences])
                for item in differences:
                    start = int(item["offset"])
                    self.assertGreaterEqual(start, 32)
                    if start != 32:
                        self.assertGreaterEqual(start, 52)
                self.assertEqual(
                    [item for item in differences if item["offset"] < 32],
                    [],
                )

    def test_malformed_pssh_is_rejected(self) -> None:
        raw = build_pssh("clearkey", OLD).serialize()
        with self.assertRaises(PSSHError):
            PSSHBox.from_bytes(raw[:-1])
        with self.assertRaises(PSSHError):
            PSSHBox.from_bytes(b"not-a-pssh-box")


if __name__ == "__main__":
    unittest.main()
