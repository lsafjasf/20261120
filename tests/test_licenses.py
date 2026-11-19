from __future__ import annotations

import base64
import unittest

from drm_sim.licenses import (
    LicenseServer,
    build_clearkey_request,
    build_playready_request,
    extract_playready_license,
)

OLD = "00112233445566778899aabbccddeeff"
NEW = "ffeeddccbbaa99887766554433221100"
OLD_KEY = bytes.fromhex("000102030405060708090a0b0c0d0e0f")
NEW_KEY = bytes.fromhex("101112131415161718191a1b1c1d1e1f")


def _request(server: LicenseServer, provider: str, kid: str):
    if provider == "clearkey":
        response = server.handle_clearkey(build_clearkey_request(kid))
        return response, response.json_body() if response.status_code == 200 else None
    response = server.handle_playready(build_playready_request(kid))
    return response, extract_playready_license(response) if response.status_code == 200 else None


class LicenseTests(unittest.TestCase):
    def test_rotation_invalidates_old_license_for_both_protocols(self) -> None:
        for provider in ("clearkey", "playready"):
            with self.subTest(provider=provider):
                server = LicenseServer()
                server.register_key(provider, OLD, OLD_KEY)
                response, license_payload = _request(server, provider, OLD)
                self.assertEqual(response.status_code, 200)
                self.assertIsNotNone(license_payload)
                license_id = (
                    license_payload["license_id"]
                    if provider == "clearkey"
                    else license_payload["LicenseID"]
                )
                self.assertTrue(server.validate(license_id).valid)

                revoked = server.rotate(provider, OLD, NEW, NEW_KEY)
                self.assertEqual([record.license_id for record in revoked], [license_id])
                status = server.validate(license_id)
                self.assertFalse(status.valid)
                self.assertEqual(status.status, "REVOKED")
                self.assertEqual(status.reason, "KID_ROTATED")

                old_response, _ = _request(server, provider, OLD)
                self.assertEqual(old_response.status_code, 403)
                new_response, new_payload = _request(server, provider, NEW)
                self.assertEqual(new_response.status_code, 200)
                new_id = (
                    new_payload["license_id"]
                    if provider == "clearkey"
                    else new_payload["LicenseID"]
                )
                self.assertTrue(server.validate(new_id).valid)

    def test_license_payloads_carry_expected_key_and_kid(self) -> None:
        server = LicenseServer()
        server.register_key("clearkey", OLD, OLD_KEY)
        clear_response, clear_payload = _request(server, "clearkey", OLD)
        self.assertEqual(clear_response.status_code, 200)
        self.assertEqual(
            base64.urlsafe_b64decode(clear_payload["keys"][0]["k"] + "=="),
            OLD_KEY,
        )

        server.register_key("playready", OLD, OLD_KEY)
        play_response, play_payload = _request(server, "playready", OLD)
        self.assertEqual(play_response.status_code, 200)
        self.assertEqual(play_payload["KID"], OLD)
        self.assertEqual(base64.b64decode(play_payload["ContentKey"]), OLD_KEY)

    def test_rotation_event_is_atomic_and_emits_state_change(self) -> None:
        server = LicenseServer()
        server.register_key("clearkey", OLD, OLD_KEY)
        _, payload = _request(server, "clearkey", OLD)
        license_id = payload["license_id"]
        server.rotate("clearkey", OLD, NEW, NEW_KEY)
        events = server.events
        begin = next(i for i, event in enumerate(events) if event["event"] == "rotation.begin")
        changed = next(
            i for i, event in enumerate(events) if event["event"] == "license.state_changed"
        )
        complete = next(
            i for i, event in enumerate(events) if event["event"] == "rotation.complete"
        )
        self.assertLess(begin, changed)
        self.assertLess(changed, complete)
        self.assertEqual(events[changed]["license_id"], license_id)
        self.assertEqual(events[changed]["after"], "REVOKED")
        self.assertTrue(events[changed]["atomic_with_rotation"])

    def test_malformed_requests_return_400(self) -> None:
        server = LicenseServer()
        clear = server.handle_clearkey(b"{}")
        play = server.handle_playready(b"<not-valid")
        self.assertEqual(clear.status_code, 400)
        self.assertEqual(play.status_code, 400)


if __name__ == "__main__":
    unittest.main()
