"""Frame-accurate smart cut.

Only the GOP(s) overlapping the edit points are decoded and re-encoded;
every interior GOP is stream-copied packet-for-packet. Audio and subtitle
streams are always stream-copied. The per-segment MP4 files are joined with
the concat demuxer (no re-mux recompression).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from ffutils import FFMPEG, FFPROBE, run

# bframes=0 keeps PTS/DTS linear inside each re-encoded GOP so the concat
# joins are gap-free without touching the stream-copied middle segment
X264_PARAMS = ("keyint=30:min-keyint=30:open-gop=0:scenecut=0"
               ":bframes=0")


@dataclass
class Segment:
    kind: str                 # "reencode" | "copy"
    path: Path
    src_start: int            # first source frame covered by this segment
    src_end: int              # exclusive
    keep_start: int           # first frame kept after local trim (source idx)
    keep_end: int             # exclusive
    encoded_frames: int = 0   # frames actually fed into libx264


def keyframe_indices(src: Path) -> list[int]:
    cmd = [
        FFPROBE, "-v", "error", "-select_streams", "v:0",
        "-show_frames", "-show_entries", "frame=key_frame",
        "-of", "json", str(src),
    ]
    frames = json.loads(run(cmd).stdout)["frames"]
    return [i for i, fr in enumerate(frames) if int(fr["key_frame"]) == 1]


def plan_segments(start: int, end: int, keyframes: list[int],
                  gop: int, total_frames: int) -> list[tuple[str, int, int]]:
    """Return (kind, seg_start, seg_end_exclusive) tuples.

    Re-encoded segments cover the full GOP that owns an edit point so the
    encoder always starts on an IDR; copied segments are keyframe aligned.
    """
    if not (0 <= start < end <= total_frames):
        raise ValueError("bad cut range")
    gop_lo = lambda f: (f // gop) * gop
    gop_hi = lambda f: min(((f // gop) + 1) * gop, total_frames)

    head_lo, head_hi = gop_lo(start), gop_hi(start)
    tail_lo, tail_hi = gop_lo(end - 1), gop_hi(end - 1)

    kf = set(keyframes)
    if head_lo == tail_lo:  # both edit points inside one GOP
        # no re-encode needed when the whole GOP is kept from its IDR
        if start == head_lo and end == head_hi:
            return [("copy", head_lo, head_hi)]
        return [("reencode", head_lo, head_hi)]

    # head segment: stream copy when the cut starts exactly on the IDR
    parts = [("copy", head_lo, head_hi)] if start in kf else \
        [("reencode", head_lo, head_hi)]
    if head_hi < tail_lo:       # keyframe-aligned interior: pure copy
        parts.append(("copy", head_hi, tail_lo))
    # tail segment: stream copy only when the cut ends exactly on the next IDR
    if end == tail_hi:
        parts.append(("copy", tail_lo, tail_hi))
    else:
        parts.append(("reencode", tail_lo, tail_hi))
    return _merge_adjacent_copy(parts)


def _merge_adjacent_copy(
        parts: list[tuple[str, int, int]]) -> list[tuple[str, int, int]]:
    merged: list[tuple[str, int, int]] = []
    for kind, lo, hi in parts:
        if merged and kind == "copy" and merged[-1][0] == "copy" \
                and merged[-1][2] == lo:
            prev_kind, prev_lo, _ = merged[-1]
            merged[-1] = (prev_kind, prev_lo, hi)
        else:
            merged.append((kind, lo, hi))
    return merged


def _fps(src: Path) -> float:
    cmd = [
        FFPROBE, "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=r_frame_rate", "-of",
        "csv=p=0", str(src),
    ]
    num, den = run(cmd).stdout.decode().strip().split("/")
    return int(num) / int(den)


def _cut_segment(src: Path, seg: Segment, fps: float, workdir: Path) -> None:
    if seg.kind == "copy":
        cmd = [
            FFMPEG, "-y",
            # input-side seek lands exactly on the IDR; -frames:v caps the
            # decoded packet count so B-frame reordering cannot add a frame
            # past the keyframe-aligned end boundary
            "-ss", f"{seg.src_start / fps:.6f}",
            "-i", str(src),
            "-an", "-sn",
            "-map", "0:v:0", "-c", "copy",
            "-frames:v", str(seg.src_end - seg.src_start),
            "-avoid_negative_ts", "make_zero",
            str(seg.path),
        ]
        run(cmd, check=True)
        return

    # Re-encoded GOP: seek to the IDR, decode the GOP, select the exact
    # frame range between the edit points, re-encode video only.
    before = seg.keep_start - seg.src_start
    span = seg.keep_end - seg.keep_start
    vf = f"select='gte(n,{before})',setpts=PTS-STARTPTS"
    cmd = [
        FFMPEG, "-y",
        "-ss", f"{seg.src_start / fps:.6f}",
        "-i", str(src),
        "-r", str(fps),
        "-filter_complex", f"[0:v]{vf}[v]",
        "-an", "-sn",
        "-map", "[v]",
        "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-x264-params", X264_PARAMS,
        "-video_track_timescale", str(int(fps * 512)),
        "-frames:v", str(span),
        str(seg.path),
    ]
    proc = run(cmd, check=True)
    stats = _x264_frame_stats(proc.stderr.decode())
    seg.encoded_frames = stats


def _x264_frame_stats(stderr: str) -> int:
    for line in stderr.splitlines():
        line = line.strip()
        if line.startswith("frame="):
            return int(line.split("frame=")[1].split()[0])
    raise RuntimeError("x264 frame counter not found in ffmpeg stderr")


def _copy_audio(src: Path, start_s: float, end_s: float, dest: Path) -> None:
    """One single stream-copy audio cut (never decoded, never re-encoded)."""
    cmd = [
        FFMPEG, "-y", "-ss", f"{start_s:.6f}", "-to", f"{end_s:.6f}",
        "-i", str(src), "-map", "0:a:0", "-c:a", "copy",
        "-avoid_negative_ts", "make_zero", str(dest),
    ]
    run(cmd, check=True)


def stream_copy_cut(src: Path, start: int, end: int, outdir: Path,
                    name: str, srt_path: Path | None = None) -> Path:
    """Reference path: stream copy only. Video starts at the previous
    keyframe, so the cut is not frame accurate; nothing is re-encoded."""
    outdir.mkdir(parents=True, exist_ok=True)
    workdir = outdir / f"{name}_parts"
    workdir.mkdir(exist_ok=True)
    fps = _fps(src)
    video_only = workdir / "video_copy.mp4"
    duration = (end - start) / fps
    cmd = [
        FFMPEG, "-y",
        "-ss", f"{(start // 30 * 30) / fps:.6f}",
        "-i", str(src),
        "-an", "-sn", "-map", "0:v:0", "-c", "copy",
        "-frames:v", str(end - start),
        "-avoid_negative_ts", "make_zero", str(video_only),
    ]
    run(cmd, check=True)
    audio_only = workdir / "audio_copy.aac"
    _copy_audio(src, start / fps, end / fps, audio_only)
    mux = [FFMPEG, "-y", "-i", str(video_only), "-i", str(audio_only)]
    sub_file = None
    if srt_path is not None:
        from srtcut import cut_srt
        sub_file = workdir / "subs_copy.srt"
        cut_srt(srt_path, start / fps, end / fps, sub_file)
        mux += ["-i", str(sub_file)]
    out = outdir / f"{name}.mp4"
    mux += ["-map", "0:v:0", "-map", "1:a:0", "-c", "copy"]
    if sub_file is not None:
        mux += ["-map", "2:0", "-c:s", "mov_text"]
    mux += ["-movflags", "+faststart", str(out)]
    run(mux, check=True)
    return out


def smart_cut(src: Path, start: int, end: int, outdir: Path, name: str,
              srt_path: Path | None = None) -> tuple[Path, list[Segment]]:
    outdir.mkdir(parents=True, exist_ok=True)
    workdir = outdir / f"{name}_parts"
    workdir.mkdir(exist_ok=True)
    fps = _fps(src)
    gop = 30
    from ffutils import count_frames
    total = count_frames(src)
    keys = keyframe_indices(src)
    if keys != list(range(0, total, gop)):
        raise AssertionError(f"unexpected keyframes: {keys[:5]}...")

    plan = plan_segments(start, end, keys, gop, total)
    segments: list[Segment] = []
    for idx, (kind, lo, hi) in enumerate(plan):
        keep_lo = max(lo, start)
        keep_hi = min(hi, end)
        seg = Segment(
            kind=kind,
            path=workdir / f"seg{idx}_{kind}.mp4",
            src_start=lo, src_end=hi,
            keep_start=keep_lo, keep_end=keep_hi,
        )
        _cut_segment(src, seg, fps, workdir)
        segments.append(seg)

    listfile = workdir / "concat.txt"
    listfile.write_text(
        "\n".join(f"file '{seg.path.resolve()}'" for seg in segments) + "\n"
    )
    video_only = workdir / "video.mp4"
    concat = [
        FFMPEG, "-y", "-f", "concat", "-safe", "0",
        "-i", str(listfile), "-c", "copy",
        # each segment starts at PTS 0; force a clean monotonic timeline so
        # the final mux never sees non-monotonic DTS across segment joins
        "-avoid_negative_ts", "make_zero", "-reset_timestamps", "1",
        str(video_only),
    ]
    run(concat, check=True)

    audio_only = workdir / "audio.aac"
    _copy_audio(src, start / fps, end / fps, audio_only)

    cmd = [FFMPEG, "-y", "-i", str(video_only), "-i", str(audio_only)]
    sub_file: Path | None = None
    if srt_path is not None:
        from srtcut import cut_srt
        sub_file = workdir / "subs.srt"
        cut_srt(srt_path, start / fps, end / fps, sub_file)
        cmd += ["-i", str(sub_file)]
    cmd += [
        "-map", "0:v:0", "-map", "1:a:0",
        "-c:v", "copy", "-c:a", "copy",
    ]
    if sub_file is not None:
        cmd += ["-map", "2:0", "-c:s", "mov_text"]
    out = outdir / f"{name}.mp4"
    cmd += ["-movflags", "+faststart", str(out)]
    run(cmd, check=True)
    return out, segments
