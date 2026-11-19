"""最小 ISO-BMFF(MP4) 解析器：独立于 ffmpeg 提取时间线信息。

支持 box 树遍历、mdhd 时间基、stts 解码时间、stss 同步样本、
elst 编辑偏移，以及 avcC（AVC 序列头）存在性检查。
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field

CONTAINERS = {
    "moov", "trak", "mdia", "minf", "stbl", "edts", "udta", "meta",
    "stsd",
}
# 视觉 sample entry：8 字节 box 头 + 78 字节固定字段后才是子 box
VISUAL_SAMPLE_ENTRIES = {"avc1", "avc3", "hev1", "hvc1", "mp4v", "encv"}
# 音频 sample entry：8 字节头 + 28 字节固定字段后才是子 box
AUDIO_SAMPLE_ENTRIES = {"mp4a", "enca", "ac-3", "ec-3"}


class Mp4Error(Exception):
    pass


@dataclass
class Box:
    type: str
    start: int
    size: int                 # 含 8 字节头
    header_len: int
    children: list["Box"] = field(default_factory=list)

    def find(self, box_type: str) -> "Box | None":
        for child in self.children:
            if child.type == box_type:
                return child
        return None

    def find_path(self, *path: str) -> "Box | None":
        node: Box | None = self
        for name in path:
            if node is None:
                return None
            node = node.find(name)
        return node

    def find_all(self, box_type: str) -> list["Box"]:
        return [c for c in self.children if c.type == box_type]

    def rfind(self, box_type: str) -> "Box | None":
        """深度优先递归查找第一个匹配的子 box。"""
        for child in self.children:
            if child.type == box_type:
                return child
            hit = child.rfind(box_type)
            if hit is not None:
                return hit
        return None


def _iter_boxes(data: bytes, start: int, end: int):
    pos = start
    while pos + 8 <= end:
        size, btype = struct.unpack_from(">I4s", data, pos)
        header_len = 8
        if size == 1:  # 64 位 largesize
            (size,) = struct.unpack_from(">Q", data, pos + 8)
            header_len = 16
        elif size == 0:
            size = end - pos
        if size < header_len or pos + size > end:
            raise Mp4Error(f"非法 box 尺寸 @0x{pos:x} type={btype!r}")
        name = btype.decode("latin-1")
        box = Box(type=name, start=pos, size=size, header_len=header_len)
        body_start = pos + header_len
        # 各 box body（去掉 box 头之后）需要再跳过的固定字节数。
        body_skip = {
            "meta": 4,            # version/flags
            "stsd": 8,            # version/flags(4) + entry_count(4)
        }
        body_skip.update({k: 78 for k in VISUAL_SAMPLE_ENTRIES})
        body_skip.update({k: 28 for k in AUDIO_SAMPLE_ENTRIES})
        if name in CONTAINERS or name in body_skip:
            child_start = body_start + body_skip.get(name, 0)
            box.children = list(_iter_boxes(data, child_start, pos + size))
        yield box
        pos += size


def parse_boxes(path: str) -> list[Box]:
    with open(path, "rb") as fh:
        data = fh.read()
    return list(_iter_boxes(data, 0, len(data)))


def _box_payload(data: bytes, box: Box) -> bytes:
    # meta 的 payload 前 4 字节是 version/flags
    skip = 4 if box.type == "meta" else 0
    return data[box.start + box.header_len + skip: box.start + box.size]


@dataclass
class TrackInfo:
    handler: str              # vide / soun
    timescale: int
    duration: int
    sample_count: int
    dts_ms: list[int]
    pts_ms: list[int]
    sync_samples: list[int]   # 1-based 样本序号
    edit_offsets_ms: list[int]
    has_avcC: bool
    first_dts_ms: int
    empty_edit_delay_ms: int    # elst 中空编辑(-1)的呈现时长
    presentation_start_ms: int  # 首样本在整体时间线上的呈现起点
    edit_media_start_ms: int    # 首个非空编辑的 media_time（媒体时间基）


def _parse_mdhd(payload: bytes) -> tuple[int, int]:
    version = payload[0]
    if version == 1:
        timescale = struct.unpack_from(">I", payload, 20)[0]
        duration = struct.unpack_from(">Q", payload, 24)[0]
    else:
        timescale = struct.unpack_from(">I", payload, 12)[0]
        duration = struct.unpack_from(">I", payload, 16)[0]
    return timescale, duration


def _parse_mvhd_timescale(payload: bytes) -> int:
    version = payload[0]
    if version == 1:
        # creation(8)+modification(8)+timescale(4)
        return struct.unpack_from(">I", payload, 20)[0]
    # creation(4)+modification(4)+timescale(4)
    return struct.unpack_from(">I", payload, 12)[0]


def _parse_stts(payload: bytes) -> list[int]:
    entry_count = struct.unpack_from(">I", payload, 4)[0]
    pos = 8
    dts = [0]
    current = 0
    total = 0
    for _ in range(entry_count):
        count, delta = struct.unpack_from(">II", payload, pos)
        pos += 8
        for _ in range(count):
            current += delta
            dts.append(current)
            total += 1
    # dts 数组长度 = 样本数 + 1（最后一个是末尾时间），去掉末尾
    return dts[:-1] if total else []


def _parse_stss(payload: bytes) -> list[int]:
    entry_count = struct.unpack_from(">I", payload, 4)[0]
    return list(struct.unpack_from(">%dI" % entry_count, payload, 8))


def _parse_ctts(payload: bytes, timescale: int) -> list[int]:
    """composition offset box，返回每个样本的 PTS 偏移(ms)，支持有符号版本。"""
    version = payload[0]
    entry_count = struct.unpack_from(">I", payload, 4)[0]
    signed = version == 1
    pos = 8
    offsets_ms: list[int] = []
    for _ in range(entry_count):
        count, raw = struct.unpack_from(">II", payload, pos)
        pos += 8
        if signed and raw & 0x80000000:
            raw -= 1 << 32
        per_sample = round(raw * 1000 / timescale) if timescale else 0
        offsets_ms.extend([per_sample] * count)
    return offsets_ms


def _parse_elst_full(payload: bytes, timescale: int,
                     movie_timescale: int
                     ) -> tuple[list[int], int, int]:
    """返回 (media_time 列表(ms，空编辑保留 -1), 空编辑延迟 ms,
    首个非空编辑的 media_time ms)。

    media_time 使用 track timescale；空编辑的 segment_duration 使用
    movie(mvhd) timescale，二者时间基不同，必须分别换算。
    """
    version = payload[0]
    entry_count = struct.unpack_from(">I", payload, 4)[0]
    media_times: list[int] = []
    empty_delay = 0
    media_start = 0
    pos = 8
    for _ in range(entry_count):
        if version == 1:
            seg_dur, media_time, _, _ = struct.unpack_from(">QqII", payload, pos)
            pos += 24
        else:
            seg_dur, media_time, rate = struct.unpack_from(
                ">IiI", payload, pos)
            pos += 12
        if media_time < 0:
            # 空编辑(media_time=-1)：segment_duration 按 movie 时间基换算
            media_times.append(-1)
            empty_delay += (round(seg_dur * 1000 / movie_timescale)
                            if movie_timescale else 0)
        else:
            mt_ms = round(media_time * 1000 / timescale) if timescale else 0
            media_times.append(mt_ms)
            if media_start == 0:
                media_start = mt_ms
    return media_times, empty_delay, media_start


def probe_mp4(path: str) -> list[TrackInfo]:
    with open(path, "rb") as fh:
        data = fh.read()
    root = list(_iter_boxes(data, 0, len(data)))
    moov = next((b for b in root if b.type == "moov"), None)
    if moov is None:
        raise Mp4Error("MP4 缺少 moov box")
    mvhd = moov.find("mvhd")
    if mvhd is None:
        raise Mp4Error("moov 缺少 mvhd")
    movie_timescale = _parse_mvhd_timescale(_box_payload(data, mvhd))
    tracks: list[TrackInfo] = []
    for trak in moov.find_all("trak"):
        mdia = trak.find("mdia")
        if mdia is None:
            continue
        hdlr = mdia.find_path("hdlr")
        handler = "unknown"
        if hdlr is not None:
            payload = _box_payload(data, hdlr)
            handler = payload[8:12].decode("latin-1")
        mdhd = mdia.find("mdhd")
        timescale, duration = _parse_mdhd(_box_payload(data, mdhd))

        minf = mdia.find("minf")
        stbl = minf.find("stbl") if minf else None
        if stbl is None:
            raise Mp4Error("trak 缺少 stbl")
        stts = stbl.find("stts")
        dts_ticks = _parse_stts(_box_payload(data, stts))
        dts_ms = [round(t * 1000 / timescale) for t in dts_ticks]
        ctts = stbl.find("ctts")
        if ctts is not None:
            ctts_ms = _parse_ctts(_box_payload(data, ctts), timescale)
            if len(ctts_ms) != len(dts_ms):
                raise Mp4Error(
                    f"ctts 样本数 {len(ctts_ms)} 与 stts {len(dts_ms)} 不一致")
            pts_ms = [d + c for d, c in zip(dts_ms, ctts_ms)]
        else:
            pts_ms = list(dts_ms)

        stss = stbl.find("stss")
        syncs = _parse_stss(_box_payload(data, stss)) if stss else (
            list(range(1, len(dts_ms) + 1)))

        has_avcc = stbl.rfind("avcC") is not None

        edts = trak.find("edts")
        edits: list[int] = []
        empty_delay = 0
        if edts is not None and edts.find("elst") is not None:
            elst_box = edts.find("elst")
            edits, empty_delay, media_start = _parse_elst_full(
                _box_payload(data, elst_box), timescale, movie_timescale)
        else:
            media_start = 0

        tracks.append(TrackInfo(
            handler=handler,
            timescale=timescale,
            duration=duration,
            sample_count=len(dts_ms),
            dts_ms=dts_ms,
            pts_ms=pts_ms,
            sync_samples=syncs,
            edit_offsets_ms=edits,
            has_avcC=has_avcc,
            first_dts_ms=dts_ms[0] if dts_ms else 0,
            empty_edit_delay_ms=empty_delay,
            presentation_start_ms=empty_delay + (dts_ms[0] if dts_ms else 0),
            edit_media_start_ms=media_start,
        ))
    return tracks
