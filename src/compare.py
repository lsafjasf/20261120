"""FLV 与重封装 MP4 的时间线/首帧偏移/AV 同步对比。"""
from __future__ import annotations

from flv import FlvFile, Tag, TAG_AUDIO, TAG_VIDEO
from mp4_probe import TrackInfo
from timeline import _media_tags, _av_sync


def _flv_media_dts_ms(flv: FlvFile, tag_type: int) -> list[int]:
    tags = _media_tags(flv.tags, tag_type)
    if not tags:
        return []
    origin = tags[0].timestamp_ms
    return [t.timestamp_ms - origin for t in tags]


def _flv_presentation_ms(flv: FlvFile, tag_type: int) -> list[int]:
    """FLV 呈现时间戳 = DTS + composition time offset（CTS）。"""
    tags = _media_tags(flv.tags, tag_type)
    if not tags:
        return []
    return [t.timestamp_ms + t.cts_ms for t in tags]


def _mp4_presentation_ms(track: TrackInfo) -> list[int]:
    """MP4 绝对呈现时间。

    elst 非空编辑的 media_time 给出媒体时间基起点，映射关系：
    presentation = 空编辑延迟 + (媒体 PTS - elst media_time)
    """
    return [track.empty_edit_delay_ms + p - track.edit_media_start_ms
            for p in track.pts_ms]


def _max_abs_diff(a: list[int], b: list[int]) -> tuple[int, int]:
    """逐样本比较（长度取 min），返回 (最大绝对差, 比较样本数)。"""
    n = min(len(a), len(b))
    if n == 0:
        return 0, 0
    worst = max(abs(a[i] - b[i]) for i in range(n))
    return worst, n


def compare(flv: FlvFile, mp4_tracks: list[TrackInfo]) -> dict:
    v_track = next((t for t in mp4_tracks if t.handler == "vide"), None)
    a_track = next((t for t in mp4_tracks if t.handler == "soun"), None)

    flv_v = _flv_media_dts_ms(flv, TAG_VIDEO)
    flv_a = _flv_media_dts_ms(flv, TAG_AUDIO)

    result: dict = {
        "sample_counts": {
            "flv_video": len(flv_v),
            "mp4_video": v_track.sample_count if v_track else 0,
            "flv_audio": len(flv_a),
            "mp4_audio": a_track.sample_count if a_track else 0,
        },
        "first_frame_offset": _first_frame_offsets(flv, v_track, a_track),
        "dts_match": {},
        "avc_sequence_header": {
            "flv_avc_seq_header": _has_flv_avc_seq(flv),
            "mp4_avcC_present": bool(v_track and v_track.has_avcC),
        },
        "av_sync": {},
    }

    if v_track:
        v_diff, v_n = _max_abs_diff(flv_v, v_track.dts_ms)
        result["dts_match"]["video_max_abs_ms"] = v_diff
        result["dts_match"]["video_compared"] = v_n
    if a_track:
        a_diff, a_n = _max_abs_diff(flv_a, a_track.dts_ms)
        result["dts_match"]["audio_max_abs_ms"] = a_diff
        result["dts_match"]["audio_compared"] = a_n

    # MP4 侧 AV 同步（与 FLV 同算法），再与 FLV 结果比较
    flv_sync = _av_sync(_media_tags(flv.tags, TAG_AUDIO),
                        _media_tags(flv.tags, TAG_VIDEO))
    if v_track and a_track:
        mp4_sync = _track_av_sync(a_track, v_track)
        result["av_sync"] = {
            "flv": flv_sync,
            "mp4": mp4_sync,
            "max_abs_delta_ms": abs(flv_sync["max_abs_ms"] - mp4_sync["max_abs_ms"]),
            "mean_abs_delta_ms": round(
                abs(flv_sync["mean_abs_ms"] - mp4_sync["mean_abs_ms"]), 4),
        }
    return result


def _first_frame_offsets(flv: FlvFile,
                         v_track: TrackInfo | None,
                         a_track: TrackInfo | None) -> dict:
    # 呈现时间线比较：FLV 用 DTS+CTS，MP4 用 pts_ms + elst 空编辑延迟。
    mp4_v_off = mp4_a_off = None
    if v_track and a_track:
        v_pres = _mp4_presentation_ms(v_track)[0]
        a_pres = _mp4_presentation_ms(a_track)[0]
        delta = v_pres - a_pres
        mp4_v_off = max(delta, 0)
        mp4_a_off = max(-delta, 0)
    flv_v_pts = _flv_presentation_ms(flv, TAG_VIDEO)
    flv_a_pts = _flv_presentation_ms(flv, TAG_AUDIO)
    flv_delta = ((flv_v_pts[0] - flv_a_pts[0])
                 if flv_v_pts and flv_a_pts else 0)
    flv_v_off = max(flv_delta, 0)
    flv_a_off = max(-flv_delta, 0)
    return {
        "flv": {
            "video_ms": flv_v_off,
            "audio_ms": flv_a_off,
            "video_first_pts_ms": flv_v_pts[0] if flv_v_pts else None,
            "audio_first_pts_ms": flv_a_pts[0] if flv_a_pts else None,
        },
        "mp4": {
            "video_ms": mp4_v_off,
            "audio_ms": mp4_a_off,
            "video_first_pts_ms": v_pres if v_track else None,
            "audio_first_pts_ms": a_pres if a_track else None,
        },
    }


def _has_flv_avc_seq(flv: FlvFile) -> bool:
    return any(t.tag_type == TAG_VIDEO and t.is_sequence_header
               for t in flv.tags)


def _track_av_sync(a_track: TrackInfo, v_track: TrackInfo) -> dict:
    import bisect
    from statistics import mean
    # 通过 elst 映射得到绝对呈现时间
    a_ts = _mp4_presentation_ms(a_track)
    v_ts = _mp4_presentation_ms(v_track)
    # 整体平移到共同原点（不改变 A/V 相对关系）
    origin = min(a_ts[0], v_ts[0])
    a_ts = [t - origin for t in a_ts]
    v_ts = [t - origin for t in v_ts]
    diffs = []
    for vt in v_ts:
        idx = bisect.bisect_left(a_ts, vt)
        candidates = []
        if idx < len(a_ts):
            candidates.append(a_ts[idx])
        if idx > 0:
            candidates.append(a_ts[idx - 1])
        nearest = min(candidates, key=lambda x: abs(x - vt))
        diffs.append(abs(nearest - vt))
    s = sorted(diffs)
    p95_idx = min(len(s) - 1, int(round(0.95 * (len(s) - 1))))
    return {
        "pairs": len(diffs),
        "max_abs_ms": max(diffs),
        "mean_abs_ms": round(mean(diffs), 4),
        "p95_abs_ms": s[p95_idx],
        "first_frame_delta_ms": abs(a_ts[0] - v_ts[0]),
    }
