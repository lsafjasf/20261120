from __future__ import annotations

import ctypes
import ctypes.util
import os
import struct
from dataclasses import dataclass
from pathlib import Path

from .model import WebPError


_WEBP_ABI = 0x0210
_DEMUX_ABI = 0x0107
_MUX_ABI = 0x0109
_MODE_RGBA = 1
_WEBP_CHUNK_ANMF = 3
_WEBP_MUX_DISPOSE_NONE = 0
_WEBP_MUX_NO_BLEND = 1


class WebPData(ctypes.Structure):
    _fields_ = [
        ("bytes", ctypes.POINTER(ctypes.c_uint8)),
        ("size", ctypes.c_size_t),
    ]


class MuxAnimParams(ctypes.Structure):
    _fields_ = [
        ("bgcolor", ctypes.c_uint32),
        ("loop_count", ctypes.c_int),
    ]


class MuxFrameInfo(ctypes.Structure):
    _fields_ = [
        ("bitstream", WebPData),
        ("x_offset", ctypes.c_int),
        ("y_offset", ctypes.c_int),
        ("duration", ctypes.c_int),
        ("id", ctypes.c_int),
        ("dispose_method", ctypes.c_int),
        ("blend_method", ctypes.c_int),
        ("pad", ctypes.c_uint32 * 1),
    ]


class AnimDecoderOptions(ctypes.Structure):
    _fields_ = [
        ("color_mode", ctypes.c_int),
        ("use_threads", ctypes.c_int),
        ("padding", ctypes.c_uint32 * 7),
    ]


class AnimInfo(ctypes.Structure):
    _fields_ = [
        ("canvas_width", ctypes.c_uint32),
        ("canvas_height", ctypes.c_uint32),
        ("loop_count", ctypes.c_uint32),
        ("bgcolor", ctypes.c_uint32),
        ("frame_count", ctypes.c_uint32),
        ("pad", ctypes.c_uint32 * 4),
    ]


@dataclass(frozen=True)
class ContainerFrame:
    x: int
    y: int
    width: int
    height: int
    duration_ms: int
    lossless: bool
    dispose: int
    blend: int


@dataclass(frozen=True)
class WebPContainer:
    width: int
    height: int
    loop_count: int
    frames: list[ContainerFrame]

    @property
    def all_lossless(self) -> bool:
        return all(frame.lossless for frame in self.frames)

    @property
    def durations_ms(self) -> list[int]:
        return [frame.duration_ms for frame in self.frames]


@dataclass
class DecodedWebP:
    width: int
    height: int
    loop_count: int
    frames: list[bytes]
    timestamps_ms: list[int]
    loop_boundary_frame: bytes | None
    loop_boundary_timestamp: int | None


def _library_candidates(name: str) -> list[str]:
    candidates = [name, ctypes.util.find_library(name)]
    if name == "webp":
        candidates.extend(["libwebp.so.7", "libwebp.so"])
    elif name == "webpmux":
        candidates.extend(["libwebpmux.so.3", "libwebpmux.so"])
    elif name == "webpdemux":
        candidates.extend(["libwebpdemux.so.2", "libwebpdemux.so"])
    roots = [
        os.environ.get("WEBP_LIBRARY_DIR"),
        str(Path.home() / ".local/apngwebp/usr/lib/x86_64-linux-gnu"),
        "/usr/lib/x86_64-linux-gnu",
        "/usr/local/lib",
    ]
    for root in roots:
        if root:
            candidates.append(str(Path(root) / f"lib{name}.so"))
    return [candidate for candidate in candidates if candidate]


def _load_library(name: str) -> ctypes.CDLL:
    errors: list[str] = []
    for candidate in _library_candidates(name):
        try:
            return ctypes.CDLL(candidate)
        except OSError as exc:
            errors.append(f"{candidate}: {exc}")
    raise WebPError(
        f"unable to load lib{name}; install libwebp/libwebpdemux/libwebpmux "
        f"({'; '.join(errors)})"
    )


_webp = _load_library("webp")
_webpmux = _load_library("webpmux")
_webpdemux = _load_library("webpdemux")

_webp.WebPEncodeLosslessRGBA.argtypes = [
    ctypes.POINTER(ctypes.c_uint8),
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.POINTER(ctypes.POINTER(ctypes.c_uint8)),
]
_webp.WebPEncodeLosslessRGBA.restype = ctypes.c_size_t
_webp.WebPFree.argtypes = [ctypes.c_void_p]
_webp.WebPFree.restype = None

_webpmux.WebPNewInternal.argtypes = [ctypes.c_int]
_webpmux.WebPNewInternal.restype = ctypes.c_void_p
_webpmux.WebPMuxDelete.argtypes = [ctypes.c_void_p]
_webpmux.WebPMuxDelete.restype = None
_webpmux.WebPMuxSetCanvasSize.argtypes = [
    ctypes.c_void_p,
    ctypes.c_int,
    ctypes.c_int,
]
_webpmux.WebPMuxSetCanvasSize.restype = ctypes.c_int
_webpmux.WebPMuxSetAnimationParams.argtypes = [
    ctypes.c_void_p,
    ctypes.POINTER(MuxAnimParams),
]
_webpmux.WebPMuxSetAnimationParams.restype = ctypes.c_int
_webpmux.WebPMuxPushFrame.argtypes = [
    ctypes.c_void_p,
    ctypes.POINTER(MuxFrameInfo),
    ctypes.c_int,
]
_webpmux.WebPMuxPushFrame.restype = ctypes.c_int
_webpmux.WebPMuxAssemble.argtypes = [ctypes.c_void_p, ctypes.POINTER(WebPData)]
_webpmux.WebPMuxAssemble.restype = ctypes.c_int

_webpdemux.WebPAnimDecoderOptionsInitInternal.argtypes = [
    ctypes.POINTER(AnimDecoderOptions),
    ctypes.c_int,
]
_webpdemux.WebPAnimDecoderOptionsInitInternal.restype = ctypes.c_int
_webpdemux.WebPAnimDecoderNewInternal.argtypes = [
    ctypes.POINTER(WebPData),
    ctypes.POINTER(AnimDecoderOptions),
    ctypes.c_int,
]
_webpdemux.WebPAnimDecoderNewInternal.restype = ctypes.c_void_p
_webpdemux.WebPAnimDecoderGetInfo.argtypes = [
    ctypes.c_void_p,
    ctypes.POINTER(AnimInfo),
]
_webpdemux.WebPAnimDecoderGetInfo.restype = ctypes.c_int
_webpdemux.WebPAnimDecoderHasMoreFrames.argtypes = [ctypes.c_void_p]
_webpdemux.WebPAnimDecoderHasMoreFrames.restype = ctypes.c_int
_webpdemux.WebPAnimDecoderGetNext.argtypes = [
    ctypes.c_void_p,
    ctypes.POINTER(ctypes.POINTER(ctypes.c_uint8)),
    ctypes.POINTER(ctypes.c_int),
]
_webpdemux.WebPAnimDecoderGetNext.restype = ctypes.c_int
_webpdemux.WebPAnimDecoderReset.argtypes = [ctypes.c_void_p]
_webpdemux.WebPAnimDecoderReset.restype = None
_webpdemux.WebPAnimDecoderDelete.argtypes = [ctypes.c_void_p]
_webpdemux.WebPAnimDecoderDelete.restype = None


def _u24(data: bytes) -> int:
    return int.from_bytes(data, "little")


def parse_webp_container(data: bytes) -> WebPContainer:
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        raise WebPError("not a RIFF WebP file")
    riff_size = struct.unpack("<I", data[4:8])[0]
    end = min(len(data), 8 + riff_size)
    offset = 12
    width = height = 0
    loop_count: int | None = None
    frames: list[ContainerFrame] = []

    while offset + 8 <= end:
        fourcc = data[offset : offset + 4]
        size = struct.unpack("<I", data[offset + 4 : offset + 8])[0]
        body_start = offset + 8
        body_end = body_start + size
        if body_end > end:
            raise WebPError(f"truncated WebP chunk {fourcc!r}")
        body = data[body_start:body_end]
        if fourcc == b"VP8X":
            if len(body) < 10:
                raise WebPError("VP8X chunk is too short")
            width = _u24(body[4:7]) + 1
            height = _u24(body[7:10]) + 1
        elif fourcc == b"ANIM":
            if len(body) < 6:
                raise WebPError("ANIM chunk is too short")
            loop_count = struct.unpack("<H", body[4:6])[0]
        elif fourcc == b"ANMF":
            if len(body) < 16:
                raise WebPError("ANMF chunk is too short")
            frame_x = _u24(body[0:3]) * 2
            frame_y = _u24(body[3:6]) * 2
            frame_width = _u24(body[6:9]) + 1
            frame_height = _u24(body[9:12]) + 1
            duration = _u24(body[12:15])
            flags = body[15]
            nested = body[16:]
            nested_offset = 0
            has_vp8l = False
            has_vp8 = False
            while nested_offset + 8 <= len(nested):
                inner = nested[nested_offset : nested_offset + 4]
                inner_size = struct.unpack(
                    "<I", nested[nested_offset + 4 : nested_offset + 8]
                )[0]
                if nested_offset + 8 + inner_size > len(nested):
                    raise WebPError("truncated image chunk inside ANMF")
                if inner == b"VP8L":
                    has_vp8l = True
                elif inner == b"VP8 ":
                    has_vp8 = True
                nested_offset += 8 + inner_size + (inner_size & 1)
            if width and (
                frame_x + frame_width > width
                or frame_y + frame_height > height
            ):
                raise WebPError("WebP frame extends beyond its canvas")
            frames.append(
                ContainerFrame(
                    x=frame_x,
                    y=frame_y,
                    width=frame_width,
                    height=frame_height,
                    duration_ms=duration,
                    lossless=has_vp8l and not has_vp8,
                    dispose=flags & 1,
                    blend=(flags >> 1) & 1,
                )
            )
        offset = body_end + (size & 1)

    if width <= 0 or height <= 0:
        raise WebPError("WebP has no valid VP8X canvas")
    if loop_count is None:
        raise WebPError("WebP is not an animation: ANIM chunk is missing")
    if not frames:
        raise WebPError("WebP animation contains no ANMF frames")
    return WebPContainer(width, height, loop_count, frames)


def _encode_lossless_frame(pixels: bytes, width: int, height: int) -> bytes:
    expected = width * height * 4
    if len(pixels) != expected:
        raise WebPError(
            f"frame buffer has {len(pixels)} bytes, expected {expected}"
        )
    source = (ctypes.c_uint8 * len(pixels)).from_buffer_copy(pixels)
    output = ctypes.POINTER(ctypes.c_uint8)()
    size = _webp.WebPEncodeLosslessRGBA(
        source, width, height, width * 4, ctypes.byref(output)
    )
    if not size or not output:
        raise WebPError("libwebp failed to encode a lossless frame")
    try:
        return ctypes.string_at(output, size)
    finally:
        _webp.WebPFree(output)


def encode_animation(
    frames: list[bytes],
    width: int,
    height: int,
    durations_ms: list[int],
    loop_count: int,
) -> bytes:
    if not frames:
        raise WebPError("cannot encode an animation without frames")
    if len(frames) != len(durations_ms):
        raise WebPError("frame and duration counts differ")
    if not (0 <= loop_count <= 0xFFFF):
        raise WebPError("WebP loop count must be between 0 and 65535")
    if width <= 0 or height <= 0 or width > 16383 or height > 16383:
        raise WebPError("WebP canvas dimensions are out of range")

    mux = _webpmux.WebPNewInternal(_MUX_ABI)
    if not mux:
        raise WebPError("libwebp could not create a mux object")
    try:
        result = _webpmux.WebPMuxSetCanvasSize(mux, width, height)
        if result != 1:
            raise WebPError(f"libwebp rejected the canvas (error {result})")
        params = MuxAnimParams(bgcolor=0, loop_count=loop_count)
        result = _webpmux.WebPMuxSetAnimationParams(
            mux, ctypes.byref(params)
        )
        if result != 1:
            raise WebPError(
                f"libwebp rejected animation parameters (error {result})"
            )
        for index, (pixels, duration) in enumerate(
            zip(frames, durations_ms)
        ):
            if not (1 <= duration <= 0xFFFFFF):
                raise WebPError(
                    f"frame {index} duration {duration}ms is outside WebP limits"
                )
            encoded = _encode_lossless_frame(pixels, width, height)
            source = (ctypes.c_uint8 * len(encoded)).from_buffer_copy(encoded)
            bitstream = WebPData(
                ctypes.cast(source, ctypes.POINTER(ctypes.c_uint8)),
                len(encoded),
            )
            frame_info = MuxFrameInfo(
                bitstream=bitstream,
                x_offset=0,
                y_offset=0,
                duration=duration,
                id=_WEBP_CHUNK_ANMF,
                dispose_method=_WEBP_MUX_DISPOSE_NONE,
                blend_method=_WEBP_MUX_NO_BLEND,
            )
            result = _webpmux.WebPMuxPushFrame(
                mux, ctypes.byref(frame_info), 1
            )
            if result != 1:
                raise WebPError(
                    f"libwebp rejected frame {index} (error {result})"
                )

        assembled = WebPData()
        result = _webpmux.WebPMuxAssemble(mux, ctypes.byref(assembled))
        if result != 1 or not assembled.bytes:
            raise WebPError(
                f"libwebp could not assemble the animation (error {result})"
            )
        try:
            return ctypes.string_at(assembled.bytes, assembled.size)
        finally:
            _webp.WebPFree(assembled.bytes)
    finally:
        _webpmux.WebPMuxDelete(mux)


def decode_animation(data: bytes) -> DecodedWebP:
    if not data:
        raise WebPError("cannot decode an empty WebP")
    source = (ctypes.c_uint8 * len(data)).from_buffer_copy(data)
    webp_data = WebPData(
        ctypes.cast(source, ctypes.POINTER(ctypes.c_uint8)), len(data)
    )
    options = AnimDecoderOptions()
    if not _webpdemux.WebPAnimDecoderOptionsInitInternal(
        ctypes.byref(options), _DEMUX_ABI
    ):
        raise WebPError("libwebpdemux rejected decoder options")
    options.color_mode = _MODE_RGBA
    options.use_threads = 0
    decoder = _webpdemux.WebPAnimDecoderNewInternal(
        ctypes.byref(webp_data), ctypes.byref(options), _DEMUX_ABI
    )
    if not decoder:
        raise WebPError("libwebpdemux could not parse the animation")
    try:
        info = AnimInfo()
        if not _webpdemux.WebPAnimDecoderGetInfo(
            decoder, ctypes.byref(info)
        ):
            raise WebPError("libwebpdemux could not read animation info")
        frame_bytes = info.canvas_width * info.canvas_height * 4
        frames: list[bytes] = []
        timestamps: list[int] = []
        while _webpdemux.WebPAnimDecoderHasMoreFrames(decoder):
            pixels = ctypes.POINTER(ctypes.c_uint8)()
            timestamp = ctypes.c_int()
            if not _webpdemux.WebPAnimDecoderGetNext(
                decoder, ctypes.byref(pixels), ctypes.byref(timestamp)
            ):
                raise WebPError("libwebpdemux failed while decoding a frame")
            frames.append(ctypes.string_at(pixels, frame_bytes))
            timestamps.append(timestamp.value)

        _webpdemux.WebPAnimDecoderReset(decoder)
        boundary_pixels = ctypes.POINTER(ctypes.c_uint8)()
        boundary_timestamp = ctypes.c_int()
        boundary = None
        boundary_time = None
        if _webpdemux.WebPAnimDecoderGetNext(
            decoder,
            ctypes.byref(boundary_pixels),
            ctypes.byref(boundary_timestamp),
        ):
            boundary = ctypes.string_at(boundary_pixels, frame_bytes)
            boundary_time = boundary_timestamp.value
        return DecodedWebP(
            width=info.canvas_width,
            height=info.canvas_height,
            loop_count=info.loop_count,
            frames=frames,
            timestamps_ms=timestamps,
            loop_boundary_frame=boundary,
            loop_boundary_timestamp=boundary_time,
        )
    finally:
        _webpdemux.WebPAnimDecoderDelete(decoder)


def read_webp(path: Path | str) -> bytes:
    try:
        return Path(path).read_bytes()
    except OSError as exc:
        raise WebPError(f"cannot read {path}: {exc}") from exc
