"""End-to-end deterministic demonstration and artifact writer."""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .licenses import (
    HTTPResponse,
    LicenseServer,
    build_clearkey_request,
    build_playready_request,
    extract_playready_license,
)
from .media import (
    audit_rewrite,
    changed_ranges,
    default_kid,
    insert_top_level_after,
    patch_default_kid,
    replace_top_level_box,
)
from .parameter_sets import (
    MissingParameterSetError,
    inspect_parameter_sets,
    require_parameter_sets,
)
from .player import probe_media
from .pssh import PSSHBox, build_pssh, kid_hex, pssh_summary, rewrite_pssh

ROOT = Path(__file__).resolve().parents[1]
SOURCE_MP4 = ROOT / "fixtures" / "source_cenc.mp4"
HEVC_FIXTURE = ROOT / "fixtures" / "hevc_vps_sps_no_pps.h265"
OLD_KID = "00112233445566778899aabbccddeeff"
NEW_KID = "ffeeddccbbaa99887766554433221100"
OLD_KEY = bytes.fromhex("000102030405060708090a0b0c0d0e0f")
NEW_KEY = bytes.fromhex("101112131415161718191a1b1c1d1e1f")
PROVIDERS = ("clearkey", "playready")


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def _body_text(response: HTTPResponse) -> str:
    return response.body.decode("utf-8", errors="replace")


def _request_license(
    server: LicenseServer, provider: str, kid: str
) -> tuple[HTTPResponse, str | None, dict[str, Any] | None, bytes]:
    if provider == "clearkey":
        request = build_clearkey_request(kid)
        response = server.handle_clearkey(request)
        if response.status_code == 200:
            payload = response.json_body()
            return response, payload["license_id"], payload, request
        return response, None, response.json_body(), request
    request = build_playready_request(kid)
    response = server.handle_playready(request)
    if response.status_code == 200:
        payload = extract_playready_license(response)
        return response, payload["LicenseID"], payload, request
    return response, None, None, request


def _pssh_byte_diff(before: bytes, after: bytes) -> dict[str, Any]:
    differences = changed_ranges(before, after)
    for item in differences:
        offset = int(item["offset"])
        item["field"] = (
            "header.kid[0]" if 32 <= offset < 48 else "provider_payload.kid"
        )
    return {
        "before_size": len(before),
        "after_size": len(after),
        "same_length": len(before) == len(after),
        "changed_byte_count": sum(int(item["length"]) for item in differences),
        "changed_ranges": differences,
    }


def _run_provider(
    server: LicenseServer,
    provider: str,
    source: bytes,
    output_dir: Path,
) -> dict[str, Any]:
    old_box = build_pssh(provider, OLD_KID)
    before = insert_top_level_after(source, b"ftyp", old_box.serialize())
    before_path = output_dir / f"{provider}_before.mp4"
    before_path.write_bytes(before)

    before_probe = probe_media(before, before_path)
    before_probe.require_ready()
    if kid_hex(default_kid(before)) != OLD_KID:
        raise RuntimeError("source fixture default KID is not the expected old KID")

    initial_response, old_license_id, initial_license, initial_request = _request_license(
        server, provider, OLD_KID
    )
    if initial_response.status_code != 200 or old_license_id is None:
        raise RuntimeError(f"{provider} initial license request failed")
    before_validation = asdict(server.validate(old_license_id))

    request_suffix = "json" if provider == "clearkey" else "xml"
    (output_dir / f"{provider}_initial_request.{request_suffix}").write_bytes(
        initial_request
    )
    (output_dir / f"{provider}_initial_response.{request_suffix}").write_bytes(
        initial_response.body
    )

    revoked = server.rotate(provider, OLD_KID, NEW_KID, NEW_KEY)
    after_rotation_validation = asdict(server.validate(old_license_id))
    if after_rotation_validation["valid"]:
        raise RuntimeError("old license remained valid after rotation")

    old_response, _, old_error, old_request = _request_license(
        server, provider, OLD_KID
    )
    if old_response.status_code != 403:
        raise RuntimeError("old KID request was not rejected after rotation")

    new_response, new_license_id, new_license, new_request = _request_license(
        server, provider, NEW_KID
    )
    if new_response.status_code != 200 or new_license_id is None:
        raise RuntimeError("new KID license request failed")
    new_validation = asdict(server.validate(new_license_id))
    (output_dir / f"{provider}_old_kid_after_rotation_request.{request_suffix}").write_bytes(
        old_request
    )
    (
        output_dir / f"{provider}_old_kid_after_rotation_response.{request_suffix}"
    ).write_bytes(old_response.body)
    (output_dir / f"{provider}_new_kid_request.{request_suffix}").write_bytes(
        new_request
    )
    (output_dir / f"{provider}_new_kid_response.{request_suffix}").write_bytes(
        new_response.body
    )

    new_box = rewrite_pssh(old_box, NEW_KID)
    after = replace_top_level_box(before, 0, new_box.serialize())
    after = patch_default_kid(after, NEW_KID)
    after_path = output_dir / f"{provider}_after.mp4"
    after_path.write_bytes(after)

    after_probe = probe_media(after, after_path)
    after_probe.require_ready()
    if after_probe.default_kid != NEW_KID:
        raise RuntimeError("rewritten PSSH KID does not match the new default KID")

    before_pssh = old_box.serialize()
    after_pssh = new_box.serialize()
    (output_dir / f"{provider}_pssh_before.bin").write_bytes(before_pssh)
    (output_dir / f"{provider}_pssh_after.bin").write_bytes(after_pssh)
    pssh_diff = _pssh_byte_diff(before_pssh, after_pssh)
    whole_file_audit = audit_rewrite(before, after, provider)
    if not whole_file_audit["only_license_fields_changed"]:
        raise RuntimeError("rewrite changed bytes outside license fields")
    if not whole_file_audit["media_payload"]["unchanged"]:
        raise RuntimeError("rewrite changed media payload bytes")

    ffprobe_before = before_probe.ffprobe or {}
    ffprobe_after = after_probe.ffprobe or {}
    ffprobe_unchanged = (
        ffprobe_before.get("stream_signature") == ffprobe_after.get("stream_signature")
        if ffprobe_before.get("available") and ffprobe_after.get("available")
        else None
    )

    state_sequence = [
        f"ISSUED_OLD:{before_validation['status']}",
        "ROTATION:old ACTIVE -> REVOKED (atomic)",
        f"VALIDATE_OLD:INVALID/{after_rotation_validation['reason']}",
        f"REQUEST_OLD:HTTP {old_response.status_code}",
        f"REQUEST_NEW:HTTP {new_response.status_code}",
        f"ISSUED_NEW:{new_validation['status']}",
    ]
    return {
        "provider": provider,
        "kid_rotation": {
            "old_kid": OLD_KID,
            "new_kid": NEW_KID,
            "generation_before": 1,
            "generation_after": 2,
        },
        "initial_request": {
            "status_code": initial_response.status_code,
            "license_id": old_license_id,
            "request": initial_request.decode("utf-8"),
            "response": initial_license,
        },
        "initial_license_validation": before_validation,
        "rotation": {
            "revoked_license_ids": [record.license_id for record in revoked],
            "atomic_with_rotation": True,
            "old_license_valid_immediately_after": after_rotation_validation["valid"],
        },
        "old_license_after_rotation": after_rotation_validation,
        "old_request_after_rotation": {
            "status_code": old_response.status_code,
            "error": old_error,
            "request": old_request.decode("utf-8"),
            "response_body": _body_text(old_response),
        },
        "new_request": {
            "status_code": new_response.status_code,
            "license_id": new_license_id,
            "request": new_request.decode("utf-8"),
            "license": new_license,
        },
        "new_license_validation": new_validation,
        "state_sequence": state_sequence,
        "pssh": {
            "before": pssh_summary(PSSHBox.from_bytes(before_pssh)),
            "after": pssh_summary(PSSHBox.from_bytes(after_pssh)),
            "diff": pssh_diff,
        },
        "player_after_rewrite": after_probe.to_dict(),
        "player_before_rewrite": before_probe.to_dict(),
        "ffprobe_streams_unchanged": ffprobe_unchanged,
        "whole_file_byte_audit": whole_file_audit,
    }


def _ensure_fixtures() -> None:
    missing = [path for path in (SOURCE_MP4, HEVC_FIXTURE) if not path.exists()]
    if not missing:
        return
    script = ROOT / "scripts" / "build_fixtures.py"
    completed = subprocess.run(
        [sys.executable, str(script)],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            "fixtures are missing and could not be generated: " + completed.stderr
        )


def _short_digest(value: str) -> str:
    return f"{value[:12]}...{value[-8:]}"


def _event_text(event: dict[str, Any]) -> str:
    details = []
    if "provider" in event:
        details.append(str(event["provider"]))
    if "license_id" in event:
        details.append(str(event["license_id"]))
    if "kid" in event:
        details.append(f"kid={event['kid']}")
    if "before" in event and "after" in event:
        details.append(f"{event['before']}->{event['after']}")
    if "before" not in event and event.get("valid") is not None:
        details.append(f"valid={event['valid']}")
    if "http_status" in event:
        details.append(f"HTTP {event['http_status']}")
    if "code" in event:
        details.append(str(event["code"]))
    return " ".join(details)


def _markdown_report(report: dict[str, Any]) -> str:
    lines = [
        "# ClearKey / PlayReady KID rotation report",
        "",
        "> Deterministic simulation using test keys. It is not a production DRM server.",
        "",
        "## Checks",
    ]
    checks = report["checks"]
    for name, passed in checks.items():
        lines.append(f"- [{'PASS' if passed else 'FAIL'}] `{name}`")
    lines.extend(["", "## License state sequence", ""])
    lines.append("| seq | event | details |")
    lines.append("| ---: | --- | --- |")
    for event in report["license_state_sequence"]["events"]:
        lines.append(
            f"| {event['sequence']} | `{event['event']}` | {_event_text(event)} |"
        )
    for provider in PROVIDERS:
        result = report["providers"][provider]
        lines.extend(
            [
                "",
                f"## {provider}",
                "",
                f"- Old KID: `{result['kid_rotation']['old_kid']}`",
                f"- New KID: `{result['kid_rotation']['new_kid']}`",
                f"- Old license after rotation: `{result['old_license_after_rotation']['status']}`, "
                f"valid=`{result['old_license_after_rotation']['valid']}`",
                f"- Old KID request after rotation: HTTP {result['old_request_after_rotation']['status_code']}",
                f"- New license: `{result['new_license_validation']['status']}`",
                f"- Player parse after rewrite: `parsed={result['player_after_rewrite']['parsed']}`",
                f"- PSSH KID == tenc.default_KID: "
                f"`{result['player_after_rewrite']['header_kid_matches_default'] and result['player_after_rewrite']['payload_kid_matches_default']}`",
                f"- ffprobe streams unchanged: `{result['ffprobe_streams_unchanged']}`",
                "",
                "### PSSH byte diff",
                "",
                "| offset | length | before hex | after hex | field |",
                "| ---: | ---: | --- | --- | --- |",
            ]
        )
        for change in result["pssh"]["diff"]["changed_ranges"]:
            lines.append(
                f"| {change['offset']} | {change['length']} | `{change['before_hex']}` "
                f"| `{change['after_hex']}` | `{change['field']}` |"
            )
        audit = result["whole_file_byte_audit"]
        lines.extend(
            [
                "",
                "### Whole-file audit",
                "",
                f"- Changed bytes: `{audit['changed_byte_count']}`",
                f"- Only PSSH/tenc license fields: `{audit['only_license_fields_changed']}`",
                f"- Bytes changed outside license fields: `{len(audit['changed_outside_license_fields'])}`",
                f"- Top-level layout identical: `{audit['layout_identical']}`",
                f"- mdat SHA-256 before: `{_short_digest(audit['media_payload']['before']['sha256'])}`",
                f"- mdat SHA-256 after: `{_short_digest(audit['media_payload']['after']['sha256'])}`",
                f"- mdat unchanged: `{audit['media_payload']['unchanged']}`",
                "",
                "| abs offset | length | field | box | before hex | after hex |",
                "| ---: | ---: | --- | --- | --- | --- |",
            ]
        )
        for change in audit["changed_ranges"]:
            lines.append(
                f"| {change['offset']} | {change['length']} "
                f"| {', '.join(change['license_fields'])} | `{change['top_level_box']}` "
                f"| `{change['before_hex']}` | `{change['after_hex']}` |"
            )
    hevc = report["parameter_sets"]
    lines.extend(
        [
            "",
            "## HEVC VPS/SPS-only stream",
            "",
            f"- NAL types: `{hevc['nal_types']}`",
            f"- Complete: `{hevc['complete']}`",
            f"- Error code: `{hevc['error_code']}`",
            f"- Message: {hevc['message']}",
            "",
            "## Artifacts",
            "",
        ]
    )
    for name in report["artifacts"]:
        lines.append(f"- `{name}`")
    return "\n".join(lines) + "\n"


def run_demo(output_dir: str | Path = ROOT / "artifacts") -> dict[str, Any]:
    _ensure_fixtures()
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    source = SOURCE_MP4.read_bytes()

    server = LicenseServer()
    for provider in PROVIDERS:
        server.register_key(provider, OLD_KID, OLD_KEY)

    providers = {
        provider: _run_provider(server, provider, source, output_dir)
        for provider in PROVIDERS
    }

    hevc_data = HEVC_FIXTURE.read_bytes()
    hevc_report = inspect_parameter_sets(hevc_data, "hevc")
    missing_pps_error: dict[str, Any] | None = None
    try:
        require_parameter_sets(hevc_data, "hevc")
    except MissingParameterSetError as exc:
        missing_pps_error = {
            "type": type(exc).__name__,
            "code": getattr(exc, "code", None),
            "message": str(exc),
        }
    if missing_pps_error is None:
        raise RuntimeError("VPS/SPS-only stream did not trigger a missing-PPS error")
    hevc_payload = hevc_report.to_dict()
    hevc_payload["raised_error"] = missing_pps_error

    checks = {
        "old_license_invalid_immediately": all(
            not result["old_license_after_rotation"]["valid"]
            for result in providers.values()
        ),
        "old_kid_request_rejected_403": all(
            result["old_request_after_rotation"]["status_code"] == 403
            for result in providers.values()
        ),
        "new_license_active": all(
            result["new_license_validation"]["valid"] for result in providers.values()
        ),
        "rewritten_pssh_player_parse": all(
            result["player_after_rewrite"]["parsed"] for result in providers.values()
        ),
        "rewritten_kid_matches_default_kid": all(
            result["player_after_rewrite"]["header_kid_matches_default"]
            and result["player_after_rewrite"]["payload_kid_matches_default"]
            for result in providers.values()
        ),
        "only_license_fields_changed": all(
            result["whole_file_byte_audit"]["only_license_fields_changed"]
            for result in providers.values()
        ),
        "media_payload_untouched": all(
            result["whole_file_byte_audit"]["media_payload"]["unchanged"]
            for result in providers.values()
        ),
        "hevc_missing_pps_detected": (
            hevc_payload["nal_types"] == [32, 33]
            and missing_pps_error["code"] == "MISSING_PPS"
        ),
    }
    if not all(value for value in checks.values() if value is not None):
        raise RuntimeError(f"one or more verification checks failed: {checks}")

    report: dict[str, Any] = {
        "simulation": {
            "source_fixture": str(SOURCE_MP4.relative_to(ROOT)),
            "old_kid": OLD_KID,
            "new_kid": NEW_KID,
            "note": (
                "Synthetic test keys and in-memory license endpoints. Media samples are "
                "not re-encrypted; PSSH/tenc metadata rotation is audited separately."
            ),
        },
        "checks": checks,
        "providers": providers,
        "license_state_sequence": {
            "clock": "logical sequence only; invalidation occurs synchronously inside rotate()",
            "events": server.events,
        },
        "parameter_sets": hevc_payload,
        "artifacts": [],
    }

    _write_json(output_dir / "license_state_sequence.json", report["license_state_sequence"])
    _write_json(
        output_dir / "byte_diffs.json",
        {
            provider: {
                "pssh": result["pssh"]["diff"],
                "whole_file": result["whole_file_byte_audit"],
            }
            for provider, result in providers.items()
        },
    )
    _write_json(
        output_dir / "player_probe.json",
        {
            provider: {
                "before": result["player_before_rewrite"],
                "after": result["player_after_rewrite"],
            }
            for provider, result in providers.items()
        },
    )
    _write_json(output_dir / "parameter_sets.json", hevc_payload)

    artifact_names = sorted(
        path.name
        for path in output_dir.iterdir()
        if path.is_file()
        and path.name not in {"report.json", "report.md", "workspace.json"}
    )
    report["artifacts"] = artifact_names + ["report.json", "report.md"]
    _write_json(output_dir / "report.json", report)
    (output_dir / "report.md").write_text(_markdown_report(report), encoding="utf-8")
    return report
