from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .media import MediaInfo, iter_frames, pack_eyes, probe_video, split_eyes


class ExportError(RuntimeError):
    pass


@dataclass
class ExportResult:
    output: Path
    order: str
    offset_left: int
    offset_right: int
    packaging: str
    frame_count: int
    frame_rate: str
    warnings: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "output": str(self.output),
            "order": self.order,
            "offset_left": self.offset_left,
            "offset_right": self.offset_right,
            "packaging": self.packaging,
            "frame_count": self.frame_count,
            "frame_rate": self.frame_rate,
            "warnings": self.warnings,
        }


def _slot_for(side: str, order: str) -> str:
    if side == "left":
        return "first" if order == "ab" else "second"
    return "second" if order == "ab" else "first"


def _frame_ids(report: dict[str, Any], side: str, order: str) -> list[int | None]:
    slot = _slot_for(side, order)
    return [frame[slot].get("source_frame") for frame in report["frames"]]


def validate_corrections(
    report: dict[str, Any],
    *,
    order: str = "auto",
    offset_left: int = 0,
    offset_right: int = 0,
) -> dict[str, Any]:
    if order not in {"auto", "ab", "ba"}:
        raise ValueError("order must be auto, ab, or ba")
    if not isinstance(offset_left, int) or not isinstance(offset_right, int):
        raise ValueError("frame offsets must be integers")
    identity = report["eye_identity"]
    selected = identity.get("selected_order") if order == "auto" else order
    errors: list[dict[str, Any]] = []
    warnings: list[str] = []
    if selected not in {"ab", "ba"}:
        errors.append(
            {
                "type": "eye_order_required",
                "message": "Eye order is unconfirmed. Select AB or BA before export.",
            }
        )
        selected = "ab"
    detected = identity.get("detected_order")
    if detected in {"ab", "ba"} and selected != detected:
        errors.append(
            {
                "type": "eye_order_not_corrected",
                "message": f"Markers show {detected.upper()}, but export uses {selected.upper()}.",
            }
        )

    left_ids = _frame_ids(report, "left", selected)
    right_ids = _frame_ids(report, "right", selected)
    total = len(report["frames"])
    mismatches: list[int] = []
    for output_index in range(total):
        left_source = output_index - offset_left
        right_source = output_index - offset_right
        if not (0 <= left_source < total and 0 <= right_source < total):
            continue
        left_id = left_ids[left_source]
        right_id = right_ids[right_source]
        if left_id is not None and right_id is not None and left_id != right_id:
            mismatches.append(output_index)
    if mismatches:
        errors.append(
            {
                "type": "frame_pair_unresolved",
                "message": (
                    f"{len(mismatches)} output frames still pair different source frame numbers "
                    f"({mismatches[0]}..{mismatches[-1]})."
                ),
                "start_frame": mismatches[0],
                "end_frame": mismatches[-1],
                "frame_count": len(mismatches),
            }
        )
    for side in ("left", "right"):
        ids = left_ids if side == "left" else right_ids
        if not any(item is not None for item in ids):
            warnings.append(f"No readable frame clock in the logical {side} eye.")
    black_left = sum(1 for index in range(total) if not 0 <= index - offset_left < total)
    black_right = sum(1 for index in range(total) if not 0 <= index - offset_right < total)
    if black_left or black_right:
        warnings.append(
            f"Offsets leave {max(black_left, black_right)} black frame position(s) at the head or tail."
        )
    unresolved_safety = [
        item["type"]
        for item in report["anomalies"]
        if item["severity"] == "critical"
        and item["type"] in {"parallax_exceeded", "vertical_offset"}
    ]
    return {
        "ok": not errors,
        "order": selected,
        "detected_order": detected,
        "offset_left": offset_left,
        "offset_right": offset_right,
        "errors": errors,
        "warnings": warnings,
        "unresolved_safety_anomalies": sorted(set(unresolved_safety)),
        "frame_pair_mismatches": len(mismatches),
    }


def export_corrected(
    report: dict[str, Any],
    output: str | Path,
    *,
    order: str = "auto",
    offset_left: int = 0,
    offset_right: int = 0,
    packaging: str | None = None,
    force: bool = False,
) -> ExportResult:
    media = probe_video(report["input"]["path"])
    destination = Path(output).expanduser().resolve()
    if destination == media.path:
        raise ExportError("Output path must differ from the source video.")
    if destination.suffix.lower() not in {".mp4", ".mov", ".mkv"}:
        raise ExportError("Output must use .mp4, .mov, or .mkv")
    output_packing = packaging or report["media"]["packing"]
    if output_packing not in {"sbs", "ou"}:
        raise ExportError("packaging must be sbs or ou")
    validation = validate_corrections(
        report,
        order=order,
        offset_left=offset_left,
        offset_right=offset_right,
    )
    if validation["errors"] and not force:
        raise ExportError("; ".join(item["message"] for item in validation["errors"]))
    if validation["unresolved_safety_anomalies"] and not force:
        raise ExportError(
            "Uncorrected safety anomalies remain: "
            + ", ".join(validation["unresolved_safety_anomalies"])
            + ". Use force/allow anomalies after manual review."
        )

    selected = validation["order"]
    total = len(report["frames"])
    eye_w = int(report["media"]["eye_width"])
    eye_h = int(report["media"]["eye_height"])
    if output_packing == "sbs":
        out_w, out_h = eye_w * 2, eye_h
    else:
        out_w, out_h = eye_w, eye_h * 2
    if out_w % 2 or out_h % 2:
        raise ExportError(f"Output dimensions must be even for yuv420p: {out_w}x{out_h}")

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.stem}.partial{destination.suffix}")
    duration = total / float(media.frame_rate)
    command = [
        "ffmpeg",
        "-v",
        "error",
        "-y",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "-s",
        f"{out_w}x{out_h}",
        "-framerate",
        media.frame_rate_text,
        "-i",
        "-",
    ]
    if media.has_audio:
        command.extend(["-i", str(media.path)])
    command.extend(["-map", "0:v:0"])
    if media.has_audio:
        command.extend(["-map", "1:a:0?", "-c:a", "copy"])
    command.extend(
        [
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "16",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            "-t",
            f"{duration:.9f}",
            str(temporary),
        ]
    )
    proc = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    assert proc.stdin is not None
    black_first = bytes(eye_w * eye_h * 3)
    black_second = bytes(eye_w * eye_h * 3)
    source_iter = iter(iter_frames(media))
    cache: dict[int, bytes] = {}
    next_source = 0
    source_exhausted = False
    try:
        for output_index in range(total):
            left_source = output_index - offset_left
            right_source = output_index - offset_right
            valid = [item for item in (left_source, right_source) if 0 <= item < total]
            prune_below: int | None = None
            if valid:
                max_needed = max(valid)
                while not source_exhausted and next_source <= max_needed:
                    try:
                        source_index, packed = next(source_iter)
                    except StopIteration:
                        source_exhausted = True
                        break
                    cache[source_index] = packed
                    next_source = source_index + 1
                future = [
                    item
                    for item in (
                        output_index + 1 - offset_left,
                        output_index + 1 - offset_right,
                    )
                    if 0 <= item < total
                ]
                if future:
                    prune_below = min(future)

            logical: dict[str, bytes] = {
                "left": black_first,
                "right": black_second,
            }
            split_cache: dict[int, tuple[bytes, bytes]] = {}
            for side, source_index in (("left", left_source), ("right", right_source)):
                if source_index not in cache:
                    continue
                if source_index not in split_cache:
                    split_cache[source_index] = split_eyes(
                        cache[source_index],
                        media.width,
                        media.height,
                        report["media"]["packing"],
                    )
                first, second = split_cache[source_index]
                logical[side] = first if _slot_for(side, selected) == "first" else second
            packed_output = pack_eyes(
                logical["left"],
                logical["right"],
                out_w,
                out_h,
                output_packing,
            )
            proc.stdin.write(packed_output)
            if prune_below is None:
                cache.clear()
            else:
                for cached_index in [item for item in cache if item < prune_below]:
                    del cache[cached_index]
        proc.stdin.close()
        assert proc.stderr is not None
        error = proc.stderr.read().decode("utf-8", "replace").strip()
        proc.stderr.close()
        if proc.stdin:
            proc.stdin.close()
        return_code = proc.wait()
        if return_code:
            raise ExportError(error or f"ffmpeg exited with {return_code}")
        temporary.replace(destination)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        if temporary.exists():
            temporary.unlink()

    warnings = list(validation["warnings"])
    if force and validation["errors"]:
        warnings.extend(item["message"] for item in validation["errors"])
    return ExportResult(
        output=destination,
        order=selected,
        offset_left=offset_left,
        offset_right=offset_right,
        packaging=output_packing,
        frame_count=total,
        frame_rate=media.frame_rate_text,
        warnings=warnings,
    )
