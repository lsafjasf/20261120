"""FLV 时间线分析：交织、首帧偏移、帧率、关键帧间隔、AV 同步误差。"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from statistics import mean

from flv import FlvFile, Tag, TAG_AUDIO, TAG_VIDEO, AVC_END_OF_SEQ


@dataclass
class StreamTimeline:
    tag_count: int
    first_ts_ms: int          # 首个媒体 tag（不含序列头）的 FLV 时间戳
    last_ts_ms: int
    seq_headers: int


@dataclass
class TimelineReport:
    video: StreamTimeline
    audio: StreamTimeline
    first_video_offset_ms: int    # 相对首个媒体 tag 的首视频帧偏移
    first_audio_offset_ms: int
    measured_fps: float
    metadata_fps: float | None
    keyframe_intervals_ms: list[int]
    keyframe_interval_stats: dict
    interleave_ok: bool
    av_sync: dict


def _media_tags(tags: list[Tag], tag_type: int) -> list[Tag]:
    media = [t for t in tags
             if t.tag_type == tag_type and not t.is_sequence_header]
    if tag_type == TAG_VIDEO:
        # AVC end-of-sequence 是无帧数据的信令 tag，MP4 不产生样本
        media = [t for t in media
                 if not (len(t.body) > 1 and t.body[1] == AVC_END_OF_SEQ)]
    return media


def _stream_timeline(tags: list[Tag], tag_type: int) -> StreamTimeline:
    media = _media_tags(tags, tag_type)
    seq = sum(1 for t in tags if t.tag_type == tag_type and t.is_sequence_header)
    if not media:
        return StreamTimeline(0, 0, 0, seq)
    return StreamTimeline(
        tag_count=len(media),
        first_ts_ms=media[0].timestamp_ms,
        last_ts_ms=media[-1].timestamp_ms,
        seq_headers=seq,
    )


def analyze(flv: FlvFile) -> TimelineReport:
    tags = flv.tags
    video_media = _media_tags(tags, TAG_VIDEO)
    audio_media = _media_tags(tags, TAG_AUDIO)

    vline = _stream_timeline(tags, TAG_VIDEO)
    aline = _stream_timeline(tags, TAG_AUDIO)

    origin = min(
        video_media[0].timestamp_ms if video_media else 1 << 30,
        audio_media[0].timestamp_ms if audio_media else 1 << 30,
    )
    first_v_off = (video_media[0].timestamp_ms - origin) if video_media else 0
    first_a_off = (audio_media[0].timestamp_ms - origin) if audio_media else 0

    # 帧率：用首末帧时间戳与帧数估算（固定帧率素材上等价于标称帧率）
    fps = 0.0
    if len(video_media) >= 2:
        span_s = (video_media[-1].timestamp_ms - video_media[0].timestamp_ms) / 1000.0
        fps = (len(video_media) - 1) / span_s if span_s > 0 else 0.0
    # FLV 时间戳为整数毫秒，固定帧率下估算会有取整噪声；
    # 与元数据帧率偏差 <1% 时对齐到标称值。
    meta_fps = flv.metadata.get("framerate")
    if meta_fps and fps and abs(fps - meta_fps) / meta_fps < 0.01:
        fps = float(meta_fps)

    # 关键帧间隔（ms），基于 FLV DTS
    key_ts = [t.timestamp_ms for t in video_media if t.keyframe]
    kf_intervals = [b - a for a, b in zip(key_ts, key_ts[1:])]
    kf_stats = {
        "count": len(key_ts),
        "min_ms": min(kf_intervals) if kf_intervals else None,
        "max_ms": max(kf_intervals) if kf_intervals else None,
        "mean_ms": round(mean(kf_intervals), 3) if kf_intervals else None,
        "constant": len(set(kf_intervals)) <= 1,
    }

    # 交织检查：媒体 tag 的流类型应交替出现（允许同向连续若干个，
    # 这里校验整体交错度，即不存在长时间只有单一流的区间）。
    kinds = [t.tag_type for t in tags
             if t.tag_type in (TAG_AUDIO, TAG_VIDEO) and not t.is_sequence_header]
    switches = sum(1 for a, b in zip(kinds, kinds[1:]) if a != b)
    interleave_ok = switches > 0 and (switches / max(1, len(kinds) - 1)) > 0.2

    av_sync = _av_sync(audio_media, video_media)

    return TimelineReport(
        video=vline,
        audio=aline,
        first_video_offset_ms=first_v_off,
        first_audio_offset_ms=first_a_off,
        measured_fps=round(fps, 4),
        metadata_fps=flv.metadata.get("framerate"),
        keyframe_intervals_ms=kf_intervals,
        keyframe_interval_stats=kf_stats,
        interleave_ok=interleave_ok,
        av_sync=av_sync,
    )


def _av_sync(audio: list[Tag], video: list[Tag]) -> dict:
    """对每个视频帧找最近音频呈现时间，计算 |PTS 差|（ms）。

    B 帧场景必须用 PTS（DTS+CTS），音频无 B 帧 PTS=DTS。
    """
    if not audio or not video:
        return {"pairs": 0, "max_abs_ms": None, "mean_abs_ms": None,
                "p95_abs_ms": None, "first_frame_delta_ms": None}
    a_ts = sorted(t.timestamp_ms + t.cts_ms for t in audio)
    v_ts = [t.timestamp_ms + t.cts_ms for t in video]
    diffs: list[int] = []
    import bisect
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


def report_to_dict(r: TimelineReport) -> dict:
    return asdict(r)
