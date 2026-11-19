from __future__ import annotations

import struct
from pathlib import Path

from .model import APNGAnimation, APNGFrame, APNGError, FrameControl
from .png import Chunk, decode_png_frame, parse_chunks, png_info


def _parse_fcTL(data: bytes, index: int) -> FrameControl:
    if len(data) != 26:
        raise APNGError(f"fcTL frame {index} must contain 26 bytes")
    values = struct.unpack(">IIIIIHHBB", data)
    control = FrameControl(*values)
    if control.width <= 0 or control.height <= 0:
        raise APNGError(f"fcTL frame {index} has an empty region")
    if control.dispose_op not in (0, 1, 2):
        raise APNGError(
            f"fcTL frame {index} has invalid dispose_op {control.dispose_op}"
        )
    if control.blend_op not in (0, 1):
        raise APNGError(
            f"fcTL frame {index} has invalid blend_op {control.blend_op}"
        )
    return control


def _chunk_indices(chunks: list[Chunk], chunk_type: str) -> list[int]:
    return [index for index, chunk in enumerate(chunks) if chunk.type == chunk_type]


def _extract_frame_streams(
    chunks: list[Chunk],
    controls: list[FrameControl],
    default_image_is_frame: bool,
) -> list[bytes]:
    streams = [bytearray() for _ in controls]
    current = 0 if default_image_is_frame else None
    seen_controls = 0
    idat_seen = False

    for chunk in chunks[1:]:
        if chunk.type == "IDAT":
            idat_seen = True
            if default_image_is_frame:
                if current != 0:
                    raise APNGError(
                        "IDAT appears after another animation frame started"
                    )
                streams[0].extend(chunk.data)
        elif chunk.type == "fcTL":
            seen_controls += 1
            current = seen_controls - 1
            if current >= len(controls):
                raise APNGError("fcTL count changed while reading frame data")
        elif chunk.type == "fdAT":
            if not idat_seen and default_image_is_frame:
                raise APNGError("fdAT cannot replace the default image")
            if current is None:
                raise APNGError("fdAT appears before the first fcTL")
            if len(chunk.data) < 4:
                raise APNGError("fdAT chunk is shorter than its sequence number")
            streams[current].extend(chunk.data[4:])

    for index, stream in enumerate(streams):
        if not stream:
            raise APNGError(f"animation frame {index} has no image data")
    return [bytes(stream) for stream in streams]


def _validate_sequence_numbers(chunks: list[Chunk]) -> None:
    expected = 0
    for chunk in chunks:
        sequence = None
        if chunk.type == "fcTL":
            if len(chunk.data) < 4:
                raise APNGError("fcTL chunk is too short")
            sequence = struct.unpack(">I", chunk.data[:4])[0]
        elif chunk.type == "fdAT":
            if len(chunk.data) < 4:
                raise APNGError("fdAT chunk is too short")
            sequence = struct.unpack(">I", chunk.data[:4])[0]
        if sequence is None:
            continue
        if sequence != expected:
            raise APNGError(
                f"APNG sequence number is {sequence}, expected {expected}"
            )
        expected += 1


def _composite_over(
    destination: bytearray,
    source: bytes,
    canvas_width: int,
    x_offset: int,
    y_offset: int,
    width: int,
    height: int,
) -> None:
    for row in range(height):
        destination_start = ((y_offset + row) * canvas_width + x_offset) * 4
        source_start = row * width * 4
        for column in range(width):
            src = source_start + column * 4
            dst = destination_start + column * 4
            source_alpha = source[src + 3]
            if source_alpha == 0:
                continue
            if source_alpha == 255:
                destination[dst : dst + 4] = source[src : src + 4]
                continue
            destination_alpha = destination[dst + 3]
            out_alpha = source_alpha + (
                destination_alpha * (255 - source_alpha) + 127
            ) // 255
            if out_alpha == 0:
                destination[dst : dst + 4] = b"\x00\x00\x00\x00"
                continue
            denominator = out_alpha * 255
            for channel in range(3):
                numerator = (
                    source[src + channel] * source_alpha * 255
                    + destination[dst + channel]
                    * destination_alpha
                    * (255 - source_alpha)
                )
                destination[dst + channel] = min(
                    255, (numerator + denominator // 2) // denominator
                )
            destination[dst + 3] = out_alpha


def _replace_region(
    canvas: bytearray,
    pixels: bytes,
    canvas_width: int,
    x_offset: int,
    y_offset: int,
    width: int,
    height: int,
) -> None:
    row_bytes = width * 4
    for row in range(height):
        source_start = row * row_bytes
        destination_start = (
            (y_offset + row) * canvas_width + x_offset
        ) * 4
        canvas[destination_start : destination_start + row_bytes] = pixels[
            source_start : source_start + row_bytes
        ]


def _clear_region(
    canvas: bytearray,
    canvas_width: int,
    x_offset: int,
    y_offset: int,
    width: int,
    height: int,
) -> None:
    row = bytes(width * 4)
    for y in range(y_offset, y_offset + height):
        start = (y * canvas_width + x_offset) * 4
        canvas[start : start + len(row)] = row


def _render_frames(
    canvas_width: int,
    canvas_height: int,
    frames: list[APNGFrame],
) -> list[bytes]:
    canvas = bytearray(canvas_width * canvas_height * 4)
    rendered: list[bytes] = []
    for frame in frames:
        control = frame.control
        before = bytes(canvas) if control.dispose_op == 2 else None
        if control.blend_op == 0:
            _replace_region(
                canvas,
                frame.pixels,
                canvas_width,
                control.x,
                control.y,
                control.width,
                control.height,
            )
        else:
            _composite_over(
                canvas,
                frame.pixels,
                canvas_width,
                control.x,
                control.y,
                control.width,
                control.height,
            )
        rendered.append(bytes(canvas))
        if control.dispose_op == 1:
            _clear_region(
                canvas,
                canvas_width,
                control.x,
                control.y,
                control.width,
                control.height,
            )
        elif control.dispose_op == 2:
            assert before is not None
            start = (
                (control.y * canvas_width + control.x) * 4
            )
            for row in range(control.height):
                source = start + row * canvas_width * 4
                length = control.width * 4
                canvas[source : source + length] = before[
                    source : source + length
                ]
    return rendered


def parse_apng_bytes(data: bytes, path: Path | str = "<memory>") -> APNGAnimation:
    chunks = parse_chunks(data)
    info = png_info(chunks)
    header = info.header

    actl_indices = _chunk_indices(chunks, "acTL")
    if len(actl_indices) != 1:
        raise APNGError("APNG must contain exactly one acTL chunk")
    actl_index = actl_indices[0]
    if actl_index > min(_chunk_indices(chunks, "IDAT"), default=len(chunks)):
        raise APNGError("acTL must appear before IDAT")
    fctl_indices = _chunk_indices(chunks, "fcTL")
    if fctl_indices and actl_index > fctl_indices[0]:
        raise APNGError("acTL must appear before the first fcTL")
    if len(chunks[actl_index].data) != 8:
        raise APNGError("acTL must contain eight bytes")
    declared_frames, num_plays = struct.unpack(">II", chunks[actl_index].data)
    if declared_frames <= 0:
        raise APNGError("acTL declares zero animation frames")
    if num_plays > 0xFFFF:
        raise APNGError("APNG loop count exceeds the WebP 16-bit limit")

    frame_controls: list[FrameControl] = []
    for index in _chunk_indices(chunks, "fcTL"):
        frame_controls.append(
            _parse_fcTL(chunks[index].data, len(frame_controls))
        )
    if len(frame_controls) != declared_frames:
        raise APNGError(
            f"acTL declares {declared_frames} frames, found "
            f"{len(frame_controls)} fcTL chunks"
        )
    _validate_sequence_numbers(chunks)

    idat_indices = _chunk_indices(chunks, "IDAT")
    if not idat_indices:
        raise APNGError("APNG has no IDAT image data")
    for index in range(idat_indices[0], idat_indices[-1] + 1):
        if chunks[index].type != "IDAT":
            raise APNGError("IDAT chunks must be consecutive")
    default_image_is_frame = bool(fctl_indices and fctl_indices[0] < idat_indices[0])

    for index, control in enumerate(frame_controls):
        if control.x + control.width > header.width:
            raise APNGError(f"frame {index} extends beyond the canvas width")
        if control.y + control.height > header.height:
            raise APNGError(f"frame {index} extends beyond the canvas height")
    first_control = frame_controls[0]
    if default_image_is_frame and (
        first_control.width != header.width
        or first_control.height != header.height
    ):
        raise APNGError("the default image frame must use the full canvas size")

    streams = _extract_frame_streams(
        chunks, frame_controls, default_image_is_frame
    )
    frames: list[APNGFrame] = []
    for index, (control, stream) in enumerate(zip(frame_controls, streams)):
        pixels = decode_png_frame(
            stream, control.width, control.height, info
        )
        frames.append(APNGFrame(index, control, stream, pixels))

    animation = APNGAnimation(
        path=Path(path),
        width=header.width,
        height=header.height,
        bit_depth=header.bit_depth,
        color_type=header.color_type,
        interlace=header.interlace,
        frames=frames,
        num_plays=num_plays,
        default_image_is_frame=default_image_is_frame,
    )
    animation.rendered = _render_frames(
        header.width, header.height, frames
    )
    return animation


def read_apng(path: Path | str) -> APNGAnimation:
    source = Path(path)
    try:
        data = source.read_bytes()
    except OSError as exc:
        raise APNGError(f"cannot read {source}: {exc}") from exc
    return parse_apng_bytes(data, source)
