"""AAC ADTS 帧头解析与 padding 位健壮性测试。

ADTS 头有两种长度：
- protection_absent=1：7 字节（无 CRC）
- protection_absent=0：9 字节（含 2 字节 CRC）
解析器必须按该位逐帧决定跳过字节数，不能假设固定头长，否则
一旦帧流中两种头混合，后续所有帧的定位都会产生累积偏移。
"""
from __future__ import annotations

from dataclasses import dataclass

ADTS_SYNC = 0xFFF

# sampling_frequency_index -> Hz
SAMPLE_RATES = (
    96000, 88200, 64000, 48000, 44100, 44100, 32000, 24000,
    22050, 16000, 12000, 11025, 8000, 7350,
)


class AdtsError(Exception):
    pass


@dataclass
class AdtsHeader:
    offset: int
    version: int               # 0=MPEG-4, 1=MPEG-2
    profile: int               # 1=LC（AAC-LC）
    sample_rate: int
    channels: int
    frame_length: int          # 含头的整帧字节数
    header_length: int         # 7 或 9
    buffer_fullness: int
    raw_block_count: int       # 通常 1


def parse_adts_stream(data: bytes) -> list[AdtsHeader]:
    """逐帧解析 ADTS 流，每帧用 frame_length 前进（而非假设固定步长）。"""
    headers: list[AdtsHeader] = []
    pos = 0
    frame_index = 0
    while pos + 7 <= len(data):
        sync = (data[pos] << 4) | (data[pos + 1] >> 4)
        if sync != ADTS_SYNC:
            raise AdtsError(
                f"帧 {frame_index} @0x{pos:x} 同步字错误: 0x{sync:03x}")
        version = (data[pos + 1] >> 3) & 0x01
        protection_absent = data[pos + 1] & 0x01
        profile = ((data[pos + 2] >> 6) & 0x03)
        sf_idx = (data[pos + 2] >> 2) & 0x0F
        if sf_idx >= len(SAMPLE_RATES):
            raise AdtsError(f"非法 sampling_frequency_index={sf_idx}")
        channels = ((data[pos + 2] & 0x01) << 2) | ((data[pos + 3] >> 6) & 0x03)
        frame_length = (
            ((data[pos + 3] & 0x03) << 11)
            | (data[pos + 4] << 3)
            | ((data[pos + 5] >> 5) & 0x07)
        )
        buffer_fullness = (
            ((data[pos + 5] & 0x1F) << 6) | ((data[pos + 6] >> 2) & 0x3F)
        )
        raw_blocks = (data[pos + 6] & 0x03) + 1
        header_length = 7 if protection_absent else 9
        if frame_length < header_length or pos + frame_length > len(data):
            raise AdtsError(
                f"帧 {frame_index} frame_length={frame_length} 越界")
        headers.append(AdtsHeader(
            offset=pos,
            version=version,
            profile=profile,
            sample_rate=SAMPLE_RATES[sf_idx],
            channels=channels,
            frame_length=frame_length,
            header_length=header_length,
            buffer_fullness=buffer_fullness,
            raw_block_count=raw_blocks,
        ))
        pos += frame_length
        frame_index += 1
    if pos != len(data):
        raise AdtsError(f"流末尾存在 {len(data) - pos} 个残余字节")
    return headers


def build_adts_frame(payload_len: int, sample_rate: int, channels: int,
                     protection_absent: int,
                     crc: bytes = b"\x00\x00") -> bytes:
    """构造单个 ADTS 帧（测试夹具用）。"""
    sf_idx = SAMPLE_RATES.index(sample_rate)
    header_length = 7 if protection_absent else 9
    frame_length = header_length + payload_len
    profile = 1  # AAC-LC
    b = bytearray(header_length)
    b[0] = 0xFF
    b[1] = 0xF0 | protection_absent   # MPEG-4(version=0)、无私有位
    b[2] = (profile << 6) | (sf_idx << 2) | ((channels >> 2) & 0x01)
    b[3] = ((channels & 0x03) << 6) | ((frame_length >> 11) & 0x03)
    b[4] = (frame_length >> 3) & 0xFF
    b[5] = ((frame_length & 0x07) << 5) | 0x1F
    b[6] = 0xFC
    if not protection_absent:
        b[7:9] = crc
    return bytes(b) + bytes(payload_len)


def padding_drift_test() -> dict:
    """混合 7/9 字节头的短样本，验证解析定位无累积偏移。

    构造 N 帧，protection_absent 交替变化（即头长 7/9 交替）。
    若解析器错误地假设固定 7 字节头，从第二帧起就会错位 2 字节，
    且错位量随帧数累积；正确解析则每帧 offset 必须与记录值完全一致。
    """
    sample_rate = 44100
    channels = 2
    payload_sizes = [100, 120, 80, 140, 90, 110, 70, 130]
    stream = b""
    expected: list[tuple[int, int]] = []
    for i, plen in enumerate(payload_sizes):
        protection_absent = i % 2          # 0 -> 9B 头, 1 -> 7B 头
        offset = len(stream)
        frame = build_adts_frame(plen, sample_rate, channels, protection_absent)
        expected.append((offset, len(frame)))
        stream += frame

    headers = parse_adts_stream(stream)
    assert len(headers) == len(payload_sizes)

    # 对比每个 offset 与帧长；任何不匹配都意味着累积偏移
    offset_ok = all(h.offset == exp_off for h, (exp_off, _) in
                    zip(headers, expected))
    length_ok = all(h.frame_length == exp_len for h, (_, exp_len) in
                    zip(headers, expected))
    # 模拟“固定 7 字节头”的错误解析器，证明它确实会累积偏移
    naive_offsets = []
    naive_pos = 0
    for h in headers:
        naive_offsets.append(naive_pos)
        naive_pos += 7 + (h.frame_length - h.header_length)
    naive_drifts = [abs(no - h.offset) for no, h in zip(naive_offsets, headers)]

    return {
        "frame_count": len(headers),
        "mixed_header_lengths": sorted({h.header_length for h in headers}),
        "sample_rate_consistent": len({h.sample_rate for h in headers}) == 1,
        "sample_rate_hz": headers[0].sample_rate,
        "correct_parser_no_drift": offset_ok and length_ok,
        "naive_fixed7_max_drift_bytes": max(naive_drifts),
        "naive_fixed7_drifts": naive_drifts,
        "parsed_offsets": [h.offset for h in headers],
        "header_lengths": [h.header_length for h in headers],
    }
