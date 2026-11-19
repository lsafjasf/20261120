from __future__ import annotations

import unittest
from pathlib import Path

from drm_sim.demo import OLD_KID, NEW_KID, SOURCE_MP4
from drm_sim.media import (
    audit_rewrite,
    default_kid,
    insert_top_level_after,
    parse_pssh_boxes,
    patch_default_kid,
    replace_top_level_box,
    top_level_boxes,
)
from drm_sim.player import probe_media
from drm_sim.pssh import build_pssh, rewrite_pssh


class MediaPlayerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = SOURCE_MP4.read_bytes()

    def test_rewrite_preserves_media_and_matches_default_kid(self) -> None:
        for provider in ("clearkey", "playready"):
            with self.subTest(provider=provider):
                old_box = build_pssh(provider, OLD_KID)
                before = insert_top_level_after(
                    self.source, b"ftyp", old_box.serialize()
                )
                self.assertEqual(default_kid(before).hex(), OLD_KID)
                before_probe = probe_media(before)
                before_probe.require_ready()

                new_box = rewrite_pssh(old_box, NEW_KID)
                after = replace_top_level_box(before, 0, new_box.serialize())
                after = patch_default_kid(after, NEW_KID)
                after_probe = probe_media(after)
                after_probe.require_ready()

                self.assertEqual(default_kid(after).hex(), NEW_KID)
                self.assertEqual(
                    parse_pssh_boxes(after)[0].provider_kids()[0].hex(), NEW_KID
                )
                self.assertTrue(after_probe.header_kid_matches_default)
                self.assertTrue(after_probe.payload_kid_matches_default)
                self.assertEqual(
                    [(box.type, box.start, box.size) for box in top_level_boxes(before)],
                    [(box.type, box.start, box.size) for box in top_level_boxes(after)],
                )

                audit = audit_rewrite(before, after, provider)
                self.assertTrue(audit["only_license_fields_changed"])
                self.assertEqual(audit["changed_outside_license_fields"], [])
                self.assertTrue(audit["media_payload"]["unchanged"])
                self.assertTrue(audit["layout_identical"])
                self.assertEqual(audit["default_kid_after"], NEW_KID)

    def test_require_ready_rejects_kid_mismatch(self) -> None:
        old_box = build_pssh("clearkey", OLD_KID)
        media = insert_top_level_after(self.source, b"ftyp", old_box.serialize())
        probe = probe_media(media)
        self.assertTrue(probe.parsed)
        probe.default_kid = NEW_KID
        probe.header_kid_matches_default = False
        with self.assertRaisesRegex(ValueError, "header KID"):
            probe.require_ready()


if __name__ == "__main__":
    unittest.main()
