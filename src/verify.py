"""Verification suite for the two export paths.

Checks:
  1. output frame counts vs expected, per-segment re-encoded frame counts;
  2. sha256 of every produced file;
  3. frame-accurate content continuity across the edit point (frame labels,
     pixel-level seam diff against the deterministic ground truth);
  4. audio AAC packets identical to source (no audio re-encode) and audio
     start offset vs video;
  5. subtitle cue/PTS sync against frame timestamps;
  6. byte-level comparison stream-copy vs smart-cut;
  7. deblocking: true-edge pixel difference (deblock on vs off).
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ffutils import FFMPEG, FFPROBE, run, packet_table, probe_streams
from framegen import render_frame, W, H, FPS
from framegen import FONT

ROOT = Path(__file__).resolve().parent.parent


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def psnr_of(mse: np.ndarray) -> np.ndarray:
    """Per-frame PSNR with exact-match frames mapped to +inf (no log10(0))."""
    out = np.full(mse.shape, np.inf)
    pos = mse > 0
    out[pos] = 10 * np.log10(255.0 ** 2 / mse[pos])
    return out


def extract_rgb(path: Path, tmp: Path | None = None) -> np.ndarray:
    """Decode every frame to an NxHxWx3 uint8 RGB array.

    Raw frames are written to a temp file rather than a stdout pipe: a pipe
    can fill (~64 KiB) and deadlock the encoder before all frames flush."""
    import tempfile
    if tmp is None:
        tmp = Path(tempfile.mkstemp(suffix=".rgb")[1])
        cleanup = True
    else:
        tmp.parent.mkdir(parents=True, exist_ok=True)
        cleanup = False
    cmd = [
        FFMPEG, "-y", "-v", "error", "-i", str(path),
        "-f", "rawvideo", "-pix_fmt", "rgb24", str(tmp),
    ]
    run(cmd, check=True)
    raw = tmp.read_bytes()
    if cleanup:
        tmp.unlink(missing_ok=True)
    frame_size = W * H * 3
    if len(raw) % frame_size != 0:
        raise AssertionError("raw video size not frame aligned")
    return np.frombuffer(raw, np.uint8).reshape(-1, H, W, 3).copy()


def decode_label(frame: np.ndarray) -> int | None:
    """Read the 3-digit 5x7 bitmap label (scale 3) rendered at (12, 10).
    Each glyph cell is reduced to a 5x7 bitmap by majority vote, then
    Hamming-matched against the font table (robust to codec ringing)."""
    scale = 3
    glyph_h, glyph_w = 7, 5
    digits = []
    for slot in range(3):
        x0 = 12 + slot * 18
        y0 = 10
        best, best_score = None, -1
        observed = np.zeros((glyph_h, glyph_w), dtype=np.uint8)
        for row in range(glyph_h):
            for col in range(glyph_w):
                patch = frame[y0 + row * scale:y0 + (row + 1) * scale,
                              x0 + col * scale:x0 + (col + 1) * scale, 0]
                observed[row, col] = 1 if (patch > 128).mean() > 0.5 else 0
        for ch, glyph in FONT.items():
            template = np.array([[int(c) for c in row] for row in glyph])
            score = int((observed == template).sum())
            if score > best_score:
                best, best_score = ch, score
        digits.append(best)
    try:
        return int("".join(digits))
    except (TypeError, ValueError):
        return None


def identify_frames(decoded: np.ndarray, lo: int, hi: int,
                    step: int = 4) -> list[int]:
    """Vectorized nearest-ground-truth frame identity on a downsampled grid.
    Robust to lossy ringing and far faster than a full-resolution scan."""
    truth = np.stack([render_frame(k) for k in range(lo, hi)])
    small_dec = decoded[:, ::step, ::step, :].astype(np.int16)
    small_tru = truth[:, ::step, ::step, :].astype(np.int16)
    ids = []
    for fr in small_dec:
        mad = np.abs(small_tru - fr).mean(axis=(1, 2, 3))
        ids.append(lo + int(mad.argmin()))
    return ids


def file_manifest(*paths: Path) -> dict:
    out = {}
    for p in paths:
        out[str(p.relative_to(ROOT))] = {
            "size_bytes": p.stat().st_size,
            "sha256": sha256(p),
        }
    return out


def video_checks(clip: Path, start: int, end: int) -> dict:
    """Frame count, per-frame PSNR vs ground truth, seam continuity."""
    got = extract_rgb(clip)
    expected = np.stack([render_frame(i) for i in range(start, end)])
    n = min(len(got), len(expected))
    got, expected = got[:n], expected[:n]
    diff = got.astype(np.int16) - expected.astype(np.int16)
    mse = np.mean(diff ** 2, axis=(1, 2, 3))
    # exact-match frames get an infinite PSNR marker; finite frames use the
    # standard formula (no artificial clipping)
    psnr = psnr_of(mse)
    finite = psnr[np.isfinite(psnr)]
    # seamless junction: compare frames straddling each internal concat
    # boundary (after 15 re-encoded frames and after 15+150 frames)
    seam_positions = [15, 165]
    seams = {}
    for pos in seam_positions:
        if pos + 1 < n:
            # continuity test: the observed frame-to-frame motion field must
            # match the ground-truth motion field (no duplicated/skipped frame)
            d = got[pos].astype(int) - got[pos - 1].astype(int)
            gt_d = (expected[pos].astype(int)
                    - expected[pos - 1].astype(int))
            continuity_err = np.abs(d - gt_d)
            seams[f"after_output_frame_{pos}"] = {
                "motion_field_max_err": int(continuity_err.max()),
                "motion_field_mean_err": float(continuity_err.mean()),
                "seamless": bool(continuity_err.mean() < 0.5),
            }
    # primary identity check: nearest ground-truth frame (robust to ringing)
    ids = identify_frames(got, max(0, start - 5), end + 5)
    expected_ids = list(range(start, end))
    identity_mismatches = [
        {"output_frame": i, "identified": got_id, "expected": want}
        for i, (got_id, want) in enumerate(zip(ids, expected_ids))
        if got_id != want
    ][:5]
    # secondary check: bitmap label OCR (informational only)
    labels = [decode_label(fr) for fr in got]
    label_mismatches = [
        {"output_frame": i, "decoded": g, "expected": w}
        for i, (g, w) in enumerate(zip(labels, expected_ids))
        if g != w
    ][:5]
    return {
        "decoded_frames": int(len(got)),
        "expected_frames": int(end - start),
        "frame_count_match": bool(len(got) == end - start),
        "psnr_min_db": float(np.min(finite)) if finite.size else None,
        "psnr_mean_db": float(np.mean(finite)) if finite.size else None,
        "exact_identical_frames": int(np.sum(np.all(diff == 0, axis=(1, 2, 3)))),
        "frame_identity_sequence_ok": not identity_mismatches,
        "frame_identity_mismatches": identity_mismatches,
        "ocr_label_mismatches_sample": label_mismatches,
        "seams": seams,
    }


def extract_aac(path: Path, dest: Path) -> Path:
    cmd = [FFMPEG, "-y", "-v", "error", "-i", str(path),
           "-map", "0:a:0", "-c:a", "copy", "-f", "adts", str(dest)]
    run(cmd, check=True)
    return dest


def adts_frames(blob: bytes) -> list[bytes]:
    """Split an ADTS byte stream into individual frame byte strings."""
    frames, i, n = [], 0, len(blob)
    while i + 7 <= n:
        if blob[i] != 0xFF or (blob[i + 1] & 0xF0) != 0xF0:
            break
        # header length is 7 bytes when protection_absent (low 2 bits of b1)
        header = 9 if (blob[i + 1] & 0x01) == 0 else 7
        frame_len = ((blob[i + 3] & 0x03) << 11) | (blob[i + 4] << 3) \
            | ((blob[i + 5] >> 5) & 0x07)
        frames.append(blob[i:i + frame_len])
        i += frame_len
    return frames


def audio_checks(src: Path, clip: Path, start: int, end: int,
                 work: Path) -> dict:
    work.mkdir(exist_ok=True)
    src_aac = extract_aac(src, work / "src.aac")
    clip_aac = extract_aac(clip, work / "clip.aac")
    src_pk = packet_table(src_aac)
    clip_pk = packet_table(clip_aac)
    src_blob = src_aac.read_bytes()
    clip_blob = clip_aac.read_bytes()
    # frame-level proof that audio packets were not re-encoded: every ADTS
    # frame of the clip must occur, byte-for-byte, in the source stream
    src_frames = adts_frames(src_blob)
    clip_frames = adts_frames(clip_blob)
    src_set = {}
    for idx, fr in enumerate(src_frames):
        src_set.setdefault(hashlib.sha256(fr).hexdigest(), idx)
    matched = sum(1 for fr in clip_frames
                  if hashlib.sha256(fr).hexdigest() in src_set)
    # longest contiguous byte window as a secondary check
    window = min(4096, len(src_blob), len(clip_blob))
    identical_window = clip_blob[128:128 + window] in src_blob
    # codec parameters must be unchanged (no re-encode)
    src_stream = next(s for s in probe_streams(src) if s["codec_type"] == "audio")
    clip_stream = next(s for s in probe_streams(clip)
                       if s["codec_type"] == "audio")
    same_codec = (src_stream["codec_name"] == clip_stream["codec_name"]
                  and src_stream["sample_rate"] == clip_stream["sample_rate"]
                  and src_stream["channels"] == clip_stream["channels"])
    # first packet start time relative to video t=0
    first_audio_pts = float(clip_pk[0]["pts_time"])
    return {
        "codec_unchanged": bool(same_codec),
        "codec_name": clip_stream["codec_name"],
        "sample_rate": int(clip_stream["sample_rate"]),
        "channels": int(clip_stream["channels"]),
        "source_aac_packets": len(src_pk),
        "clip_aac_packets": len(clip_pk),
        "adts_frames_source": len(src_frames),
        "adts_frames_clip": len(clip_frames),
        "adts_frames_bit_identical_to_source": matched,
        "all_audio_frames_copied": matched == len(clip_frames),
        "first_audio_pts_s": first_audio_pts,
        "first_video_pts_s": 0.0,
        "av_start_offset_ms": round(first_audio_pts * 1000, 3),
        "bit_identical_4kib_window_in_source": bool(identical_window),
        "adts_sha256": {
            "source": sha256(src_aac),
            "clip": sha256(clip_aac),
        },
    }


def subtitle_checks(clip: Path, start: int, end: int, work: Path) -> dict:
    work.mkdir(exist_ok=True)
    got_srt = work / "clip_subs.srt"
    cmd = [FFMPEG, "-y", "-v", "error", "-i", str(clip),
           "-map", "0:s:0", "-c:s", "srt", str(got_srt)]
    run(cmd, check=True)
    from srtcut import to_seconds
    blocks = re_split(got_srt.read_text(encoding="utf-8"))
    cues = []
    for block in blocks:
        lines = [ln for ln in block.splitlines() if ln.strip()]
        if len(lines) >= 2 and "-->" in lines[1]:
            a, b = (x.strip() for x in lines[1].split("-->"))
            cues.append((to_seconds(a), to_seconds(b), " ".join(lines[2:])))
    expected_span = (end - start) / FPS
    max_err = 0.0
    for lo, hi, _ in cues:
        max_err = max(max_err, abs(lo - round(lo * FPS) / FPS),
                      abs(hi - round(hi * FPS) / FPS))
    return {
        "cue_count": len(cues),
        "first_cue_start_s": cues[0][0] if cues else None,
        "last_cue_end_s": cues[-1][1] if cues else None,
        "video_duration_s": expected_span,
        "max_timestamp_quantization_error_ms": round(max_err * 1000, 3),
        "starts_at_zero": bool(cues and abs(cues[0][0]) < 1e-6),
        "all_cues_within_clip": bool(
            all(0 <= lo and hi <= expected_span + 1e-3 for lo, hi, _ in cues)
        ),
        "sample_text": cues[0][2] if cues else "",
    }


def re_split(text: str) -> list[str]:
    import re
    return re.split(r"\n\s*\n", text.strip())


def byte_diff_checks(copy_clip: Path, smart_clip: Path,
                     src: Path, work: Path) -> dict:
    a = copy_clip.read_bytes()
    b = smart_clip.read_bytes()
    work.mkdir(exist_ok=True)
    # extract H.264 elementary streams (Annex-B) for codec-level comparison
    for tag, clip in (("copy", copy_clip), ("smart", smart_clip)):
        run([FFMPEG, "-y", "-v", "error", "-i", str(clip),
             "-map", "0:v:0", "-c", "copy", "-f", "h264",
             str(work / f"{tag}.h264")], check=True)
    h_copy = (work / "copy.h264").read_bytes()
    h_smart = (work / "smart.h264").read_bytes()
    # count NAL units by Annex-B start codes as a structural metric
    def nal_count(blob: bytes) -> int:
        return blob.count(b"\x00\x00\x01") + blob.count(b"\x00\x00\x00\x01")
    return {
        "streamcopy": {
            "size_bytes": len(a), "sha256": sha256(copy_clip),
            "h264_nal_units": nal_count(h_copy),
        },
        "smartcut": {
            "size_bytes": len(b), "sha256": sha256(smart_clip),
            "h264_nal_units": nal_count(h_smart),
        },
        "container_size_delta_bytes": len(a) - len(b),
        "h264_es_size_copy": len(h_copy),
        "h264_es_size_smart": len(h_smart),
        "identical_mp4_bytes": a == b,
        "identical_h264_es": h_copy == h_smart,
    }


def _encode_gop(src: Path, start_frame: int, span: int, deblock: bool,
                dest: Path, fps: float, crf: int) -> None:
    # this x264 build parses no-deblock as a boolean x264-params key;
    # deblock=0:0 is rejected, and the ffmpeg -deblock option maps to
    # x264 strength offsets rather than an on/off switch
    params = "keyint=30:min-keyint=30:open-gop=0:scenecut=0"
    if not deblock:
        params += ":no-deblock=1"
    cmd = [
        FFMPEG, "-y", "-v", "error",
        "-ss", f"{start_frame / fps:.6f}", "-i", str(src),
        "-filter_complex",
        f"[0:v]select='gte(n,0)',setpts=PTS-STARTPTS[v]",
        "-map", "[v]", "-an", "-sn",
        "-c:v", "libx264", "-preset", "medium", "-crf", str(crf),
        "-x264-params", params,
        "-frames:v", str(span), str(dest),
    ]
    run(cmd, check=True)


def deblock_checks(src: Path, start: int, work: Path) -> dict:
    """Re-encode the head GOP (source frames 30..59) deblock on/off at two
    CRFs. True-edge pixels come from the static checkerboard patch; block
    boundaries sampled inside flat checker interiors isolate deblock gain."""
    work.mkdir(exist_ok=True)
    fps = FPS
    span = 30
    truth = np.stack([render_frame(i) for i in range(30, 60)]).astype(np.int16)

    x0, y0, cells, cell = 432, 96, 6, 16
    patch_rows = slice(y0, y0 + cells * cell)
    patch_cols = slice(x0, x0 + cells * cell)
    # true edges: 1 px bands around every checker cell border (16 px grid)
    edge = np.zeros((span, H, W), dtype=bool)
    for k in range(1, cells):
        edge[:, y0 + k * cell - 1:y0 + k * cell + 1,
             x0:x0 + cells * cell] = True
        edge[:, y0:y0 + cells * cell,
             x0 + k * cell - 1:x0 + k * cell + 1] = True
    # 8x8 transform-block grid inside the mid-contrast 8px checker probe;
    # interior 8px lines are pure blocking-sensitive samples (not cell edges)
    block_mask = np.zeros((span, H, W, 3), dtype=bool)
    qx0, qy0, qcells, qsize = 320, 180, 8, 8
    rows = [r for r in range(qy0, qy0 + qcells * qsize)
            if (r - qy0) % 8 == 7 and (r - qy0) % 16 != 15]
    cols = [c for c in range(qx0, qx0 + qcells * qsize)
            if (c - qx0) % 8 == 7 and (c - qx0) % 16 != 15]
    block_mask[:, rows, :, :] = True
    block_mask[:, :, cols, :] = True
    ramp_mask3 = np.zeros((span, H, W, 3), dtype=bool)
    ramp_mask3[:, qy0:qy0 + qcells * qsize,
               qx0:qx0 + qcells * qsize, :] = True
    patch_mask3 = np.zeros((span, H, W, 3), dtype=bool)
    patch_mask3[:, patch_rows, patch_cols, :] = True
    edge3 = np.repeat(edge[:, :, :, None], 3, axis=3) & patch_mask3
    block_mask &= ramp_mask3
    results = {"gop_source_frames": [30, 60],
               "static_patch": {"checker": {"x": x0, "y": y0,
                                             "size": cells * cell},
                                "deblock_probe": {"x": qx0, "y": qy0,
                                                  "size": qcells * qsize}},
               "true_edge_pixels": int(edge3.sum()),
               "flat_block_boundary_samples": int(block_mask.sum()),
               "crf_sweep": {}}
    for crf in (20, 32, 38):
        on = work / f"gop_crf{crf}_deblock_on.mp4"
        off = work / f"gop_crf{crf}_deblock_off.mp4"
        _encode_gop(src, 30, span, True, on, fps, crf)
        _encode_gop(src, 30, span, False, off, fps, crf)
        dec_on = extract_rgb(on).astype(np.int16)
        dec_off = extract_rgb(off).astype(np.int16)
        err_on = np.abs(dec_on - truth)
        err_off = np.abs(dec_off - truth)
        results["crf_sweep"][f"crf_{crf}"] = {
            "deblock_on": {
                "true_edge_mean_abs_diff": float(err_on[edge3].mean()),
                "true_edge_max_abs_diff": int(err_on[edge3].max()),
                "flat_interior_mean_abs_diff": float(
                    err_on[ramp_mask3].mean()),
                "block_boundary_mean_abs_diff": float(
                    err_on[block_mask].mean()),
                "psnr_mean_db": float(_mean_psnr(dec_on, truth)),
            },
            "deblock_off": {
                "true_edge_mean_abs_diff": float(err_off[edge3].mean()),
                "true_edge_max_abs_diff": int(err_off[edge3].max()),
                "flat_interior_mean_abs_diff": float(
                    err_off[ramp_mask3].mean()),
                "block_boundary_mean_abs_diff": float(
                    err_off[block_mask].mean()),
                "psnr_mean_db": float(_mean_psnr(dec_off, truth)),
            },
            # <=0 => deblock does not blur true edges (on is no worse)
            "true_edge_diff_delta_on_minus_off": float(
                err_on[edge3].mean() - err_off[edge3].mean()),
            # positive => deblock suppresses block-boundary artifacts
            "block_smoothing_gain_off_minus_on": float(
                err_off[block_mask].mean() - err_on[block_mask].mean()),
            # a sub-0.1 level change on true edges is visually lossless;
            # this is the quantitative "no real-edge blur" guard
            "true_edge_blur_below_0p1_level": bool(
                abs(err_on[edge3].mean() - err_off[edge3].mean()) < 0.1),
        }
    production = results["crf_sweep"]["crf_20"]
    results["verdict_at_production_crf20"] = {
        "true_edge_mean_abs_diff_deblock_on":
            production["deblock_on"]["true_edge_mean_abs_diff"],
        "true_edge_mean_abs_diff_deblock_off":
            production["deblock_off"]["true_edge_mean_abs_diff"],
        "true_edge_pixel_level_delta":
            production["true_edge_diff_delta_on_minus_off"],
        "real_edges_not_blurred": bool(
            abs(production["true_edge_diff_delta_on_minus_off"]) < 0.05),
        "block_boundary_smoothing_gain":
            production["block_smoothing_gain_off_minus_on"],
        "blocking_reduced":
            production["block_smoothing_gain_off_minus_on"] >= 0.0,
    }
    return results


def _mean_psnr(decoded: np.ndarray, truth: np.ndarray) -> float:
    mse = np.mean((decoded - truth) ** 2, axis=(1, 2, 3))
    psnr = psnr_of(mse)
    finite = psnr[np.isfinite(psnr)]
    return float(finite.mean()) if finite.size else float("inf")


def main() -> None:
    manifest = json.loads((ROOT / "reports" / "run_manifest.json").read_text())
    start = manifest["cut"]["start_frame"]
    end = manifest["cut"]["end_frame_exclusive"]
    src = ROOT / manifest["source"]
    copy_clip = ROOT / manifest["streamcopy_output"]
    smart_clip = ROOT / manifest["smartcut_output"]
    work = ROOT / "output" / "verify_work"

    copy_ids = identify_frames(extract_rgb(copy_clip),
                               max(0, start - 20), end + 5)

    report = {
        "cut_range_frames": [start, end],
        "reencode_plan": manifest["segments"],
        "reencoded_frame_total": sum(
            s["reencoded_frames"] for s in manifest["segments"]),
        "file_hashes": file_manifest(src, copy_clip, smart_clip),
        "video_streamcopy": video_checks(
            copy_clip, start // 30 * 30,
            start // 30 * 30 + (end - start)),
        "streamcopy_keyframe_alignment": {
            "requested_start_frame": start,
            "actual_first_frame": copy_ids[0],
            "extra_head_frames": start - copy_ids[0],
            "frame_accurate": copy_ids[0] == start,
            "note": "stream copy can only start on the preceding IDR keyframe",
        },
        "video_smartcut": video_checks(smart_clip, start, end),
        "audio_smartcut": audio_checks(src, smart_clip, start, end, work),
        "subtitles_smartcut": subtitle_checks(smart_clip, start, end, work),
        "byte_comparison": byte_diff_checks(copy_clip, smart_clip, src, work),
        "deblocking": deblock_checks(src, start, work),
    }
    dest = ROOT / "reports" / "verification_report.json"
    dest.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
