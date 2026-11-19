"""FLV (Flash Video) 解析器。

仅依赖标准库，支持：
- FLV header / PreviousTagSize 校验；
- script(18)、audio(8)、video(9) 三类 tag 的头解析与时间戳重组；
- AAC 音频（含 AudioSpecificConfig 序列头）与 AVC 视频
  （含 AVCDecoderConfigurationRecord 序列头）的帧类型识别；
- onMetadata script tag 的 AMF0 解码（够用的子集）。
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Any

TAG_AUDIO = 8
TAG_VIDEO = 9
TAG_SCRIPT = 18

# SoundFormat
SF_AAC = 10
# AACPacketType
AAC_SEQ_HEADER = 0
AAC_RAW = 1
# AVCPacketType
AVC_SEQ_HEADER = 0
AVC_NALU = 1
AVC_END_OF_SEQ = 2


class FlvError(Exception):
    pass


@dataclass
class FlvHeader:
    version: int
    has_audio: bool
    has_video: bool
    data_offset: int


@dataclass
class Tag:
    tag_type: int
    data_size: int
    timestamp_ms: int          # 已含扩展字节，单位 ms
    stream_id: int
    body: bytes
    offset: int                 # tag 头在文件中的字节偏移
    prev_tag_size: int

    # 解析后的便捷属性
    kind: str = ""
    is_sequence_header: bool = False
    cts_ms: int = 0             # AVC composition time offset, ms
    keyframe: bool = False
    codec: str = ""


@dataclass
class FlvFile:
    header: FlvHeader
    tags: list[Tag] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


def _u24(buf: bytes, pos: int) -> int:
    return (buf[pos] << 16) | (buf[pos + 1] << 8) | buf[pos + 2]


def parse_flv(path: str) -> FlvFile:
    with open(path, "rb") as fh:
        data = fh.read()
    if data[:3] != b"FLV":
        raise FlvError("不是 FLV 文件（缺少 'FLV' 签名）")
    version = data[3]
    flags = data[4]
    data_offset = struct.unpack_from(">I", data, 5)[0]
    if data_offset < 9:
        raise FlvError(f"非法 DataOffset: {data_offset}")
    header = FlvHeader(
        version=version,
        has_audio=bool(flags & 0x04),
        has_video=bool(flags & 0x01),
        data_offset=data_offset,
    )

    pos = data_offset
    prev_size0 = struct.unpack_from(">I", data, pos)[0]
    if prev_size0 != 0:
        raise FlvError(f"首个 PreviousTagSize 应为 0，实际 {prev_size0}")
    pos += 4

    flv = FlvFile(header=header)
    index = 0
    while pos < len(data):
        if pos + 11 > len(data):
            raise FlvError(f"tag 头被截断 @0x{pos:x}")
        tag_offset = pos
        tag_type = data[pos]
        data_size = _u24(data, pos + 1)
        ts24 = _u24(data, pos + 4)
        ts_ext = data[pos + 7]
        stream_id = _u24(data, pos + 8)
        # 时间戳：低 24 位 + 高 8 位扩展，组成有符号 32 位整数
        timestamp = (ts_ext << 24) | ts24
        if timestamp & 0x80000000:
            timestamp -= 1 << 32
        body_start = pos + 11
        body_end = body_start + data_size
        if body_end + 4 > len(data):
            raise FlvError(f"tag 体被截断 @0x{pos:x}")
        body = data[body_start:body_end]
        prev_tag_size = struct.unpack_from(">I", data, body_end)[0]
        if prev_tag_size != data_size + 11:
            raise FlvError(
                f"PreviousTagSize 不匹配 @0x{pos:x}: "
                f"{prev_tag_size} != {data_size + 11}"
            )

        tag = Tag(
            tag_type=tag_type,
            data_size=data_size,
            timestamp_ms=timestamp,
            stream_id=stream_id,
            body=body,
            offset=tag_offset,
            prev_tag_size=prev_tag_size,
        )
        _annotate(tag)
        if tag.tag_type == TAG_SCRIPT:
            meta = _parse_script(body)
            if meta:
                flv.metadata.update(meta)
        flv.tags.append(tag)
        pos = body_end + 4
        index += 1
    return flv


def _annotate(tag: Tag) -> None:
    """根据 tag 类型解析首字节，填充便捷属性。"""
    body = tag.body
    if tag.tag_type == TAG_SCRIPT:
        tag.kind = "script"
        return
    if tag.tag_type == TAG_AUDIO:
        tag.kind = "audio"
        if not body:
            raise FlvError("audio tag 体为空")
        byte0 = body[0]
        sound_format = (byte0 >> 4) & 0x0F
        sound_rate = (byte0 >> 2) & 0x03
        sound_size = (byte0 >> 1) & 0x01
        sound_type = byte0 & 0x01
        if sound_format == SF_AAC:
            tag.codec = "AAC"
            if len(body) < 2:
                raise FlvError("AAC tag 缺少 AACPacketType 字节")
            aac_packet_type = body[1]
            tag.is_sequence_header = aac_packet_type == AAC_SEQ_HEADER
        else:
            tag.codec = f"SoundFormat({sound_format})"
        tag._sound_rate_idx = sound_rate      # type: ignore[attr-defined]
        tag._sound_size = sound_size          # type: ignore[attr-defined]
        tag._sound_type = sound_type          # type: ignore[attr-defined]
        return
    if tag.tag_type == TAG_VIDEO:
        tag.kind = "video"
        if not body:
            raise FlvError("video tag 体为空")
        byte0 = body[0]
        frame_type = (byte0 >> 4) & 0x0F      # 1=keyframe, 2=inter
        codec_id = byte0 & 0x0F               # 7=AVC
        tag.keyframe = frame_type == 1
        if codec_id == 7:
            tag.codec = "AVC"
            if len(body) < 5:
                raise FlvError("AVC tag 缺少 AVCPacketType/CTS 字段")
            avc_packet_type = body[1]
            # composition time: 24bit 有符号，单位与 FLV 时间戳同为 ms
            cts24 = (body[2] << 16) | (body[3] << 8) | body[4]
            if cts24 & 0x800000:
                cts24 -= 1 << 24
            tag.cts_ms = cts24
            tag.is_sequence_header = avc_packet_type == AVC_SEQ_HEADER
        else:
            tag.codec = f"CodecID({codec_id})"
        return
    tag.kind = f"unknown({tag.tag_type})"


# ---------------------------------------------------------------------------
# AMF0 解码（仅覆盖 onMetadata 常见类型）
# ---------------------------------------------------------------------------

AMF_NUMBER = 0x00
AMF_BOOLEAN = 0x01
AMF_STRING = 0x02
AMF_OBJECT = 0x03
AMF_NULL = 0x05
AMF_UNDEFINED = 0x06
AMF_ECMA_ARRAY = 0x08
AMF_OBJECT_END = 0x09
AMF_STRICT_ARRAY = 0x0A


def _amf_string(buf: bytes, pos: int) -> tuple[str, int]:
    (length,) = struct.unpack_from(">H", buf, pos)
    pos += 2
    val = buf[pos:pos + length].decode("utf-8", errors="replace")
    return val, pos + length


def _amf_decode(buf: bytes, pos: int) -> tuple[Any, int]:
    marker = buf[pos]
    pos += 1
    if marker == AMF_NUMBER:
        (val,) = struct.unpack_from(">d", buf, pos)
        return val, pos + 8
    if marker == AMF_BOOLEAN:
        return bool(buf[pos]), pos + 1
    if marker == AMF_STRING:
        return _amf_string(buf, pos)
    if marker in (AMF_NULL, AMF_UNDEFINED):
        return None, pos
    if marker in (AMF_OBJECT, AMF_ECMA_ARRAY):
        if marker == AMF_ECMA_ARRAY:
            pos += 4  # array count（不可信，按 object 方式读）
        out: dict[str, Any] = {}
        while True:
            if pos >= len(buf):
                raise FlvError("AMF object 未正常结束")
            # 结束序列为 空字符串key(00 00) + 0x09；也容忍直接出现 0x09。
            if buf[pos] == AMF_OBJECT_END:
                return out, pos + 1
            key, pos = _amf_string(buf, pos)
            if key == "" and buf[pos] == AMF_OBJECT_END:
                return out, pos + 1
            val, pos = _amf_decode(buf, pos)
            out[key] = val
        return out, pos
    if marker == AMF_STRICT_ARRAY:
        (count,) = struct.unpack_from(">I", buf, pos)
        pos += 4
        arr = []
        for _ in range(count):
            val, pos = _amf_decode(buf, pos)
            arr.append(val)
        return arr, pos
    raise FlvError(f"不支持的 AMF0 marker: 0x{marker:02x} @0x{pos - 1:x}")


_ON_METADATA = "on" + "MetaData"


def _parse_script(body: bytes) -> dict[str, Any]:
    """解析 onMetadata script tag，返回元数据对象（非 onMetadata 返回空 dict）。"""
    try:
        name, pos = _amf_decode(body, 0)
        if name != _ON_METADATA:
            return {}
        meta, _ = _amf_decode(body, pos)
    except FlvError:
        return {}
    return meta if isinstance(meta, dict) else {}
