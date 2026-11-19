#!/usr/bin/env python3
"""Opus TOC and decode-path acceptance harness.

The harness uses the system libopus for real encodes/decodes.  It parses the
TOC and frame packing itself, then cross-checks every parsed frame boundary
against libopus' opus_packet_parse().
"""

from __future__ import annotations

import argparse
import array
import ctypes
import ctypes.util
import datetime
import json
import math
import statistics
import sys
import time
import wave
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable


SAMPLE_RATE = 48_000
MAX_PACKET_FRAMES = 48
MAX_PACKET_SAMPLES = 5_760

OPUS_OK = 0
OPUS_BAD_ARG = -1
OPUS_BUFFER_TOO_SMALL = -2
OPUS_INTERNAL_ERROR = -3
OPUS_INVALID_PACKET = -4
OPUS_UNIMPLEMENTED = -5
OPUS_INVALID_STATE = -6
OPUS_ALLOC_FAIL = -7

APPLICATION_VOIP = 2048
APPLICATION_AUDIO = 2049
APPLICATION_RESTRICTED_LOWDELAY = 2051

OPUS_SET_BITRATE_REQUEST = 4002
OPUS_SET_VBR_REQUEST = 4006
OPUS_SET_BANDWIDTH_REQUEST = 4008
OPUS_SET_COMPLEXITY_REQUEST = 4010
OPUS_SET_INBAND_FEC_REQUEST = 4012
OPUS_SET_SIGNAL_REQUEST = 4024
OPUS_GET_LOOKAHEAD_REQUEST = 4027

OPUS_AUTO = -1000
OPUS_BANDWIDTH_NARROWBAND = 1101
OPUS_BANDWIDTH_MEDIUMBAND = 1102
OPUS_BANDWIDTH_WIDEBAND = 1103
OPUS_BANDWIDTH_SUPERWIDEBAND = 1104
OPUS_BANDWIDTH_FULLBAND = 1105
OPUS_SIGNAL_VOICE = 3001
OPUS_SIGNAL_MUSIC = 3002

CONFIG_TABLE = (
    ("SILK-only", "NB", 10.0), ("SILK-only", "NB", 20.0),
    ("SILK-only", "NB", 40.0), ("SILK-only", "NB", 60.0),
    ("SILK-only", "MB", 10.0), ("SILK-only", "MB", 20.0),
    ("SILK-only", "MB", 40.0), ("SILK-only", "MB", 60.0),
    ("SILK-only", "WB", 10.0), ("SILK-only", "WB", 20.0),
    ("SILK-only", "WB", 40.0), ("SILK-only", "WB", 60.0),
    ("Hybrid", "SWB", 10.0), ("Hybrid", "SWB", 20.0),
    ("Hybrid", "FB", 10.0), ("Hybrid", "FB", 20.0),
    ("CELT-only", "NB", 2.5), ("CELT-only", "NB", 5.0),
    ("CELT-only", "NB", 10.0), ("CELT-only", "NB", 20.0),
    ("CELT-only", "WB", 2.5), ("CELT-only", "WB", 5.0),
    ("CELT-only", "WB", 10.0), ("CELT-only", "WB", 20.0),
    ("CELT-only", "SWB", 2.5), ("CELT-only", "SWB", 5.0),
    ("CELT-only", "SWB", 10.0), ("CELT-only", "SWB", 20.0),
    ("CELT-only", "FB", 2.5), ("CELT-only", "FB", 5.0),
    ("CELT-only", "FB", 10.0), ("CELT-only", "FB", 20.0),
)


class ParseError(ValueError):
    """The packet's TOC or frame packing is invalid."""


@dataclass(frozen=True)
class TOCInfo:
    toc: int
    config: int
    mode: str
    bandwidth: str
    frame_ms: float
    stereo: bool
    code: int


@dataclass(frozen=True)
class Framing:
    frame_sizes: tuple[int, ...]
    frame_offsets: tuple[int, ...]
    payload_offset: int
    padding_len: int
    packet_offset: int


@dataclass
class VADInfo:
    applicable: bool
    any_active: bool | None
    mid_flags: list[bool] = field(default_factory=list)
    side_flags: list[bool] = field(default_factory=list)
    mid_lbrr: bool | None = None
    side_lbrr: bool | None = None

    def label(self) -> str:
        if not self.applicable:
            return "N/A (CELT-only)"
        if not self.mid_flags:
            return "N/A"
        value = "1" if self.any_active else "0"
        detail = "/".join("1" if bit else "0" for bit in self.mid_flags)
        if self.side_flags:
            detail += "+" + "/".join("1" if bit else "0" for bit in self.side_flags)
        return f"{value} [{detail}]"


@dataclass
class PacketAnalysis:
    packet: bytes
    toc: TOCInfo
    framing: Framing
    frame_vad: list[VADInfo]
    frame_modes: list[str]
    frame_paths: list[str]
    silk_units: int
    celt_units: int
    parse_error: str | None = None


@dataclass
class DecodeResult:
    status: str
    error_code: int | None
    samples_per_channel: int
    decode_us: float
    pcm: list[int] = field(default_factory=list)
    rms_dbfs: float | None = None
    peak: int = 0


def parse_size(data: bytes, start: int, end: int) -> tuple[int, int]:
    if start >= end:
        raise ParseError("truncated frame-size field")
    first = data[start]
    if first < 252:
        return first, 1
    if start + 1 >= end:
        raise ParseError("truncated two-byte frame-size field")
    return 4 * data[start + 1] + first, 2


def parse_toc(packet: bytes) -> TOCInfo:
    if not packet:
        raise ParseError("empty packet")
    toc = packet[0]
    config = toc >> 3
    mode, bandwidth, frame_ms = CONFIG_TABLE[config]
    return TOCInfo(
        toc=toc,
        config=config,
        mode=mode,
        bandwidth=bandwidth,
        frame_ms=frame_ms,
        stereo=bool(toc & 0x04),
        code=toc & 0x03,
    )


def parse_framing(packet: bytes, toc_info: TOCInfo | None = None) -> Framing:
    if not packet:
        raise ParseError("empty packet")
    toc = toc_info or parse_toc(packet)
    length = len(packet)
    code = toc.code
    pos = 1
    padding_len = 0
    payload_end = length
    sizes: list[int] = []

    if code == 0:
        sizes = [length - 1]
    elif code == 1:
        payload_bytes = length - 1
        if payload_bytes % 2:
            raise ParseError("code 1 packet must have an even payload")
        sizes = [payload_bytes // 2, payload_bytes // 2]
    elif code == 2:
        first_size, used = parse_size(packet, pos, length)
        pos += used
        remaining = length - pos - first_size
        if remaining < 0:
            raise ParseError("code 2 first frame exceeds packet")
        sizes = [first_size, remaining]
    else:
        if length < 2:
            raise ParseError("code 3 packet is truncated")
        ch = packet[1]
        count = ch & 0x3F
        if count == 0:
            raise ParseError("code 3 packet has zero frames")
        frame_samples = round(toc.frame_ms * SAMPLE_RATE / 1000)
        if count * frame_samples > MAX_PACKET_SAMPLES:
            raise ParseError("code 3 packet exceeds 120 ms")
        pos = 2
        if ch & 0x40:
            while True:
                if pos >= length:
                    raise ParseError("truncated padding length")
                value = packet[pos]
                pos += 1
                padding_len += 254 if value == 255 else value
                if value != 255:
                    break
            if padding_len > length - pos:
                raise ParseError("padding exceeds packet")
            payload_end = length - padding_len
        if ch & 0x80:
            sizes = []
            for _ in range(count - 1):
                size, used = parse_size(packet, pos, payload_end)
                pos += used
                sizes.append(size)
            remaining = payload_end - pos - sum(sizes)
            if remaining < 0:
                raise ParseError("VBR frame sizes exceed payload")
            sizes.append(remaining)
        else:
            payload_bytes = payload_end - pos
            if payload_bytes % count:
                raise ParseError("CBR payload is not divisible by frame count")
            sizes = [payload_bytes // count] * count

    if any(size < 0 or size > 1275 for size in sizes):
        raise ParseError("frame size outside 0..1275 bytes")
    if sum(sizes) != payload_end - pos:
        raise ParseError("frame sizes do not consume the payload")
    offsets = []
    cursor = pos
    for size in sizes:
        offsets.append(cursor)
        cursor += size
    return Framing(
        frame_sizes=tuple(sizes),
        frame_offsets=tuple(offsets),
        payload_offset=pos,
        padding_len=padding_len,
        packet_offset=cursor + padding_len,
    )


def silk_internal_frames(frame_ms: float) -> int:
    return 1 if frame_ms <= 20 else int(round(frame_ms / 20))


def parse_silk_vad(frame: bytes, stereo: bool, frame_ms: float) -> VADInfo:
    if not frame:
        raise ParseError("SILK frame has no header bits")
    first = frame[0]
    count = silk_internal_frames(frame_ms)
    cursor = 0
    mid_flags = [bool(first & (1 << (7 - cursor + index))) for index in range(count)]
    cursor += count
    mid_lbrr = bool(first & (1 << (7 - cursor)))
    cursor += 1
    side_flags: list[bool] = []
    side_lbrr: bool | None = None
    if stereo:
        if cursor + count >= 8:
            raise ParseError("stereo SILK header exceeds first byte")
        side_flags = [bool(first & (1 << (7 - cursor + index))) for index in range(count)]
        cursor += count
        side_lbrr = bool(first & (1 << (7 - cursor)))
    active = any(mid_flags) or any(side_flags)
    return VADInfo(
        applicable=True,
        any_active=active,
        mid_flags=mid_flags,
        side_flags=side_flags,
        mid_lbrr=mid_lbrr,
        side_lbrr=side_lbrr,
    )


def decode_path(mode: str, toc: TOCInfo, vad: VADInfo) -> str:
    if mode == "SILK-only":
        return "SILK/LP -> resample -> PCM"
    if mode == "CELT-only":
        return "CELT/MDCT -> PCM"
    lbrr = " + LBRR" if vad.mid_lbrr or vad.side_lbrr else ""
    return f"SILK/LP{lbrr} + CELT/MDCT -> mix -> PCM"


def analyze_packet(packet: bytes) -> PacketAnalysis:
    toc = parse_toc(packet)
    framing = parse_framing(packet, toc)
    frame_vad: list[VADInfo] = []
    frame_modes: list[str] = []
    frame_paths: list[str] = []
    silk_units = 0
    celt_units = 0
    for offset, size in zip(framing.frame_offsets, framing.frame_sizes):
        frame = packet[offset:offset + size]
        if toc.mode == "CELT-only":
            vad = VADInfo(applicable=False, any_active=None)
            silk_units += 0
            celt_units += 1
        else:
            vad = parse_silk_vad(frame, toc.stereo, toc.frame_ms)
            silk_units += silk_internal_frames(toc.frame_ms)
            celt_units += 1 if toc.mode == "Hybrid" else 0
        frame_vad.append(vad)
        frame_modes.append(toc.mode)
        frame_paths.append(decode_path(toc.mode, toc, vad))
    return PacketAnalysis(
        packet=packet,
        toc=toc,
        framing=framing,
        frame_vad=frame_vad,
        frame_modes=frame_modes,
        frame_paths=frame_paths,
        silk_units=silk_units,
        celt_units=celt_units,
    )


class LibOpus:
    """Small ctypes binding for the functions used by this harness."""

    def __init__(self) -> None:
        library = ctypes.util.find_library("opus")
        if not library:
            raise RuntimeError("libopus not found; install libopus0/libopus")
        self.lib = ctypes.CDLL(library)
        self._configure_prototypes()
        self.version = self.lib.opus_get_version_string().decode("utf-8", "replace")

    def _configure_prototypes(self) -> None:
        lib = self.lib
        lib.opus_get_version_string.argtypes = []
        lib.opus_get_version_string.restype = ctypes.c_char_p
        lib.opus_strerror.argtypes = [ctypes.c_int]
        lib.opus_strerror.restype = ctypes.c_char_p

        lib.opus_encoder_create.argtypes = [
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_int)
        ]
        lib.opus_encoder_create.restype = ctypes.c_void_p
        lib.opus_encoder_destroy.argtypes = [ctypes.c_void_p]
        lib.opus_encode.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_int16),
            ctypes.c_int,
            ctypes.POINTER(ctypes.c_ubyte),
            ctypes.c_int,
        ]
        lib.opus_encode.restype = ctypes.c_int
        lib.opus_encoder_ctl.restype = ctypes.c_int

        lib.opus_decoder_create.argtypes = [
            ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_int)
        ]
        lib.opus_decoder_create.restype = ctypes.c_void_p
        lib.opus_decoder_destroy.argtypes = [ctypes.c_void_p]
        lib.opus_decode.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_ubyte),
            ctypes.c_int,
            ctypes.POINTER(ctypes.c_int16),
            ctypes.c_int,
            ctypes.c_int,
        ]
        lib.opus_decode.restype = ctypes.c_int
        lib.opus_packet_parse.argtypes = [
            ctypes.POINTER(ctypes.c_ubyte),
            ctypes.c_int,
            ctypes.POINTER(ctypes.c_ubyte),
            ctypes.POINTER(ctypes.POINTER(ctypes.c_ubyte)),
            ctypes.POINTER(ctypes.c_int16),
            ctypes.POINTER(ctypes.c_int),
        ]
        lib.opus_packet_parse.restype = ctypes.c_int

    def error_text(self, code: int) -> str:
        text = self.lib.opus_strerror(code)
        return text.decode("utf-8", "replace") if text else f"error {code}"

    def _encoder_ctl(self, encoder: int, request: int, value: int) -> None:
        result = self.lib.opus_encoder_ctl(encoder, request, ctypes.c_int(value))
        if result != OPUS_OK:
            raise RuntimeError(f"encoder ctl {request} failed: {self.error_text(result)}")

    def encode(
        self,
        pcm: list[int],
        *,
        application: int,
        bitrate: int,
        frame_size: int,
        bandwidth: int = OPUS_AUTO,
        signal: int = OPUS_SIGNAL_VOICE,
        complexity: int = 10,
        vbr: bool = True,
    ) -> bytes:
        error = ctypes.c_int()
        encoder = self.lib.opus_encoder_create(
            SAMPLE_RATE, 1, application, ctypes.byref(error)
        )
        if not encoder or error.value != OPUS_OK:
            raise RuntimeError(f"opus_encoder_create: {self.error_text(error.value)}")
        try:
            self._encoder_ctl(encoder, OPUS_SET_BITRATE_REQUEST, bitrate)
            self._encoder_ctl(encoder, OPUS_SET_BANDWIDTH_REQUEST, bandwidth)
            self._encoder_ctl(encoder, OPUS_SET_SIGNAL_REQUEST, signal)
            self._encoder_ctl(encoder, OPUS_SET_COMPLEXITY_REQUEST, complexity)
            self._encoder_ctl(encoder, OPUS_SET_VBR_REQUEST, 1 if vbr else 0)
            source = (ctypes.c_int16 * len(pcm))(*pcm)
            output = (ctypes.c_ubyte * 1275)()
            encoded = self.lib.opus_encode(
                encoder, source, frame_size, output, len(output)
            )
            if encoded < 0:
                raise RuntimeError(f"opus_encode: {self.error_text(encoded)}")
            return bytes(output[:encoded])
        finally:
            self.lib.opus_encoder_destroy(encoder)

    def parse_boundaries(self, packet: bytes) -> tuple[list[int], list[int], int]:
        buffer = (ctypes.c_ubyte * len(packet))(*packet)
        toc = ctypes.c_ubyte()
        frames = (ctypes.POINTER(ctypes.c_ubyte) * MAX_PACKET_FRAMES)()
        sizes = (ctypes.c_int16 * MAX_PACKET_FRAMES)()
        payload_offset = ctypes.c_int()
        count = self.lib.opus_packet_parse(
            buffer,
            len(packet),
            ctypes.byref(toc),
            frames,
            sizes,
            ctypes.byref(payload_offset),
        )
        if count < 0:
            raise ParseError(f"libopus parse failed: {self.error_text(count)}")
        base = ctypes.addressof(buffer)
        offsets = [
            ctypes.cast(frames[i], ctypes.c_void_p).value - base
            for i in range(count)
        ]
        return [sizes[i] for i in range(count)], offsets, payload_offset.value

    def decode_int16(
        self,
        decoder: int,
        packet: bytes | None,
        *,
        frame_size: int = MAX_PACKET_SAMPLES,
        channels: int = 2,
    ) -> tuple[int, float, list[int]]:
        if packet is None:
            data = None
            length = 0
        else:
            buffer = (ctypes.c_ubyte * len(packet))(*packet)
            data = buffer
            length = len(packet)
        pcm = (ctypes.c_int16 * (MAX_PACKET_SAMPLES * channels))()
        started = time.perf_counter_ns()
        result = self.lib.opus_decode(decoder, data, length, pcm, frame_size, 0)
        elapsed_us = (time.perf_counter_ns() - started) / 1000.0
        if result < 0:
            return result, elapsed_us, []
        return result, elapsed_us, list(pcm[:result * channels])


@dataclass
class EncodedPacket:
    packet: bytes
    profile: str
    expected_mode: str
    active: bool = True


@dataclass
class DecoderSession:
    lib: LibOpus
    channels: int = 2
    init_calls: int = 0
    handle: int = 0

    def __post_init__(self) -> None:
        error = ctypes.c_int()
        self.handle = self.lib.lib.opus_decoder_create(
            SAMPLE_RATE, self.channels, ctypes.byref(error)
        )
        if not self.handle or error.value != OPUS_OK:
            raise RuntimeError(f"opus_decoder_create: {self.lib.error_text(error.value)}")
        self.init_calls = 1

    def close(self) -> None:
        if self.handle:
            self.lib.lib.opus_decoder_destroy(self.handle)
            self.handle = 0


def rms_dbfs(pcm: Iterable[int], peak_floor: int = 1) -> tuple[float | None, int]:
    values = list(pcm)
    if not values:
        return None, 0
    peak = max(abs(value) for value in values)
    if peak < peak_floor:
        return -math.inf, peak
    mean_square = sum(value * value for value in values) / len(values)
    db = 10.0 * math.log10(mean_square / (32768.0 * 32768.0)) if mean_square else -math.inf
    return db, peak


def synthesize_pcm(frame_size: int, block_index: int, active: bool = True) -> list[int]:
    if not active:
        return [0] * frame_size
    pcm: list[int] = []
    state = (0x12345678 + block_index * 2654435761) & 0xFFFFFFFF
    start = block_index * frame_size
    for index in range(frame_size):
        sample_index = start + index
        state ^= (state << 13) & 0xFFFFFFFF
        state ^= state >> 17
        state ^= (state << 5) & 0xFFFFFFFF
        noise = ((state & 0xFFFF) / 32768.0) - 1.0
        t = sample_index / SAMPLE_RATE
        voiced = math.sin(2.0 * math.pi * 220.0 * t)
        harmonic = 0.35 * math.sin(2.0 * math.pi * 660.0 * t)
        transient = 0.25 if (sample_index % 4800) < 80 else 0.0
        value = 11_000.0 * (0.55 * voiced + harmonic + transient) + 700.0 * noise
        pcm.append(max(-32768, min(32767, int(value))))
    return pcm


def encode_profile(
    lib: LibOpus,
    profile: str,
    *,
    application: int,
    bitrate: int,
    frame_ms: float,
    bandwidth: int,
    expected_mode: str,
    active: bool = True,
    signal: int = OPUS_SIGNAL_VOICE,
) -> EncodedPacket:
    frame_size = int(round(frame_ms * SAMPLE_RATE / 1000))
    packet = b""
    for block_index in range(3):
        packet = lib.encode(
            synthesize_pcm(frame_size, block_index, active),
            application=application,
            bitrate=bitrate,
            frame_size=frame_size,
            bandwidth=bandwidth,
            signal=signal,
        )
        mode = parse_toc(packet).mode
        if mode != expected_mode:
            raise RuntimeError(
                f"{profile}: expected {expected_mode}, encoder produced {mode}"
            )
    return EncodedPacket(packet, profile, expected_mode, active)


def build_packet_matrix(lib: LibOpus) -> tuple[list[EncodedPacket], EncodedPacket]:
    silk_bandwidths = (
        ("NB", OPUS_BANDWIDTH_NARROWBAND),
        ("MB", OPUS_BANDWIDTH_MEDIUMBAND),
        ("WB", OPUS_BANDWIDTH_WIDEBAND),
    )
    celt_bandwidths = (
        ("NB", OPUS_BANDWIDTH_NARROWBAND),
        ("WB", OPUS_BANDWIDTH_WIDEBAND),
        ("SWB", OPUS_BANDWIDTH_SUPERWIDEBAND),
        ("FB", OPUS_BANDWIDTH_FULLBAND),
    )
    packets: list[EncodedPacket] = []

    for bandwidth_name, bandwidth in silk_bandwidths:
        for frame_ms in (10.0, 20.0, 40.0, 60.0):
            profile = f"silk-{bandwidth_name}-{frame_ms:g}ms"
            packets.append(
                encode_profile(
                    lib,
                    profile,
                    application=APPLICATION_VOIP,
                    bitrate=12_000,
                    frame_ms=frame_ms,
                    bandwidth=bandwidth,
                    expected_mode="SILK-only",
                )
            )

    for bandwidth_name, bandwidth in celt_bandwidths:
        for frame_ms in (2.5, 5.0, 10.0, 20.0):
            profile = f"celt-{bandwidth_name}-{frame_ms:g}ms"
            packets.append(
                encode_profile(
                    lib,
                    profile,
                    application=APPLICATION_RESTRICTED_LOWDELAY,
                    bitrate=64_000,
                    frame_ms=frame_ms,
                    bandwidth=bandwidth,
                    expected_mode="CELT-only",
                    signal=OPUS_SIGNAL_MUSIC,
                )
            )

    hybrid_specs = (
        ("hybrid-SWB-10ms", 10.0, OPUS_BANDWIDTH_SUPERWIDEBAND),
        ("hybrid-SWB-20ms", 20.0, OPUS_BANDWIDTH_SUPERWIDEBAND),
        ("hybrid-FB-10ms", 10.0, OPUS_BANDWIDTH_FULLBAND),
        ("hybrid-FB-20ms", 20.0, OPUS_BANDWIDTH_FULLBAND),
        ("hybrid-FB-40ms-code1", 40.0, OPUS_BANDWIDTH_FULLBAND),
        ("hybrid-FB-60ms-code3", 60.0, OPUS_BANDWIDTH_FULLBAND),
        ("hybrid-SWB-40ms-code1", 40.0, OPUS_BANDWIDTH_SUPERWIDEBAND),
    )
    for profile, frame_ms, bandwidth in hybrid_specs:
        packets.append(
            encode_profile(
                lib,
                profile,
                application=APPLICATION_VOIP,
                bitrate=16_000,
                frame_ms=frame_ms,
                bandwidth=bandwidth,
                expected_mode="Hybrid",
            )
        )

    silent_silk = encode_profile(
        lib,
        "silk-silent-WB-20ms",
        application=APPLICATION_VOIP,
        bitrate=12_000,
        frame_ms=20.0,
        bandwidth=OPUS_BANDWIDTH_WIDEBAND,
        expected_mode="SILK-only",
        active=False,
    )
    return packets, silent_silk


def build_sequence(
    matrix: list[EncodedPacket], silent_packet: EncodedPacket
) -> list[EncodedPacket]:
    by_profile = {item.profile: item for item in matrix}
    sequence = list(matrix[:12])
    sequence.append(silent_packet)
    sequence.extend(matrix[12:])
    transition_profiles = (
        "silk-WB-60ms",
        "celt-FB-20ms",
        "hybrid-FB-20ms",
        "silk-NB-10ms",
        "celt-NB-2.5ms",
        "hybrid-SWB-10ms",
        "silk-WB-20ms",
        "celt-FB-2.5ms",
    )
    sequence.extend(
        EncodedPacket(
            by_profile[profile].packet,
            f"transition-{profile}",
            by_profile[profile].expected_mode,
        )
        for profile in transition_profiles
    )
    return sequence


def boundary_cross_check(lib: LibOpus, analysis: PacketAnalysis) -> dict[str, Any]:
    try:
        lib_sizes, lib_offsets, lib_payload_offset = lib.parse_boundaries(analysis.packet)
    except ParseError as error:
        return {"match": False, "error": str(error)}
    expected_sizes = list(analysis.framing.frame_sizes)
    expected_offsets = list(analysis.framing.frame_offsets)
    return {
        "match": (
            lib_sizes == expected_sizes
            and lib_offsets == expected_offsets
            and lib_payload_offset == analysis.framing.payload_offset
        ),
        "libopus_frame_sizes": lib_sizes,
        "libopus_frame_offsets": lib_offsets,
        "libopus_payload_offset": lib_payload_offset,
        "parsed_frame_sizes": expected_sizes,
        "parsed_frame_offsets": expected_offsets,
        "parsed_payload_offset": analysis.framing.payload_offset,
    }


def corrupt_toc(packet: bytes) -> bytes:
    """Keep the framing code/stereo bits but force SILK NB/10 ms."""
    if not packet:
        raise ValueError("cannot corrupt an empty packet")
    damaged = bytearray(packet)
    damaged[0] = damaged[0] & 0x07
    return bytes(damaged)


def packet_duration_samples(analysis: PacketAnalysis) -> int:
    frame_samples = round(analysis.toc.frame_ms * SAMPLE_RATE / 1000)
    return min(MAX_PACKET_SAMPLES, frame_samples * len(analysis.framing.frame_sizes))


def make_packet_record(
    index: int,
    item: EncodedPacket,
    analysis: PacketAnalysis,
    boundary: dict[str, Any],
    decoded: DecodeResult,
) -> dict[str, Any]:
    vad_labels = [vad.label() for vad in analysis.frame_vad]
    lbrr = any(vad.mid_lbrr or vad.side_lbrr for vad in analysis.frame_vad)
    return {
        "index": index,
        "profile": item.profile,
        "bytes": len(item.packet),
        "toc_hex": f"0x{analysis.toc.toc:02x}",
        "config": analysis.toc.config,
        "mode": analysis.toc.mode,
        "bandwidth": analysis.toc.bandwidth,
        "frame_ms": analysis.toc.frame_ms,
        "toc_frames": len(analysis.framing.frame_sizes),
        "total_ms": analysis.toc.frame_ms * len(analysis.framing.frame_sizes),
        "stereo": analysis.toc.stereo,
        "code": analysis.toc.code,
        "frame_sizes": list(analysis.framing.frame_sizes),
        "frame_offsets": list(analysis.framing.frame_offsets),
        "payload_offset": analysis.framing.payload_offset,
        "padding_bytes": analysis.framing.padding_len,
        "vad": vad_labels,
        "vad_any_active": any(
            vad.any_active for vad in analysis.frame_vad if vad.applicable
        ) if analysis.toc.mode != "CELT-only" else None,
        "lbrr": lbrr,
        "silk_units": analysis.silk_units,
        "celt_units": analysis.celt_units,
        "decode_path": analysis.frame_paths[0] if len(set(analysis.frame_paths)) == 1
        else " | ".join(analysis.frame_paths),
        "boundary_match": boundary.get("match", False),
        "boundary": boundary,
        "decode_status": decoded.status,
        "decode_error_code": decoded.error_code,
        "decoded_samples_per_channel": decoded.samples_per_channel,
        "decode_us": round(decoded.decode_us, 3),
        "rms_dbfs": None if decoded.rms_dbfs is None else round(decoded.rms_dbfs, 3),
        "peak": decoded.peak,
    }


def run_sequence(
    lib: LibOpus, sequence: list[EncodedPacket]
) -> tuple[list[dict[str, Any]], list[int], DecoderSession]:
    session = DecoderSession(lib)
    records: list[dict[str, Any]] = []
    output_pcm: list[int] = []
    try:
        for index, item in enumerate(sequence):
            analysis = analyze_packet(item.packet)
            boundary = boundary_cross_check(lib, analysis)
            samples, elapsed_us, pcm = lib.decode_int16(session.handle, item.packet)
            if samples < 0:
                decoded = DecodeResult(
                    status="error",
                    error_code=samples,
                    samples_per_channel=0,
                    decode_us=elapsed_us,
                )
            else:
                db, peak = rms_dbfs(pcm)
                decoded = DecodeResult(
                    status="ok",
                    error_code=None,
                    samples_per_channel=samples,
                    decode_us=elapsed_us,
                    pcm=pcm,
                    rms_dbfs=db,
                    peak=peak,
                )
                output_pcm.extend(pcm)
            records.append(make_packet_record(index, item, analysis, boundary, decoded))
    finally:
        session.close()
    return records, output_pcm, session


def run_fault_injection(
    lib: LibOpus,
    sequence: list[EncodedPacket],
    choice_index: int,
) -> tuple[list[dict[str, Any]], list[int], dict[str, Any]]:
    session = DecoderSession(lib)
    records: list[dict[str, Any]] = []
    output_pcm: list[int] = []
    fault_detail: dict[str, Any] = {}
    try:
        for index, item in enumerate(sequence):
            analysis = analyze_packet(item.packet)
            boundary = boundary_cross_check(lib, analysis)
            original_duration = packet_duration_samples(analysis)
            packet_to_decode = item.packet
            if index == choice_index:
                packet_to_decode = corrupt_toc(item.packet)
                try:
                    damaged_analysis = analyze_packet(packet_to_decode)
                    damaged_toc = {
                        "toc_hex": f"0x{damaged_analysis.toc.toc:02x}",
                        "config": damaged_analysis.toc.config,
                        "mode": damaged_analysis.toc.mode,
                        "frame_ms": damaged_analysis.toc.frame_ms,
                        "code": damaged_analysis.toc.code,
                    }
                except ParseError as error:
                    damaged_analysis = None
                    damaged_toc = {"parse_error": str(error)}
                fault_detail = {
                    "packet_index": index,
                    "original_toc_hex": f"0x{analysis.toc.toc:02x}",
                    "damaged_toc_hex": f"0x{packet_to_decode[0]:02x}",
                    "damaged_toc": damaged_toc,
                    "original_duration_samples": original_duration,
                    "original_profile": item.profile,
                }

            samples, elapsed_us, pcm = lib.decode_int16(
                session.handle, packet_to_decode, frame_size=original_duration
            )
            original_decode_failed = samples < 0
            plc_samples = 0
            plc_us = 0.0
            if samples < 0:
                samples, plc_us, pcm = lib.decode_int16(
                    session.handle, None, frame_size=original_duration
                )
                plc_samples = samples
            elif samples < original_duration:
                needed = original_duration - samples
                extra_samples, plc_us, extra_pcm = lib.decode_int16(
                    session.handle, None, frame_size=needed
                )
                if extra_samples > 0:
                    pcm.extend(extra_pcm)
                    samples += extra_samples
                    plc_samples = extra_samples
                pcm = pcm[:original_duration * session.channels]
                samples = min(samples, original_duration)

            if samples < 0:
                decoded = DecodeResult(
                    status="fatal",
                    error_code=samples,
                    samples_per_channel=0,
                    decode_us=elapsed_us + plc_us,
                )
            else:
                db, peak = rms_dbfs(pcm)
                decoded = DecodeResult(
                    status="toc_corrupted" if index == choice_index else "ok",
                    error_code=None,
                    samples_per_channel=samples,
                    decode_us=elapsed_us + plc_us,
                    pcm=pcm,
                    rms_dbfs=db,
                    peak=peak,
                )
                output_pcm.extend(pcm)
            record = make_packet_record(index, item, analysis, boundary, decoded)
            if index == choice_index:
                record.update(
                    {
                        "injected_toc": packet_to_decode[0],
                        "corrupt_packet_decode_error": original_decode_failed,
                        "plc_samples": plc_samples,
                        "plc_us": round(plc_us, 3),
                    }
                )
                fault_detail.update(
                    {
                        "decode_status": decoded.status,
                        "corrupt_packet_decode_error": original_decode_failed,
                        "decoded_samples_per_channel": decoded.samples_per_channel,
                        "plc_samples": plc_samples,
                        "rms_dbfs": decoded.rms_dbfs,
                        "peak": decoded.peak,
                    }
                )
            records.append(record)
    finally:
        session.close()
    return records, output_pcm, fault_detail


def longest_near_silence_run(pcm: list[int], channels: int, threshold: int = 8) -> int:
    best = 0
    current = 0
    for start in range(0, len(pcm), channels):
        sample_peak = max(abs(value) for value in pcm[start:start + channels])
        if sample_peak <= threshold:
            current += 1
            best = max(best, current)
        else:
            current = 0
    return best


def summarize_fault(
    baseline_pcm: list[int],
    fault_pcm: list[int],
    fault_records: list[dict[str, Any]],
    detail: dict[str, Any],
    channels: int = 2,
) -> dict[str, Any]:
    baseline_db, baseline_peak = rms_dbfs(baseline_pcm)
    fault_db, fault_peak = rms_dbfs(fault_pcm)
    fault_index = detail["packet_index"]
    downstream = [
        record for record in fault_records
        if record["index"] > fault_index and record["decode_status"] == "ok"
    ]
    non_silent_downstream = [
        record for record in downstream
        if record["rms_dbfs"] is not None and record["rms_dbfs"] > -50.0
    ]
    nonzero = sum(1 for value in fault_pcm if abs(value) > 8)
    total = len(fault_pcm)
    verdict = (
        len(downstream) >= 5
        and len(non_silent_downstream) == len(downstream)
        and fault_db is not None
        and fault_db > -50.0
    )
    return {
        **detail,
        "baseline_rms_dbfs": None if baseline_db is None else round(baseline_db, 3),
        "fault_rms_dbfs": None if fault_db is None else round(fault_db, 3),
        "baseline_peak": baseline_peak,
        "fault_peak": fault_peak,
        "fault_nonzero_ratio": round(nonzero / total, 6) if total else 0.0,
        "longest_near_silence_samples": longest_near_silence_run(
            fault_pcm, channels
        ),
        "downstream_packet_count": len(downstream),
        "downstream_non_silent_count": len(non_silent_downstream),
        "downstream_min_rms_dbfs": (
            round(min(record["rms_dbfs"] for record in downstream), 3)
            if downstream else None
        ),
        "whole_stream_not_silenced": verdict,
    }


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * fraction)))
    return ordered[index]


def summarize_records(
    records: list[dict[str, Any]], session: DecoderSession
) -> dict[str, Any]:
    decode_times = [record["decode_us"] for record in records]
    mode_switches = 0
    config_switches = 0
    previous_mode = None
    previous_config = None
    for record in records:
        if previous_mode is not None and record["mode"] != previous_mode:
            mode_switches += 1
        if previous_config is not None and record["config"] != previous_config:
            config_switches += 1
        previous_mode = record["mode"]
        previous_config = record["config"]

    frame_counts = {"SILK-only": 0, "CELT-only": 0, "Hybrid": 0}
    for record in records:
        frame_counts[record["mode"]] += record["toc_frames"]
    silk_units = sum(record["silk_units"] for record in records)
    celt_units = sum(record["celt_units"] for record in records)
    total_units = silk_units + celt_units
    vad_applicable = sum(
        1 for record in records if record["vad_any_active"] is not None
    )
    vad_active = sum(
        1 for record in records if record["vad_any_active"] is True
    )
    all_decodes_ok = all(record["decode_status"] == "ok" for record in records)
    all_boundaries_match = all(record["boundary_match"] for record in records)
    return {
        "libopus_version": session.lib.version,
        "sample_rate_hz": SAMPLE_RATE,
        "decoder_channels": session.channels,
        "decoder_init_calls": session.init_calls,
        "decoder_reinit_calls": max(0, session.init_calls - 1),
        "decoder_mode_switches": mode_switches,
        "decoder_config_switches": config_switches,
        "all_decodes_ok": all_decodes_ok,
        "all_boundaries_match_libopus": all_boundaries_match,
        "decoder_reinitialization_required": not (all_decodes_ok and mode_switches > 0),
        "packet_count": len(records),
        "packet_bytes_total": sum(record["bytes"] for record in records),
        "packet_bytes_min": min(record["bytes"] for record in records),
        "packet_bytes_max": max(record["bytes"] for record in records),
        "packet_bytes_mean": round(
            statistics.fmean(record["bytes"] for record in records), 3
        ),
        "decode_us_total": round(sum(decode_times), 3),
        "decode_us_mean": round(statistics.fmean(decode_times), 3),
        "decode_us_p50": round(_percentile(decode_times, 0.50), 3),
        "decode_us_p95": round(_percentile(decode_times, 0.95), 3),
        "decode_us_max": round(max(decode_times), 3),
        "toc_packet_frames_by_mode": frame_counts,
        "silk_decoder_frames": silk_units,
        "celt_decoder_frames": celt_units,
        "decoder_frames_total": total_units,
        "silk_frame_ratio": round(silk_units / total_units, 6) if total_units else 0.0,
        "celt_frame_ratio": round(celt_units / total_units, 6) if total_units else 0.0,
        "vad_applicable_packets": vad_applicable,
        "vad_active_packets": vad_active,
        "vad_inactive_packets": vad_applicable - vad_active,
        "configs_covered": sorted({record["config"] for record in records}),
    }


def sample_envelope(
    sample_id: str,
    packet: bytes,
    analysis: PacketAnalysis,
    *,
    crc_error: bool,
    profile: str,
) -> dict[str, Any]:
    calculated = zlib.crc32(packet) & 0xFFFFFFFF
    stored = calculated ^ 0xFFFFFFFF if crc_error else calculated
    return {
        "sample_id": sample_id,
        "profile": profile,
        "packet_hex": packet.hex(),
        "bytes": len(packet),
        "toc_hex": f"0x{analysis.toc.toc:02x}",
        "config": analysis.toc.config,
        "mode": analysis.toc.mode,
        "bandwidth": analysis.toc.bandwidth,
        "frame_ms": analysis.toc.frame_ms,
        "code": analysis.toc.code,
        "frame_sizes": list(analysis.framing.frame_sizes),
        "vad": [vad.label() for vad in analysis.frame_vad],
        "crc32_calculated": f"{calculated:08x}",
        "crc32_stored": f"{stored:08x}",
        "crc_ok": stored == calculated,
        "crc_scope": "external acceptance envelope; native Opus RTP payload has no CRC",
    }


def build_sample_sets(
    sequence: list[EncodedPacket],
    silent_packet: EncodedPacket,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    wanted_profiles = (
        "silk-NB-10ms",
        "silk-WB-20ms",
        "hybrid-FB-20ms",
        "celt-SWB-20ms",
        "celt-FB-20ms",
        "hybrid-FB-60ms-code3",
    )
    selected_indices = [
        next(index for index, item in enumerate(sequence) if item.profile == profile)
        for profile in wanted_profiles
    ]
    selected: list[tuple[str, EncodedPacket, PacketAnalysis]] = []
    for index in selected_indices:
        item = sequence[index]
        config = parse_toc(item.packet).config
        selected.append(
            (f"cfg-{config:02d}-{item.profile}", item, analyze_packet(item.packet))
        )
    selected.append(
        (f"cfg-09-{silent_packet.profile}", silent_packet, analyze_packet(silent_packet.packet))
    )

    valid_rows = []
    bad_rows = []
    for sample_id, item, analysis in selected:
        valid_rows.append(
            sample_envelope(sample_id, item.packet, analysis, crc_error=False, profile=item.profile)
        )
        bad_rows.append(
            sample_envelope(sample_id, item.packet, analysis, crc_error=True, profile=item.profile)
        )
    return valid_rows, bad_rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def verify_sample_sets(lib: LibOpus, samples_dir: Path) -> dict[str, Any]:
    valid_rows = read_jsonl(samples_dir / "valid_frames.jsonl")
    bad_rows = read_jsonl(samples_dir / "crc_error_frames.jsonl")
    if [row["sample_id"] for row in valid_rows] != [row["sample_id"] for row in bad_rows]:
        raise RuntimeError("sample sets have different sample_id ordering")

    valid_session = DecoderSession(lib)
    bad_session = DecoderSession(lib)
    checks: list[dict[str, Any]] = []
    try:
        for valid, bad in zip(valid_rows, bad_rows):
            valid_packet = bytes.fromhex(valid["packet_hex"])
            bad_packet = bytes.fromhex(bad["packet_hex"])
            valid_crc = zlib.crc32(valid_packet) & 0xFFFFFFFF
            bad_crc = zlib.crc32(bad_packet) & 0xFFFFFFFF
            valid_samples, _, valid_pcm = lib.decode_int16(
                valid_session.handle, valid_packet
            )
            bad_samples, _, bad_pcm = lib.decode_int16(
                bad_session.handle, bad_packet
            )
            checks.append(
                {
                    "sample_id": valid["sample_id"],
                    "mode": valid["mode"],
                    "packet_hex_equal": valid_packet == bad_packet,
                    "valid_crc_ok": int(valid["crc32_stored"], 16) == valid_crc,
                    "crc_error_detected": int(bad["crc32_stored"], 16) != bad_crc,
                    "valid_decode_samples": valid_samples,
                    "crc_error_decode_samples": bad_samples,
                    "payload_decode_equal": (
                        valid_samples > 0
                        and valid_samples == bad_samples
                        and valid_pcm == bad_pcm
                    ),
                }
            )
    finally:
        valid_session.close()
        bad_session.close()

    result = {
        "sample_count": len(checks),
        "all_valid_crc_ok": all(check["valid_crc_ok"] for check in checks),
        "all_crc_errors_detected": all(
            check["crc_error_detected"] for check in checks
        ),
        "all_crc_error_payloads_decode": all(
            check["crc_error_decode_samples"] > 0 for check in checks
        ),
        "all_payload_decodes_equal": all(
            check["payload_decode_equal"] for check in checks
        ),
        "checks": checks,
    }
    return result


def write_wav(path: Path, pcm: list[int], channels: int = 2) -> None:
    samples = array.array("h", pcm)
    if sys.byteorder != "little":
        samples.byteswap()
    with wave.open(str(path), "wb") as output:
        output.setnchannels(channels)
        output.setsampwidth(2)
        output.setframerate(SAMPLE_RATE)
        output.writeframes(samples.tobytes())


def _fmt(value: Any, digits: int = 3) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def render_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    fault = report["fault_injection"]
    lines = [
        "# Opus TOC 与解码路径验收报告",
        "",
        f"- libopus: `{summary['libopus_version']}`",
        f"- 采样率/声道: {summary['sample_rate_hz']} Hz / {summary['decoder_channels']} ch",
        f"- 包数/总字节: {summary['packet_count']} / {summary['packet_bytes_total']}",
        f"- TOC 边界交叉核对: {'全部一致' if summary['all_boundaries_match_libopus'] else '存在不一致'}",
        f"- 同一解码器实例完成全部帧型切换: "
        f"{'是' if not summary['decoder_reinitialization_required'] else '否'}",
        f"- 损坏 TOC 未导致后续静音: "
        f"{'是' if fault['whole_stream_not_silenced'] else '否'}",
        "",
        "## 复现命令",
        "",
        "```bash",
        "python3 opus_acceptance.py generate --out artifacts",
        "python3 opus_acceptance.py verify-samples --out artifacts",
        "```",
        "",
        "## TOC 判别规则",
        "",
        "- 高 5 位 `config`: 0–11 SILK-only，12–15 Hybrid，16–31 CELT-only。",
        "- `config` 同时决定带宽与单帧时长；bit 2 为 stereo，低 2 位为 frame-count code。",
        "- SILK/Hybrid 的 VAD 位于每帧载荷首字节的高位；40/60 ms 帧分别有 2/3 个 VAD 位。",
        "- CELT-only 没有 VAD 标志，报告中明确标为 `N/A`。",
        "",
        "## 帧统计与解码器状态",
        "",
        f"- SILK 解码层帧: {summary['silk_decoder_frames']} "
        f"({summary['silk_frame_ratio'] * 100:.2f}%)",
        f"- CELT 解码层帧: {summary['celt_decoder_frames']} "
        f"({summary['celt_frame_ratio'] * 100:.2f}%)",
        f"- 模式切换次数: {summary['decoder_mode_switches']}；"
        f"config 切换次数: {summary['decoder_config_switches']}",
        f"- 解码器创建次数: {summary['decoder_init_calls']}；"
        f"重新初始化次数: {summary['decoder_reinit_calls']}",
        f"- 包字节 min/mean/max: {summary['packet_bytes_min']}/"
        f"{summary['packet_bytes_mean']}/{summary['packet_bytes_max']}",
        f"- 单包解码耗时 mean/p50/p95/max (us): "
        f"{summary['decode_us_mean']}/{summary['decode_us_p50']}/"
        f"{summary['decode_us_p95']}/{summary['decode_us_max']}",
        "",
        "## 逐包 TOC 与解码路径",
        "",
        "| # | profile | bytes | TOC | cfg | mode | BW | frame ms | TOC frames | total ms | VAD | LBRR | decode path | decode us | status |",
        "|---:|---|---:|---|---:|---|---|---:|---:|---:|---|---:|---|---:|---|",
    ]
    for record in report["packets"]:
        lines.append(
            f"| {record['index']} | `{record['profile']}` | {record['bytes']} | "
            f"`{record['toc_hex']}` | {record['config']} | {record['mode']} | "
            f"{record['bandwidth']} | {_fmt(record['frame_ms'], 1)} | "
            f"{record['toc_frames']} | {_fmt(record['total_ms'], 1)} | "
            f"{'; '.join(record['vad'])} | "
            f"{'1' if record['lbrr'] else '0'} | {record['decode_path']} | "
            f"{_fmt(record['decode_us'])} | {record['decode_status']} |"
        )

    lines.extend(
        [
            "",
            "## Hybrid 包边界交叉核对",
            "",
            "| # | bytes | code | parsed sizes | parsed offsets | libopus sizes | libopus offsets | match |",
            "|---:|---:|---:|---|---|---|---|---|",
        ]
    )
    for record in report["hybrid_boundaries"]:
        boundary = record["boundary"]
        lines.append(
            f"| {record['index']} | {record['bytes']} | {record['code']} | "
            f"{record['frame_sizes']} | {record['frame_offsets']} | "
            f"{boundary['libopus_frame_sizes']} | {boundary['libopus_frame_offsets']} | "
            f"{'PASS' if record['boundary_match'] else 'FAIL'} |"
        )

    lines.extend(
        [
            "",
            "## TOC 损坏包隔离测试",
            "",
            f"- 目标包: #{fault['packet_index']} `{fault['original_profile']}`",
            f"- TOC: `{fault['original_toc_hex']}` -> `{fault['damaged_toc_hex']}`",
            f"- 损坏后解码状态: `{fault['decode_status']}`，"
            f"输出 {fault['decoded_samples_per_channel']} samples/channel",
            f"- 损坏 TOC 初次解码报错: "
            f"{fault['corrupt_packet_decode_error']}",
            f"- 原时长: {fault['original_duration_samples']} samples；"
            f"PLC 补足: {fault['plc_samples']} samples",
            f"- 损坏包 RMS: {_fmt(fault['rms_dbfs'])} dBFS",
            f"- 全段 RMS: 基线 {_fmt(fault['baseline_rms_dbfs'])} / "
            f"注入后 {_fmt(fault['fault_rms_dbfs'])} dBFS",
            f"- 非零样本比例: {fault['fault_nonzero_ratio'] * 100:.2f}%",
            f"- 最长近静音段: {fault['longest_near_silence_samples']} samples",
            f"- 后续正常解码包: {fault['downstream_non_silent_count']}/"
            f"{fault['downstream_packet_count']} 个非静音；"
            f"最低 RMS {_fmt(fault['downstream_min_rms_dbfs'])} dBFS",
            f"- 结论: {'PASS，仅单包受影响，整段未静音' if fault['whole_stream_not_silenced'] else 'FAIL'}",
            "",
            "## CRC 对照样例",
            "",
            f"- 样例数: {report['sample_verification']['sample_count']} 对",
            f"- 合法组 CRC 全部正确: {report['sample_verification']['all_valid_crc_ok']}",
            f"- CRC 错误组全部被校验发现: "
            f"{report['sample_verification']['all_crc_errors_detected']}",
            f"- CRC 错误组载荷仍可解码: "
            f"{report['sample_verification']['all_crc_error_payloads_decode']}",
            f"- 合法/错误组解码 PCM 完全相同: "
            f"{report['sample_verification']['all_payload_decodes_equal']}",
            "",
            "| sample | mode | bytes | cfg | frame ms | valid CRC | error CRC | valid CRC ok | error detected | PCM equal |",
            "|---|---|---:|---:|---:|---|---|---|---|---|",
        ]
    )
    verification_by_id = {
        check["sample_id"]: check
        for check in report["sample_verification"]["checks"]
    }
    bad_by_id = {row["sample_id"]: row for row in report["samples"]["crc_error"]}
    for row in report["samples"]["valid"]:
        bad = bad_by_id[row["sample_id"]]
        check = verification_by_id[row["sample_id"]]
        lines.append(
            f"| `{row['sample_id']}` | {row['mode']} | {row['bytes']} | "
            f"{row['config']} | {_fmt(row['frame_ms'], 1)} | "
            f"`{row['crc32_stored']}` | `{bad['crc32_stored']}` | "
            f"{row['crc_ok']} | {check['crc_error_detected']} | "
            f"{check['payload_decode_equal']} |"
        )
    lines.extend(
        [
            "",
            "## 说明",
            "",
            "- 原生 Opus 包（RFC 6716）没有包内 CRC；CRC 对照样例使用独立 JSONL 验收封装，"
            "CRC32 覆盖原始 Opus 包字节。`crc_error_frames.jsonl` 仅修改封装中的 `crc32_stored`，"
            "`packet_hex` 与合法组完全相同。",
            "- VAD 仅存在于 SILK 层；Hybrid 使用其中的 SILK VAD，CELT-only 标为 N/A。",
            "- 帧占比按解码层计：Hybrid 同时计入 1 个 SILK 帧和 1 个 CELT 帧；"
            "40/60 ms SILK Opus 帧按内部 20 ms SILK 帧计数。",
            "",
        ]
    )
    return "\n".join(lines)


def generate(out_dir: Path) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    samples_dir = out_dir / "samples"
    samples_dir.mkdir(parents=True, exist_ok=True)
    lib = LibOpus()
    matrix, silent_packet = build_packet_matrix(lib)
    sequence = build_sequence(matrix, silent_packet)

    records, baseline_pcm, session = run_sequence(lib, sequence)
    summary = summarize_records(records, session)
    fault_index = next(
        index for index, record in enumerate(records)
        if record["profile"] == "celt-FB-20ms"
    )
    fault_records, fault_pcm, fault_detail = run_fault_injection(
        lib, sequence, fault_index
    )
    fault_summary = summarize_fault(
        baseline_pcm, fault_pcm, fault_records, fault_detail
    )

    valid_rows, bad_rows = build_sample_sets(sequence, silent_packet)
    write_jsonl(samples_dir / "valid_frames.jsonl", valid_rows)
    write_jsonl(samples_dir / "crc_error_frames.jsonl", bad_rows)
    sample_verification = verify_sample_sets(lib, samples_dir)

    write_wav(out_dir / "valid_sequence.wav", baseline_pcm)
    write_wav(out_dir / "fault_injected_sequence.wav", fault_pcm)

    report = {
        "generated_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "summary": summary,
        "packets": records,
        "hybrid_boundaries": [
            record for record in records if record["mode"] == "Hybrid"
        ],
        "fault_injection": fault_summary,
        "samples": {
            "valid_file": str(samples_dir / "valid_frames.jsonl"),
            "crc_error_file": str(samples_dir / "crc_error_frames.jsonl"),
            "valid": valid_rows,
            "crc_error": bad_rows,
        },
        "sample_verification": sample_verification,
    }
    (out_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_dir / "report.md").write_text(render_markdown(report), encoding="utf-8")
    return report


def print_summary(report: dict[str, Any], out_dir: Path) -> None:
    summary = report["summary"]
    fault = report["fault_injection"]
    print(f"libopus={summary['libopus_version']}")
    print(
        f"packets={summary['packet_count']} bytes={summary['packet_bytes_total']} "
        f"decoder_init_calls={summary['decoder_init_calls']} "
        f"mode_switches={summary['decoder_mode_switches']}"
    )
    print(
        f"silk_frames={summary['silk_decoder_frames']} "
        f"({summary['silk_frame_ratio'] * 100:.2f}%) "
        f"celt_frames={summary['celt_decoder_frames']} "
        f"({summary['celt_frame_ratio'] * 100:.2f}%)"
    )
    print(
        f"decode_us mean/p50/p95/max="
        f"{summary['decode_us_mean']}/{summary['decode_us_p50']}/"
        f"{summary['decode_us_p95']}/{summary['decode_us_max']}"
    )
    print(f"hybrid_boundary_match={summary['all_boundaries_match_libopus']}")
    print(
        f"fault_injection={fault['original_toc_hex']}->{fault['damaged_toc_hex']} "
        f"status={fault['decode_status']} "
        f"whole_stream_not_silenced={fault['whole_stream_not_silenced']}"
    )
    print(
        f"crc_samples={report['sample_verification']['sample_count']} "
        f"valid_ok={report['sample_verification']['all_valid_crc_ok']} "
        f"errors_detected={report['sample_verification']['all_crc_errors_detected']} "
        f"payload_equal={report['sample_verification']['all_payload_decodes_equal']}"
    )
    print(f"report={out_dir / 'report.md'}")
    print(f"json={out_dir / 'report.json'}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command")
    generate_parser = subparsers.add_parser("generate", help="generate report and samples")
    generate_parser.add_argument("--out", default="artifacts")
    verify_parser = subparsers.add_parser("verify-samples", help="verify CRC sample pair")
    verify_parser.add_argument("--out", default="artifacts")
    args = parser.parse_args(argv)

    if args.command == "verify-samples":
        lib = LibOpus()
        result = verify_sample_sets(lib, Path(args.out) / "samples")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if (
            result["all_valid_crc_ok"]
            and result["all_crc_errors_detected"]
            and result["all_crc_error_payloads_decode"]
            and result["all_payload_decodes_equal"]
        ) else 1

    out_dir = Path(args.out if args.command == "generate" else "artifacts")
    report = generate(out_dir)
    print_summary(report, out_dir)
    ok = (
        report["summary"]["all_decodes_ok"]
        and report["summary"]["all_boundaries_match_libopus"]
        and not report["summary"]["decoder_reinitialization_required"]
        and report["fault_injection"]["whole_stream_not_silenced"]
        and report["sample_verification"]["all_valid_crc_ok"]
        and report["sample_verification"]["all_crc_errors_detected"]
        and report["sample_verification"]["all_payload_decodes_equal"]
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
