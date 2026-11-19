from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass

from .model import APNGError


PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

_VALID_DEPTHS = {
    0: {1, 2, 4, 8, 16},
    2: {8, 16},
    3: {1, 2, 4, 8},
    4: {8, 16},
    6: {8, 16},
}

_CHANNELS = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}
_ADAM7 = (
    (0, 0, 8, 8),
    (4, 0, 8, 8),
    (0, 4, 4, 8),
    (2, 0, 4, 4),
    (0, 2, 2, 4),
    (1, 0, 2, 2),
    (0, 1, 1, 2),
)


@dataclass(frozen=True)
class Chunk:
    type: str
    data: bytes
    offset: int


@dataclass(frozen=True)
class PNGHeader:
    width: int
    height: int
    bit_depth: int
    color_type: int
    interlace: int

    @property
    def bits_per_pixel(self) -> int:
        return self.bit_depth * _CHANNELS[self.color_type]

    @property
    def filter_bpp(self) -> int:
        return max(1, (self.bits_per_pixel + 7) // 8)


@dataclass
class PNGInfo:
    header: PNGHeader
    palette: bytes | None
    transparency: bytes | None


def parse_chunks(data: bytes, check_crc: bool = True) -> list[Chunk]:
    if not data.startswith(PNG_SIGNATURE):
        raise APNGError("not a PNG/APNG file: bad signature")
    chunks: list[Chunk] = []
    offset = len(PNG_SIGNATURE)
    while offset < len(data):
        if offset + 12 > len(data):
            raise APNGError(f"truncated PNG chunk at offset {offset}")
        length = struct.unpack(">I", data[offset : offset + 4])[0]
        chunk_type = data[offset + 4 : offset + 8]
        body_start = offset + 8
        body_end = body_start + length
        crc_end = body_end + 4
        if crc_end > len(data):
            raise APNGError(f"truncated PNG chunk at offset {offset}")
        try:
            name = chunk_type.decode("ascii")
        except UnicodeDecodeError as exc:
            raise APNGError(f"invalid PNG chunk type at offset {offset}") from exc
        body = data[body_start:body_end]
        if check_crc:
            expected = struct.unpack(">I", data[body_end:crc_end])[0]
            actual = zlib.crc32(chunk_type + body) & 0xFFFFFFFF
            if actual != expected:
                raise APNGError(f"CRC mismatch in {name} chunk at offset {offset}")
        chunks.append(Chunk(name, body, offset))
        offset = crc_end
        if name == "IEND":
            break
    if not chunks or chunks[0].type != "IHDR":
        raise APNGError("PNG must start with IHDR")
    if chunks[-1].type != "IEND":
        raise APNGError("PNG is missing IEND")
    return chunks


def parse_header(ihdr: bytes) -> PNGHeader:
    if len(ihdr) != 13:
        raise APNGError("IHDR must contain 13 bytes")
    width, height, depth, color, compression, filtering, interlace = (
        struct.unpack(">IIBBBBB", ihdr)
    )
    if width <= 0 or height <= 0:
        raise APNGError("PNG dimensions must be positive")
    if width > 0x7FFFFFFF or height > 0x7FFFFFFF:
        raise APNGError("PNG dimensions exceed the format limit")
    if color not in _VALID_DEPTHS:
        raise APNGError(f"unsupported PNG color type {color}")
    if depth not in _VALID_DEPTHS[color]:
        raise APNGError(f"invalid bit depth {depth} for PNG color type {color}")
    if compression != 0 or filtering != 0:
        raise APNGError("unsupported PNG compression or filter method")
    if interlace not in (0, 1):
        raise APNGError(f"invalid PNG interlace method {interlace}")
    return PNGHeader(width, height, depth, color, interlace)


def png_info(chunks: list[Chunk]) -> PNGInfo:
    header = parse_header(chunks[0].data)
    palette = None
    transparency = None
    for chunk in chunks[1:]:
        if chunk.type == "PLTE":
            palette = chunk.data
        elif chunk.type == "tRNS":
            transparency = chunk.data
    if header.color_type == 3:
        if palette is None or not palette:
            raise APNGError("indexed PNG is missing PLTE")
        if len(palette) % 3:
            raise APNGError("PLTE length is not divisible by three")
    return PNGInfo(header, palette, transparency)


def _row_layout(width: int, header: PNGHeader) -> tuple[int, int]:
    row_bytes = (width * header.bits_per_pixel + 7) // 8
    return row_bytes, header.filter_bpp


def _paeth(left: int, above: int, upper_left: int) -> int:
    estimate = left + above - upper_left
    pa = abs(estimate - left)
    pb = abs(estimate - above)
    pc = abs(estimate - upper_left)
    if pa <= pb and pa <= pc:
        return left
    if pb <= pc:
        return above
    return upper_left


def _unfilter_row(
    filtered: bytes, previous: bytes, bpp: int, filter_type: int
) -> bytes:
    if filter_type == 0:
        return bytes(filtered)
    out = bytearray(len(filtered))
    if filter_type == 1:
        for i, value in enumerate(filtered):
            left = out[i - bpp] if i >= bpp else 0
            out[i] = (value + left) & 0xFF
    elif filter_type == 2:
        for i, value in enumerate(filtered):
            out[i] = (value + previous[i]) & 0xFF
    elif filter_type == 3:
        for i, value in enumerate(filtered):
            left = out[i - bpp] if i >= bpp else 0
            out[i] = (value + ((left + previous[i]) >> 1)) & 0xFF
    elif filter_type == 4:
        for i, value in enumerate(filtered):
            left = out[i - bpp] if i >= bpp else 0
            upper_left = previous[i - bpp] if i >= bpp else 0
            out[i] = (value + _paeth(left, previous[i], upper_left)) & 0xFF
    else:
        raise APNGError(f"invalid PNG filter type {filter_type}")
    return bytes(out)


def _rows_from_raw(
    raw: bytes, width: int, height: int, header: PNGHeader
) -> list[bytes]:
    row_bytes, bpp = _row_layout(width, header)
    expected = height * (row_bytes + 1)
    if len(raw) != expected:
        raise APNGError(
            f"PNG frame has {len(raw)} decompressed bytes, expected {expected}"
        )
    rows: list[bytes] = []
    previous = bytes(row_bytes)
    offset = 0
    for _ in range(height):
        filter_type = raw[offset]
        offset += 1
        row = _unfilter_row(
            raw[offset : offset + row_bytes], previous, bpp, filter_type
        )
        rows.append(row)
        previous = row
        offset += row_bytes
    return rows


def _decompress(compressed: bytes) -> bytes:
    try:
        return zlib.decompress(compressed)
    except zlib.error as exc:
        raise APNGError(f"invalid compressed PNG frame data: {exc}") from exc


def _samples_from_row(
    row: bytes,
    width: int,
    header: PNGHeader,
    palette_mode: bool,
) -> list[tuple[int, ...]]:
    channels = _CHANNELS[header.color_type]
    depth = header.bit_depth
    values: list[int] = []
    if depth == 8:
        values = list(row[: width * channels])
    elif depth == 16:
        values = [
            struct.unpack(">H", row[i : i + 2])[0]
            for i in range(0, width * channels * 2, 2)
        ]
    else:
        mask = (1 << depth) - 1
        for byte in row:
            for shift in range(8 - depth, -1, -depth):
                values.append((byte >> shift) & mask)
                if len(values) == width:
                    break
            if len(values) == width:
                break
    if palette_mode:
        return [(value,) for value in values]
    return [
        tuple(values[i : i + channels])
        for i in range(0, len(values), channels)
    ]


def _sample_to_byte(value: int, depth: int) -> int:
    if depth == 8:
        return value
    if depth == 16:
        return (value * 255 + 32767) // 65535
    maximum = (1 << depth) - 1
    return (value * 255 + maximum // 2) // maximum


def _rgba_from_samples(
    samples: list[tuple[int, ...]],
    info: PNGInfo,
) -> bytes:
    header = info.header
    depth = header.bit_depth
    color_type = header.color_type
    out = bytearray()
    trns = info.transparency

    def scale(value: int) -> int:
        return _sample_to_byte(value, depth)

    for values in samples:
        if color_type == 0:
            gray = scale(values[0])
            alpha = 255
            if trns is not None and len(trns) >= 2:
                if values[0] == struct.unpack(">H", trns[:2])[0]:
                    alpha = 0
            out.extend((gray, gray, gray, alpha))
        elif color_type == 2:
            red, green, blue = (scale(value) for value in values)
            alpha = 255
            if trns is not None and len(trns) >= 6:
                key = struct.unpack(">HHH", trns[:6])
                if values == key:
                    alpha = 0
            out.extend((red, green, blue, alpha))
        elif color_type == 3:
            index = values[0]
            palette = info.palette or b""
            base = index * 3
            if base + 2 >= len(palette):
                raise APNGError(f"palette index {index} is out of range")
            alpha = 255
            if trns is not None and index < len(trns):
                alpha = trns[index]
            out.extend((palette[base], palette[base + 1], palette[base + 2], alpha))
        elif color_type == 4:
            gray = scale(values[0])
            alpha = scale(values[1])
            out.extend((gray, gray, gray, alpha))
        else:
            out.extend(scale(value) for value in values)
    return bytes(out)


def decode_png_frame(
    compressed: bytes,
    width: int,
    height: int,
    info: PNGInfo,
) -> bytes:
    header = info.header
    raw = _decompress(compressed)
    if header.interlace == 0:
        rows = _rows_from_raw(raw, width, height, header)
        samples: list[tuple[int, ...]] = []
        for row in rows:
            samples.extend(
                _samples_from_row(
                    row, width, header, header.color_type == 3
                )
            )
        return _rgba_from_samples(samples, info)

    samples = [None] * (width * height)
    offset = 0
    for start_x, start_y, step_x, step_y in _ADAM7:
        pass_width = (
            0 if start_x >= width else (width - start_x + step_x - 1) // step_x
        )
        pass_height = (
            0
            if start_y >= height
            else (height - start_y + step_y - 1) // step_y
        )
        if pass_width == 0 or pass_height == 0:
            continue
        row_bytes, _ = _row_layout(pass_width, header)
        size = pass_height * (row_bytes + 1)
        segment = raw[offset : offset + size]
        offset += size
        pass_rows = _rows_from_raw(
            segment, pass_width, pass_height, header
        )
        for pass_y, row in enumerate(pass_rows):
            values = _samples_from_row(
                row, pass_width, header, header.color_type == 3
            )
            for pass_x, sample in enumerate(values):
                x = start_x + pass_x * step_x
                y = start_y + pass_y * step_y
                samples[y * width + x] = sample
    if offset != len(raw) or any(sample is None for sample in samples):
        raise APNGError("interlaced PNG frame has incomplete scanline data")
    return _rgba_from_samples(samples, info)
