"""In-memory ClearKey and PlayReady license server simulation."""

from __future__ import annotations

import base64
import json
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from typing import Any

from .pssh import (
    as_kid,
    build_playready_data,
    clearkey_kid_b64,
    kid_hex,
    playready_payload_kids,
)

STATUS_ACTIVE = "ACTIVE"
STATUS_REVOKED = "REVOKED"
PLAYREADY_PROTOCOL_NS = "http://schemas.microsoft.com/DRM/2007/03/protocols"


class LicenseError(ValueError):
    """Raised for malformed license requests."""


@dataclass
class LicenseRecord:
    license_id: str
    provider: str
    kid_hex: str
    content_key_hex: str
    issued_sequence: int
    generation: int
    status: str = STATUS_ACTIVE
    invalidated_sequence: int | None = None
    invalidation_reason: str | None = None

    def public_dict(self, include_key: bool = False) -> dict[str, Any]:
        result = asdict(self)
        if not include_key:
            result.pop("content_key_hex")
        return result


@dataclass(frozen=True)
class LicenseStatus:
    license_id: str
    provider: str
    kid_hex: str
    valid: bool
    status: str
    generation: int
    reason: str


@dataclass(frozen=True)
class HTTPResponse:
    status_code: int
    headers: dict[str, str]
    body: bytes

    def json_body(self) -> dict[str, Any]:
        return json.loads(self.body.decode("utf-8"))


class LicenseServer:
    def __init__(self) -> None:
        self._active: dict[tuple[str, str], bytes] = {}
        self._generation: dict[str, int] = {}
        self.records: dict[str, LicenseRecord] = {}
        self.events: list[dict[str, Any]] = []
        self._sequence = 0
        self._request_counter = 0
        self._license_counters: dict[str, int] = {}

    def _emit(self, event: str, **fields: Any) -> int:
        self._sequence += 1
        row = {"sequence": self._sequence, "event": event, **fields}
        self.events.append(row)
        return self._sequence

    def _next_request_id(self) -> str:
        self._request_counter += 1
        return f"req-{self._request_counter:03d}"

    def register_key(self, provider: str, kid: str | bytes, key: bytes) -> None:
        if len(key) != 16:
            raise LicenseError("content key must be 16 bytes")
        provider = provider.lower()
        self._active[(provider, kid_hex(kid))] = key
        self._generation.setdefault(provider, 1)
        self._emit(
            "key.registered",
            provider=provider,
            kid=kid_hex(kid),
            generation=self._generation[provider],
        )

    def _issue(
        self, provider: str, kid: str | bytes, request_id: str
    ) -> LicenseRecord:
        normalized_kid = kid_hex(kid)
        key = self._active.get((provider, normalized_kid))
        if key is None:
            raise LicenseError(f"no active {provider} content key for KID {normalized_kid}")
        counter = self._license_counters.get(provider, 0) + 1
        self._license_counters[provider] = counter
        issue_sequence = self._sequence + 1
        record = LicenseRecord(
            license_id=f"lic-{provider}-{counter:03d}",
            provider=provider,
            kid_hex=normalized_kid,
            content_key_hex=key.hex(),
            issued_sequence=issue_sequence,
            generation=self._generation[provider],
        )
        self.records[record.license_id] = record
        self._emit(
            "license.issued",
            request_id=request_id,
            license_id=record.license_id,
            provider=provider,
            kid=normalized_kid,
            generation=record.generation,
            status=record.status,
        )
        return record

    def rotate(
        self,
        provider: str,
        old_kid: str | bytes,
        new_kid: str | bytes,
        new_key: bytes,
    ) -> list[LicenseRecord]:
        provider = provider.lower()
        old_hex = kid_hex(old_kid)
        new_hex = kid_hex(new_kid)
        if old_hex == new_hex:
            raise LicenseError("rotated KID must differ from the old KID")
        if len(new_key) != 16:
            raise LicenseError("content key must be 16 bytes")
        if (provider, old_hex) not in self._active:
            raise LicenseError(f"old KID {old_hex} is not active for {provider}")

        affected = [
            record
            for record in self.records.values()
            if record.provider == provider
            and record.kid_hex == old_hex
            and record.status == STATUS_ACTIVE
        ]
        self._emit(
            "rotation.begin",
            provider=provider,
            old_kid=old_hex,
            new_kid=new_hex,
            affected_licenses=len(affected),
        )
        del self._active[(provider, old_hex)]
        self._generation[provider] += 1
        for record in affected:
            record.status = STATUS_REVOKED
            record.invalidated_sequence = self._sequence + 1
            record.invalidation_reason = "KID_ROTATED"
            invalidation_sequence = self._emit(
                "license.state_changed",
                license_id=record.license_id,
                provider=provider,
                kid=old_hex,
                before=STATUS_ACTIVE,
                after=STATUS_REVOKED,
                reason="KID_ROTATED",
                atomic_with_rotation=True,
            )
            if invalidation_sequence != record.invalidated_sequence:
                raise AssertionError("rotation sequence mismatch")
        self._active[(provider, new_hex)] = new_key
        self._emit(
            "rotation.complete",
            provider=provider,
            old_kid=old_hex,
            new_kid=new_hex,
            generation=self._generation[provider],
            old_kid_active=False,
            old_licenses_invalid_immediately=True,
        )
        return affected

    def validate(self, license_id: str) -> LicenseStatus:
        record = self.records.get(license_id)
        if record is None:
            raise LicenseError(f"unknown license {license_id}")
        active_entry = self._active.get((record.provider, record.kid_hex))
        valid = (
            record.status == STATUS_ACTIVE
            and active_entry is not None
            and active_entry.hex() == record.content_key_hex
            and self._generation.get(record.provider) == record.generation
        )
        reason = "ACTIVE" if valid else (
            record.invalidation_reason
            or ("GENERATION_CHANGED" if record.status == STATUS_ACTIVE else record.status)
        )
        status = LicenseStatus(
            license_id=record.license_id,
            provider=record.provider,
            kid_hex=record.kid_hex,
            valid=valid,
            status=record.status,
            generation=record.generation,
            reason=reason,
        )
        self._emit(
            "license.validated",
            license_id=record.license_id,
            provider=record.provider,
            kid=record.kid_hex,
            valid=valid,
            status=record.status,
            reason=reason,
        )
        return status

    def _deny(
        self,
        provider: str,
        request_id: str,
        kid_value: str,
        code: str,
        message: str,
    ) -> None:
        self._emit(
            "license.request_denied",
            request_id=request_id,
            provider=provider,
            kid=kid_value,
            http_status=403,
            code=code,
            message=message,
        )

    def handle_clearkey(self, request_body: bytes | dict[str, Any]) -> HTTPResponse:
        request_id = self._next_request_id()
        try:
            if isinstance(request_body, bytes):
                payload = json.loads(request_body.decode("utf-8"))
            else:
                payload = request_body
            raw_kids = payload.get("kids", [])
            if not isinstance(raw_kids, list) or not raw_kids:
                raise LicenseError("ClearKey request must contain a non-empty kids array")
            kid = _decode_clearkey_kid(str(raw_kids[0]))
        except (LicenseError, UnicodeDecodeError, json.JSONDecodeError, AttributeError) as exc:
            self._emit(
                "license.request_rejected",
                request_id=request_id,
                provider="clearkey",
                http_status=400,
                code="BAD_REQUEST",
                message=str(exc),
            )
            body = json.dumps(
                {"error": "BAD_REQUEST", "message": str(exc)}, separators=(",", ":")
            ).encode("utf-8")
            return HTTPResponse(400, {"Content-Type": "application/json"}, body)

        normalized = kid_hex(kid)
        self._emit(
            "license.requested",
            request_id=request_id,
            provider="clearkey",
            kid=normalized,
            endpoint="/clearkey",
        )
        try:
            record = self._issue("clearkey", kid, request_id)
        except LicenseError:
            message = f"ClearKey KID {normalized} is not active"
            self._deny("clearkey", request_id, normalized, "KID_NOT_ACTIVE", message)
            body = json.dumps(
                {
                    "error": "KID_NOT_ACTIVE",
                    "message": message,
                    "http_status": 403,
                },
                separators=(",", ":"),
            ).encode("utf-8")
            return HTTPResponse(403, {"Content-Type": "application/json"}, body)

        key = bytes.fromhex(record.content_key_hex)
        body = json.dumps(
            {
                "keys": [
                    {
                        "kty": "oct",
                        "kid": clearkey_kid_b64(kid),
                        "k": base64.urlsafe_b64encode(key)
                        .rstrip(b"=")
                        .decode("ascii"),
                    }
                ],
                "type": "temporary",
                "license_id": record.license_id,
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return HTTPResponse(200, {"Content-Type": "application/json"}, body)

    def handle_playready(self, request_body: bytes | str) -> HTTPResponse:
        request_id = self._next_request_id()
        try:
            text = (
                request_body.decode("utf-8")
                if isinstance(request_body, bytes)
                else request_body
            )
            kid = _parse_playready_kid(text)
        except (LicenseError, ET.ParseError, UnicodeDecodeError) as exc:
            self._emit(
                "license.request_rejected",
                request_id=request_id,
                provider="playready",
                http_status=400,
                code="BAD_REQUEST",
                message=str(exc),
            )
            return _playready_fault(400, "BAD_REQUEST", str(exc))

        normalized = kid_hex(kid)
        self._emit(
            "license.requested",
            request_id=request_id,
            provider="playready",
            kid=normalized,
            endpoint="/playready",
        )
        try:
            record = self._issue("playready", kid, request_id)
        except LicenseError:
            message = f"PlayReady KID {normalized} is not active"
            self._deny("playready", request_id, normalized, "KID_NOT_ACTIVE", message)
            return _playready_fault(403, "KID_NOT_ACTIVE", message)

        license_payload = {
            "LicenseID": record.license_id,
            "KID": record.kid_hex,
            "ContentKey": base64.b64encode(
                bytes.fromhex(record.content_key_hex)
            ).decode("ascii"),
            "Status": record.status,
            "Generation": record.generation,
        }
        encoded_license = base64.b64encode(
            json.dumps(license_payload, separators=(",", ":")).encode("utf-8")
        ).decode("ascii")
        body = (
            '<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">'
            "<soap:Body>"
            f'<AcquireLicenseResponse xmlns="{PLAYREADY_PROTOCOL_NS}">'
            f"<License>{encoded_license}</License>"
            "</AcquireLicenseResponse></soap:Body></soap:Envelope>"
        ).encode("utf-8")
        return HTTPResponse(200, {"Content-Type": "text/xml; charset=utf-8"}, body)


def _decode_clearkey_kid(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    try:
        kid = base64.urlsafe_b64decode(value + padding)
    except (ValueError, TypeError) as exc:
        raise LicenseError(f"invalid ClearKey KID: {value!r}") from exc
    if len(kid) != 16:
        raise LicenseError("ClearKey KID must decode to 16 bytes")
    return kid


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _parse_playready_kid(text: str) -> bytes:
    root = ET.fromstring(text)
    explicit: list[bytes] = []
    objects: list[bytes] = []
    for element in root.iter():
        name = _local_name(element.tag)
        if name == "KID" and element.text and element.text.strip():
            try:
                value = bytes.fromhex(element.text.strip())
            except ValueError as exc:
                raise LicenseError("PlayReady request KID is not hexadecimal") from exc
            if len(value) != 16:
                raise LicenseError("PlayReady request KID must be 16 bytes")
            explicit.append(value)
        elif name == "PlayReadyObject" and element.text:
            try:
                objects.append(base64.b64decode(element.text.strip(), validate=True))
            except (ValueError, TypeError) as exc:
                raise LicenseError("PlayReadyObject is not valid base64") from exc
    if objects:
        payload_kids = playready_payload_kids(objects[0])
        if explicit and explicit[0] not in payload_kids:
            raise LicenseError("request KID does not match the PlayReady PSSH KID")
        return payload_kids[0]
    if not explicit:
        raise LicenseError("PlayReady request contains no KID")
    return explicit[0]


def _playready_fault(status_code: int, code: str, message: str) -> HTTPResponse:
    from xml.sax.saxutils import escape

    body = (
        '<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">'
        "<soap:Body><soap:Fault>"
        f"<faultcode>{escape(code)}</faultcode>"
        f"<faultstring>{escape(message)}</faultstring>"
        f"<detail><HTTPStatus>{status_code}</HTTPStatus></detail>"
        "</soap:Fault></soap:Body></soap:Envelope>"
    ).encode("utf-8")
    return HTTPResponse(status_code, {"Content-Type": "text/xml; charset=utf-8"}, body)


def build_clearkey_request(kid: str | bytes) -> bytes:
    payload = {
        "kids": [clearkey_kid_b64(kid)],
        "type": "temporary",
    }
    return json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")


def build_playready_request(kid: str | bytes) -> bytes:
    normal_kid = as_kid(kid)
    playready_object = base64.b64encode(
        build_playready_data(normal_kid)
    ).decode("ascii")
    body = (
        '<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/" '
        f'xmlns:pr="{PLAYREADY_PROTOCOL_NS}">'
        "<soap:Body><pr:AcquireLicense><pr:challenge>"
        f"<pr:KID>{normal_kid.hex()}</pr:KID>"
        f"<pr:PlayReadyObject>{playready_object}</pr:PlayReadyObject>"
        "</pr:challenge></pr:AcquireLicense></soap:Body></soap:Envelope>"
    )
    return body.encode("utf-8")


def extract_playready_license(response: HTTPResponse) -> dict[str, Any]:
    if response.status_code != 200:
        raise LicenseError(f"PlayReady response status is {response.status_code}")
    root = ET.fromstring(response.body.decode("utf-8"))
    for element in root.iter():
        if _local_name(element.tag) == "License" and element.text:
            decoded = base64.b64decode(element.text, validate=True)
            return json.loads(decoded.decode("utf-8"))
    raise LicenseError("PlayReady response has no License element")
