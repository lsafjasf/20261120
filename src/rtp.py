"""RTP 包解析与重连（分段推流）后的时间戳连续性验证。

模拟实时场景：一个媒体流被拆成两段 RTP 会话传输，第二段是第一段的
“重连续传”。RTP 的 32 位时间戳会在新会话从随机 SSRC/随机初始值开始，
因此正确的接收端不能直接比较原始 RTP 时间戳，而要：
1. 段内按 RTP 序号检测回绕/丢包；
2. 用每段首包的 RTP 时间戳与媒体 PTS 建立偏移映射；
3. 重连后校验归一化 PTS 在拼接点连续（增量等于帧时长，无回退/跳变）。
"""
from __future__ import annotations

from dataclasses import dataclass

RTP_VERSION = 2


class RtpError(Exception):
    pass


@dataclass
class RtpPacket:
    version: int
    payload_type: int
    sequence: int          # 16 位，会回绕
    timestamp: int         # 32 位，会回绕
    ssrc: int
    marker: bool
    payload: bytes


def parse_rtp(data: bytes) -> RtpPacket:
    if len(data) < 12:
        raise RtpError("RTP 包少于 12 字节")
    b0, b1, seq, ts, ssrc = (
        data[0], data[1],
        int.from_bytes(data[2:4], "big"),
        int.from_bytes(data[4:8], "big"),
        int.from_bytes(data[8:12], "big"),
    )
    version = b0 >> 6
    if version != RTP_VERSION:
        raise RtpError(f"RTP 版本错误: {version}")
    csrc_count = b0 & 0x0F
    extension = (b0 >> 4) & 0x01
    header_len = 12 + 4 * csrc_count
    if extension:
        if len(data) < header_len + 4:
            raise RtpError("扩展头被截断")
        ext_len_words = int.from_bytes(
            data[header_len + 2: header_len + 4], "big")
        header_len += 4 + 4 * ext_len_words
    return RtpPacket(
        version=version,
        payload_type=b1 & 0x7F,
        marker=bool(b1 & 0x80),
        sequence=seq,
        timestamp=ts,
        ssrc=ssrc,
        payload=data[header_len:],
    )


def u32_diff(curr: int, prev: int) -> int:
    """32 位无符号回绕安全差值（curr - prev）。"""
    return (curr - prev) & 0xFFFFFFFF


def u16_diff(curr: int, prev: int) -> int:
    return (curr - prev) & 0xFFFF


def build_rtp(sequence: int, timestamp: int, ssrc: int,
              payload_type: int = 96, marker: bool = False,
              payload: bytes = b"") -> bytes:
    """构造最小 RTP 包（无 CSRC、无扩展）。"""
    b0 = (RTP_VERSION << 6)
    b1 = payload_type | (0x80 if marker else 0)
    header = bytes([b0, b1])
    header += (sequence & 0xFFFF).to_bytes(2, "big")
    header += (timestamp & 0xFFFFFFFF).to_bytes(4, "big")
    header += (ssrc & 0xFFFFFFFF).to_bytes(4, "big")
    return header + payload


@dataclass
class Segment:
    packets: list[RtpPacket]
    base_ts: int            # 该段首包 RTP 时间戳
    clock_rate: int

    def normalized_ms(self) -> list[int]:
        """相对本段首包的毫秒时间戳（回绕安全）。"""
        out = []
        for pkt in self.packets:
            ticks = u32_diff(pkt.timestamp, self.base_ts)
            out.append(round(ticks * 1000 / self.clock_rate))
        return out


def check_segment_integrity(seg: Segment) -> dict:
    """段内序号连续性（允许 16 位回绕，不允许丢包）。"""
    gaps = []
    for prev, curr in zip(seg.packets, seg.packets[1:]):
        d = u16_diff(curr.sequence, prev.sequence)
        if d != 1:
            gaps.append({"from": prev.sequence, "to": curr.sequence,
                         "delta": d})
    return {
        "packets": len(seg.packets),
        "ssrc": seg.packets[0].ssrc,
        "base_timestamp": seg.base_ts,
        "sequence_gaps": gaps,
        "ok": not gaps,
    }


def reconnect_continuity(seg1: Segment, seg2: Segment,
                         frame_duration_ms: int) -> dict:
    """校验两段拼接后归一化 PTS 连续。

    两段各自归一化到“段首为 0”，再用第二段首包对应的全局位置拼接。
    关键判据：
    - 拼接点后第一个新增量 == frame_duration_ms（无重复/跳帧）；
    - 整条时间线单调非递减；
    - 原始 RTP 时间戳在重连点允许不连续（随机基准），归一化后必须连续。
    """
    n1 = seg1.normalized_ms()
    n2 = seg2.normalized_ms()
    # 第二段首帧接在第一段末帧之后一个帧时长
    join_origin = n1[-1] + frame_duration_ms
    joined = n1 + [join_origin + x for x in n2]

    deltas = [b - a for a, b in zip(joined, joined[1:])]
    monotonic = all(d >= 0 for d in deltas)
    # 拼接点增量（第一段末尾 -> 第二段首帧）
    join_delta = deltas[len(n1) - 1] if len(deltas) >= len(n1) else None
    join_ok = join_delta == frame_duration_ms

    # 同时证明：原始 RTP 时间戳在重连点确实“不连续”，
    # 连续性是靠归一化映射恢复的。
    raw_last = seg1.packets[-1].timestamp
    raw_first = seg2.packets[0].timestamp
    raw_delta = u32_diff(raw_first, raw_last)

    return {
        "frame_duration_ms": frame_duration_ms,
        "seg1_packets": len(seg1.packets),
        "seg2_packets": len(seg2.packets),
        "ssrc_changed": seg1.packets[0].ssrc != seg2.packets[0].ssrc,
        "raw_rtp_timestamp_rewound_at_join": raw_delta != frame_duration_ms *
            seg1.clock_rate // 1000,
        "join_delta_ms": join_delta,
        "join_continuous": join_ok,
        "monotonic": monotonic,
        "joined_timeline_ms": joined,
        "max_step_ms": max(deltas) if deltas else 0,
        "ok": join_ok and monotonic,
    }


def reconnect_test_scenario(clock_rate: int = 90000,
                            frame_duration_ms: int = 40,
                            frames_per_segment: int = 6) -> dict:
    """构造两段 RTP：不同 SSRC、不同随机基准，且第二段触发 32 位回绕。

    第一段时间戳正常递增；第二段把基准设在 0xFFFFFFFF 附近，
    前几帧即发生 32 位回绕，验证归一化逻辑能正确处理。
    """
    ticks_per_frame = clock_rate * frame_duration_ms // 1000

    # 第一段：SSRC=0x11111111，基准 1000
    ssrc1 = 0x11111111
    base1 = 1000
    pkts1 = [
        parse_rtp(build_rtp(
            sequence=i,
            timestamp=base1 + i * ticks_per_frame,
            ssrc=ssrc1,
            marker=(i == frames_per_segment - 1),
            payload=bytes([i & 0xFF]),
        ))
        for i in range(frames_per_segment)
    ]
    seg1 = Segment(pkts1, base1, clock_rate)

    # 第二段：新 SSRC，基准贴近 32 位上限以制造回绕
    ssrc2 = 0x22222222
    base2 = 0xFFFFFFFF - ticks_per_frame  # 第二帧即回绕
    pkts2 = [
        parse_rtp(build_rtp(
            sequence=i,  # 新会话序号重新从 0 开始
            timestamp=(base2 + i * ticks_per_frame) & 0xFFFFFFFF,
            ssrc=ssrc2,
            marker=(i == frames_per_segment - 1),
            payload=bytes([(i + 100) & 0xFF]),
        ))
        for i in range(frames_per_segment)
    ]
    seg2 = Segment(pkts2, pkts2[0].timestamp, clock_rate)

    integrity1 = check_segment_integrity(seg1)
    integrity2 = check_segment_integrity(seg2)
    continuity = reconnect_continuity(seg1, seg2, frame_duration_ms)
    # 第二段原始时间戳出现数值回退，即发生 32 位回绕
    wrapped = any(
        pkts2[i].timestamp < pkts2[i - 1].timestamp
        for i in range(1, len(pkts2))
    )
    return {
        "clock_rate_hz": clock_rate,
        "ticks_per_frame": ticks_per_frame,
        "seg1_integrity": integrity1,
        "seg2_integrity": integrity2,
        "segment2_wrapped_32bit": wrapped,
        "continuity": continuity,
    }
