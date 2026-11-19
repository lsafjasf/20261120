from __future__ import annotations

from pathlib import Path
from typing import Any

from .apng import read_apng
from .model import (
    APNGAnimation,
    APNGError,
    Difference,
    TimingPlan,
    VerificationResult,
    WebPError,
)
from .timing import plan_delays
from .webp import (
    decode_animation,
    encode_animation,
    parse_webp_container,
    read_webp,
)


def _frame_difference(
    expected: bytes, actual: bytes, width: int, height: int
) -> Difference:
    if len(expected) != len(actual):
        raise WebPError("frame buffers have different sizes")
    count = 0
    min_x = width
    min_y = height
    max_x = -1
    max_y = -1
    for y in range(height):
        row = y * width * 4
        for x in range(width):
            offset = row + x * 4
            expected_alpha = expected[offset + 3]
            actual_alpha = actual[offset + 3]
            visible = bool(expected_alpha or actual_alpha)
            alpha_mismatch = expected_alpha != actual_alpha
            color_mismatch = visible and expected[offset : offset + 3] != actual[
                offset : offset + 3
            ]
            if not alpha_mismatch and not color_mismatch:
                continue
            count += 1
            min_x = min(min_x, x)
            min_y = min(min_y, y)
            max_x = max(max_x, x)
            max_y = max(max_y, y)
    if count == 0:
        return Difference(0, 0, 0, 0, 0)
    return Difference(
        pixels=count,
        x=min_x,
        y=min_y,
        width=max_x - min_x + 1,
        height=max_y - min_y + 1,
    )


def preflight_apng(path: Path | str) -> dict[str, Any]:
    animation = read_apng(path)
    timing = plan_delays(frame.control for frame in animation.frames)
    frames = []
    for frame, planned in zip(animation.frames, timing.frames):
        control = frame.control
        frames.append(
            {
                "frame_index": frame.index,
                "frame_number": frame.index + 1,
                "region": {
                    "x": control.x,
                    "y": control.y,
                    "width": control.width,
                    "height": control.height,
                },
                "delay": {
                    "numerator": control.delay_num,
                    "denominator": control.delay_den or 100,
                    "exact_ms": str(planned.exact_delay_ms),
                },
                "duration_ms": planned.duration_ms,
                "cumulative_ms": planned.cumulative_ms,
                "lost_precision": planned.lost_precision,
                "dispose": control.dispose_op,
                "blend": control.blend_op,
            }
        )
    return {
        "ok": True,
        "input": str(animation.path),
        "canvas": {"width": animation.width, "height": animation.height},
        "format": {
            "bit_depth": animation.bit_depth,
            "color_type": animation.color_type,
            "interlace": animation.interlace,
        },
        "frame_count": len(animation.frames),
        "loop_count": animation.num_plays,
        "total_duration_ms": timing.total_ms,
        "frames": frames,
        "diagnostics": timing.diagnostics,
    }


def _error(
    frame_index: int,
    reason: str,
    message: str,
    difference: Difference | None = None,
    **details: Any,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "frame_index": frame_index,
        "frame_number": frame_index + 1,
        "reason": reason,
        "message": message,
    }
    if difference is not None:
        payload.update(difference.as_dict())
    payload.update(details)
    return payload


def verify_webp(
    animation: APNGAnimation,
    timing: TimingPlan,
    data: bytes,
) -> VerificationResult:
    errors: list[dict[str, Any]] = []
    checks: dict[str, Any] = {}
    container = None
    decoded = None
    try:
        container = parse_webp_container(data)
        checks["container"] = True
    except WebPError as exc:
        checks["container"] = str(exc)
        errors.append(_error(0, "invalid_webp", str(exc)))
    try:
        decoded = decode_animation(data)
        checks["decode"] = True
    except WebPError as exc:
        checks["decode"] = str(exc)
        errors.append(_error(0, "decode_failed", str(exc)))

    webp_loop = container.loop_count if container else 0
    webp_count = container.frames if container else []
    webp_count_num = len(webp_count)
    durations = [frame.duration_ms for frame in webp_count]
    timestamps = decoded.timestamps_ms if decoded else []

    expected_width = animation.width
    expected_height = animation.height
    if container and (
        container.width != expected_width
        or container.height != expected_height
    ):
        errors.append(
            _error(
                0,
                "canvas_mismatch",
                f"canvas is {container.width}x{container.height}, expected "
                f"{expected_width}x{expected_height}",
            )
        )
    if decoded and (
        decoded.width != expected_width
        or decoded.height != expected_height
    ):
        errors.append(
            _error(
                0,
                "decoded_canvas_mismatch",
                f"decoder returned {decoded.width}x{decoded.height}, expected "
                f"{expected_width}x{expected_height}",
            )
        )

    expected_count = len(animation.frames)
    if webp_count_num != expected_count:
        index = min(webp_count_num, expected_count)
        message = (
            f"WebP has {webp_count_num} frames, expected {expected_count}; "
            "a frame may have been swallowed"
        )
        errors.append(_error(index, "frame_count_mismatch", message))
    if decoded and len(decoded.frames) != expected_count:
        index = min(len(decoded.frames), expected_count)
        errors.append(
            _error(
                index,
                "decoded_frame_count_mismatch",
                f"decoder returned {len(decoded.frames)} frames, expected "
                f"{expected_count}",
            )
        )

    expected_durations = [item.duration_ms for item in timing.frames]
    for index, expected in enumerate(expected_durations):
        if index >= len(durations):
            break
        if durations[index] != expected:
            errors.append(
                _error(
                    index,
                    "duration_mismatch",
                    f"duration is {durations[index]}ms, expected {expected}ms",
                )
            )
    expected_timestamps = [item.cumulative_ms for item in timing.frames]
    for index, expected in enumerate(expected_timestamps):
        if index >= len(timestamps):
            break
        if timestamps[index] != expected:
            errors.append(
                _error(
                    index,
                    "timestamp_mismatch",
                    f"decoder timestamp is {timestamps[index]}ms, expected "
                    f"{expected}ms",
                )
            )
    if decoded and sum(durations) != timing.total_ms:
        errors.append(
            _error(
                max(0, len(durations) - 1),
                "total_duration_mismatch",
                f"total duration is {sum(durations)}ms, expected "
                f"{timing.total_ms}ms",
            )
        )

    for index, frame in enumerate(webp_count):
        if not frame.lossless:
            errors.append(
                _error(
                    index,
                    "frame_not_lossless",
                    "frame does not use a lossless VP8L bitstream",
                )
            )

    if container and container.loop_count != animation.num_plays:
        errors.append(
            _error(
                0,
                "loop_count_mismatch",
                f"WebP loop count is {container.loop_count}, expected "
                f"{animation.num_plays}",
            )
        )
    if decoded and decoded.loop_count != animation.num_plays:
        errors.append(
            _error(
                0,
                "decoded_loop_count_mismatch",
                f"decoder loop count is {decoded.loop_count}, expected "
                f"{animation.num_plays}",
            )
        )

    if decoded:
        if len(decoded.frames) != expected_count:
            frame_limit = min(len(decoded.frames), expected_count)
        else:
            frame_limit = expected_count
        for index in range(frame_limit):
            difference = _frame_difference(
                animation.rendered[index],
                decoded.frames[index],
                expected_width,
                expected_height,
            )
            if not difference.empty:
                errors.append(
                    _error(
                        index,
                        "pixel_or_alpha_mismatch",
                        f"{difference.pixels} visible pixel(s) or alpha "
                        "values differ",
                        difference,
                    )
                )
        if decoded.loop_boundary_frame is not None and animation.rendered:
            difference = _frame_difference(
                animation.rendered[0],
                decoded.loop_boundary_frame,
                expected_width,
                expected_height,
            )
            boundary_timestamp = timing.frames[0].cumulative_ms
            if (
                not difference.empty
                or decoded.loop_boundary_timestamp != boundary_timestamp
            ):
                errors.append(
                    _error(
                        0,
                        "loop_boundary_mismatch",
                        "the first frame after wrapping does not match the "
                        "start of the next loop",
                        difference,
                        expected_timestamp_ms=boundary_timestamp,
                        actual_timestamp_ms=decoded.loop_boundary_timestamp,
                    )
                )
        else:
            errors.append(
                _error(
                    0,
                    "loop_boundary_unavailable",
                    "decoder could not read the first frame after wrapping",
                )
            )

    errors.sort(key=lambda item: (item["frame_index"], item["reason"]))
    first_error = errors[0] if errors else None
    checks["visible_pixels_and_alpha"] = checks.get("decode") is True and all(
        item["reason"] != "pixel_or_alpha_mismatch" for item in errors
    )
    checks["cumulative_timing"] = checks.get("decode") is True and not any(
        item["reason"] in {"duration_mismatch", "timestamp_mismatch"}
        for item in errors
    )
    checks["loop_boundary"] = checks.get("decode") is True and not any(
        item["reason"]
        in {
            "loop_count_mismatch",
            "decoded_loop_count_mismatch",
            "loop_boundary_mismatch",
            "loop_boundary_unavailable",
        }
        for item in errors
    )
    checks["lossless"] = not any(
        item["reason"] == "frame_not_lossless" for item in errors
    )
    checks["canvas"] = not any(
        item["reason"] in {"canvas_mismatch", "decoded_canvas_mismatch"}
        for item in errors
    )
    checks["frame_count"] = not any(
        item["reason"]
        in {"frame_count_mismatch", "decoded_frame_count_mismatch"}
        for item in errors
    )
    return VerificationResult(
        ok=first_error is None,
        source=animation,
        timing=timing,
        width=expected_width,
        height=expected_height,
        webp_loop_count=webp_loop,
        webp_frame_count=webp_count_num,
        webp_durations_ms=durations,
        webp_timestamps_ms=timestamps,
        first_error=first_error,
        checks=checks,
    )


def convert_apng(
    input_path: Path | str,
    output_path: Path | str,
    strict_timing: bool = False,
) -> VerificationResult:
    animation = read_apng(input_path)
    timing = plan_delays(frame.control for frame in animation.frames)
    if strict_timing and timing.diagnostics:
        frames = ", ".join(
            str(item["frame_number"]) for item in timing.diagnostics
        )
        raise APNGError(
            "strict timing check failed for frame(s) " + frames
        )
    payload = encode_animation(
        animation.rendered,
        animation.width,
        animation.height,
        [item.duration_ms for item in timing.frames],
        animation.num_plays,
    )
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(payload)
    return verify_webp(animation, timing, payload)


def verify_files(apng_path: Path | str, webp_path: Path | str) -> VerificationResult:
    animation = read_apng(apng_path)
    timing = plan_delays(frame.control for frame in animation.frames)
    return verify_webp(animation, timing, read_webp(webp_path))
