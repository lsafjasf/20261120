"""Generate deterministic AIFF/AIFC fixtures for parser and offset tests."""

from __future__ import annotations

from math import ceil, pi, sin
from pathlib import Path
import struct

from .extended80 import encode_extended80

COMPRESSION_NAMES = {
    "NONE": "NONE",
    "sowt": "sowt",
    "FL32": "FL32",
    "ulaw": "ulaw",
    "alaw": "alaw",
    "ima4": "ima4",
}


def _chunk(chunk_id: bytes, payload: bytes) -> bytes:
    if len(chunk_id) != 4:
        raise ValueError("chunk ids must be four bytes")
    return chunk_id + len(payload).to_bytes(4, "big") + payload + (b"\x00" if len(payload) & 1 else b"")


def _form(form_type: bytes, chunks: list[bytes]) -> bytes:
    body = form_type + b"".join(chunks)
    return b"FORM" + len(body).to_bytes(4, "big") + body


def _pstring(name: str) -> bytes:
    raw = name.encode("latin-1")
    if len(raw) > 255:
        raise ValueError("AIFF-C compression name is too long")
    payload = bytes([len(raw)]) + raw
    return payload + (b"\x00" if len(payload) & 1 else b"")


def _comm(
    channels: int,
    frames: int,
    bits: int,
    sample_rate: int,
    compression_type: str | None = None,
    compression_name: str | None = None,
) -> bytes:
    payload = struct.pack(
        ">HIH",
        channels,
        frames,
        bits,
    ) + encode_extended80(sample_rate)
    if compression_type is not None:
        encoded_type = compression_type.encode("latin-1")
        if len(encoded_type) != 4:
            raise ValueError("compression type must be exactly four bytes")
        name = compression_name if compression_name is not None else COMPRESSION_NAMES[compression_type]
        payload += encoded_type + _pstring(name)
    return _chunk(b"COMM", payload)


def _ssnd(audio: bytes, offset: int = 0, block_size: int = 0) -> bytes:
    if offset < 0 or block_size < 0:
        raise ValueError("SSND offset and block size must be non-negative")
    alignment = b"\x00" * offset
    return _chunk(b"SSND", struct.pack(">II", offset, block_size) + alignment + audio)


def _stereo_tone(frames: int, sample_rate: int, f_left: float, f_right: float):
    for frame in range(frames):
        position = frame / sample_rate
        yield sin(2 * pi * f_left * position), sin(2 * pi * f_right * position)


def pcm16_be(frames: int, sample_rate: int) -> bytes:
    output = bytearray()
    for left, right in _stereo_tone(frames, sample_rate, 220.0, 230.0):
        output += struct.pack(">hh", round(left * 0.8 * 32767), round(right * 0.8 * 32767))
    return bytes(output)


def pcm16_le(frames: int, sample_rate: int) -> bytes:
    output = bytearray()
    for left, right in _stereo_tone(frames, sample_rate, 220.0, 230.0):
        output += struct.pack("<hh", round(left * 0.8 * 32767), round(right * 0.8 * 32767))
    return bytes(output)


def pcm24_be(frames: int, sample_rate: int) -> bytes:
    output = bytearray()
    for left, right in _stereo_tone(frames, sample_rate, 220.0, 230.0):
        for value in (left, right):
            sample = round(value * 0.8 * 8388607)
            output += (sample & 0xFFFFFF).to_bytes(3, "big")
    return bytes(output)


def fl32_be(frames: int, sample_rate: int) -> bytes:
    output = bytearray()
    for left, right in _stereo_tone(frames, sample_rate, 220.0, 230.0):
        output += struct.pack(">ff", left * 0.8, right * 0.8)
    return bytes(output)


def _ulaw_sample(sample: float) -> int:
    value = max(-32768, min(32767, round(sample * 32767)))
    sign = 0x80 if value < 0 else 0
    magnitude = min(abs(value), 32635) + 0x84
    exponent = max(0, magnitude.bit_length() - 8)
    mantissa = (magnitude >> (exponent + 3)) & 0x0F
    return (~(sign | (exponent << 4) | mantissa)) & 0xFF


def _alaw_sample(sample: float) -> int:
    value = max(-32768, min(32767, round(sample * 32767)))
    sign = 0x80 if value >= 0 else 0
    magnitude = abs(value)
    if magnitude < 256:
        encoded = magnitude >> 4
    else:
        exponent = min(7, magnitude.bit_length() - 8)
        encoded = (exponent << 4) | ((magnitude >> (exponent + 3)) & 0x0F)
    return (encoded ^ 0x55) | sign


def g711(frames: int, sample_rate: int, kind: str) -> bytes:
    encode = _ulaw_sample if kind == "ulaw" else _alaw_sample
    output = bytearray()
    for left, right in _stereo_tone(frames, sample_rate, 220.0, 230.0):
        output.extend((encode(left * 0.8), encode(right * 0.8)))
    return bytes(output)


def ima4_payload(frames: int, channels: int = 2) -> bytes:
    packet_frames = 64
    packets = ceil(frames / packet_frames)
    packet_bytes = 34
    output = bytearray()
    for _ in range(packets):
        for _channel in range(channels):
            output += b"\x00\x00" + b"\x00" * 32
    return bytes(output[: packets * channels * packet_bytes])


def write_aiff(path: Path, frames: int, sample_rate: int, bits: int) -> None:
    audio = pcm24_be(frames, sample_rate) if bits == 24 else pcm16_be(frames, sample_rate)
    path.write_bytes(_form(b"AIFF", [_comm(2, frames, bits, sample_rate), _ssnd(audio)]))


def write_aifc(
    path: Path,
    frames: int,
    sample_rate: int,
    compression_type: str,
    audio: bytes,
    bits: int | None = None,
    ssnd_offset: int = 0,
) -> None:
    if bits is None:
        bits = 32 if compression_type in ("FL32", "fl32") else 8 if compression_type in ("ulaw", "alaw") else 16
    chunks = [
        _chunk(b"FVER", (0xA2805140).to_bytes(4, "big")),
        _comm(2, frames, bits, sample_rate, compression_type),
        _ssnd(audio, offset=ssnd_offset, block_size=ssnd_offset),
    ]
    path.write_bytes(_form(b"AIFC", chunks))


def generate_fixtures(directory: str | Path) -> dict[str, Path]:
    """Create a complete fixture set and return a name-to-path mapping."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    frames = 12000
    rate = 48000
    files: dict[str, Path] = {}

    specs = [
        ("stereo_48k_none.aifc", "NONE", pcm16_be(frames, rate), 16, 0),
        ("stereo_48k_sowt.aifc", "sowt", pcm16_le(frames, rate), 16, 0),
        ("stereo_48k_fl32.aifc", "FL32", fl32_be(frames, rate), 32, 0),
        ("stereo_48k_ulaw.aifc", "ulaw", g711(frames, rate, "ulaw"), 8, 0),
        ("stereo_48k_alaw.aifc", "alaw", g711(frames, rate, "alaw"), 8, 0),
        ("stereo_48k_ima4.aifc", "ima4", ima4_payload(frames), 16, 0),
        ("stereo_48k_none_ssnd4096.aifc", "NONE", pcm16_be(frames, rate), 16, 4096),
        ("stereo_48k_ima4_ssnd4096.aifc", "ima4", ima4_payload(frames), 16, 4096),
    ]
    for name, compression, audio, bits, offset in specs:
        path = directory / name
        write_aifc(path, frames, rate, compression, audio, bits, offset)
        files[name] = path

    path = directory / "stereo_48k_24bit.aiff"
    write_aiff(path, frames, rate, 24)
    files[path.name] = path

    path = directory / "stereo_44100_none.aifc"
    write_aifc(path, 4410, 44100, "NONE", pcm16_be(4410, 44100), 16)
    files[path.name] = path

    path = directory / "timestretch_source_48k.aiff"
    write_aiff(path, 48000, 48000, 16)
    files[path.name] = path
    return files
