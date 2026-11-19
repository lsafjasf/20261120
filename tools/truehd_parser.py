#!/usr/bin/env python3
"""Minimal raw TrueHD / MLP bitstream parser.

Parses major sync headers, substream restart headers, per-access-unit
substream segments and truncated tail frames. References follow the
MLP/TrueHD bitstream layout used by FFmpeg's mlp_parser / mlpdec.
"""

import json
import struct

SYNC_WORD = b"\xf8\x72\x6f\xba"

# TrueHD presentation channel assignment names (mlp_channel_layout, 5 bits).
CH_MAP_5BIT = {
    0x00: [],
    0x01: ["LF", "RF"],
    0x02: ["LF", "RF", "C"],
    0x03: ["LF", "RF", "C", "LFE"],
    0x04: ["LF", "RF", "Ls", "Rs"],
    0x05: ["LF", "RF", "Ls", "Rs", "C"],
    0x06: ["LF", "RF", "Ls", "Rs", "C", "LFE"],
    0x07: ["LF", "RF", "Lrs", "Rrs"],
    0x08: ["LF", "RF", "Lrs", "Rrs", "C"],
    0x09: ["LF", "RF", "Lrs", "Rrs", "C", "LFE"],
    0x0A: ["LF", "RF", "Ls", "Rs", "Lrs", "Rrs"],
    0x0B: ["LF", "RF", "Ls", "Rs", "Lrs", "Rrs", "C", "LFE"],
    0x0C: ["Lc", "Rc"],
    0x0D: ["Lc", "Rc", "C"],
    0x0E: ["Lc", "Rc", "C", "LFE"],
    0x0F: ["LF", "RF", "C", "LFE", "Ls", "Rs"],
    0x10: ["LF", "RF", "C", "LFE", "Lrs", "Rrs"],
    0x11: ["LF", "RF", "C", "LFE", "Ls", "Rs", "Lrs", "Rrs"],
    0x12: ["LH", "RH"],
    0x13: ["LH", "RH", "C", "LFE"],
    0x14: ["LH", "RH", "Lrs", "Rrs", "C", "LFE"],
    0x15: ["LF", "RF", "LH", "RH"],
    0x16: ["LF", "RF", "LH", "RH", "C", "LFE"],
    0x17: ["LF", "RF", "Ls", "Rs", "LH", "RH", "C", "LFE"],
    0x18: ["LF", "RF", "Ls", "Rs", "Lrs", "Rrs", "LH", "RH", "C", "LFE", "Lw", "Rw", "Ltf", "Rtf", "Ltm", "Rtm"],
}

# TrueHD 13-bit channel modifier used for stream 2 / object presentation.
CH_MAP_13BIT = {0x0F: CH_MAP_5BIT[0x0F]}


class BitReader:
    def __init__(self, data, base=0):
        self.data = data
        self.pos = base

    def bit(self):
        byte = self.data[self.pos >> 3]
        value = (byte >> (7 - (self.pos & 7))) & 1
        self.pos += 1
        return value

    def bits(self, count):
        value = 0
        for _ in range(count):
            value = (value << 1) | self.bit()
        return value

    def bytes_used(self):
        return (self.pos + 7) >> 3


def crc16_ccitt(data, length):
    crc = 0
    for idx in range(length):
        crc ^= data[idx] << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc


def _build_mlp_crc_table():
    """Reproduce FFmpeg av_crc_init(le=0, bits=16, poly=0x002d).

    Entries are built 32-bit MSB-first then byte-swapped, matching the
    effective 16-bit table used by ff_mlp_checksum16.
    """
    table = []
    for idx in range(256):
        reg = idx << 24
        for _ in range(8):
            if reg & 0x80000000:
                reg = ((reg << 1) & 0xFFFFFFFF) ^ (0x002D << 16)
            else:
                reg = (reg << 1) & 0xFFFFFFFF
        swapped = struct.unpack(">I", struct.pack("<I", reg))[0]
        table.append(swapped & 0xFFFF)
    return table


MLP_CRC_TABLE = _build_mlp_crc_table()


def mlp_crc16(buf, crc=0):
    for byte in buf:
        crc = MLP_CRC_TABLE[(crc & 0xFF) ^ byte] ^ (crc >> 8)
        crc &= 0xFFFF
    return crc


def mlp_checksum16(data, length):
    """CRC over the first length-2 bytes, XOR the trailing LE checksum."""
    crc = mlp_crc16(data[: length - 2])
    stored = struct.unpack("<H", data[length - 2:length])[0]
    return crc ^ stored


def read_major_sync(data, offset):
    """Return parsed major sync header at offset, or None if truncated."""
    if data[offset:offset + 4] != SYNC_WORD:
        return None
    if len(data) - offset < 28:
        return {"truncated_header": True, "offset": offset}
    stream_type = data[offset + 3]
    has_extension = data[offset + 25] & 1
    if has_extension:
        extension_words = data[offset + 26] >> 4
        header_size = 28 + 2 + extension_words * 2
    else:
        extension_words = 0
        header_size = 28
    if len(data) - offset < header_size:
        return {"truncated_header": True, "offset": offset}
    stored_checksum = struct.unpack_from("<H", data, offset + header_size - 2)[0]
    # ff_mlp_checksum16(buf, header_size): CRC over header_size-2 bytes,
    # XOR the little-endian checksum stored in the final two bytes.
    # Mirror ff_mlp_read_major_sync: ff_mlp_checksum16(buf, header_size - 2),
    # whose inner CRC covers header_size - 4 bytes before XORing the LE
    # checksum stored at buf + header_size - 2.
    crc_length = header_size - 4
    calc_crc = mlp_crc16(data[offset:offset + crc_length])
    calc = calc_crc ^ stored_checksum
    # get_bits(gb,24) == 0xf8726f, then stream_type is the next byte (0xba).
    br = BitReader(data, offset * 8)
    br.bits(24)
    stream_type = br.bits(8)
    ch_mod_s0 = ch_mod_s1 = ch_arr5 = ch_mod_s2 = ch_arr13 = 0
    group2_rate_bits = 0
    if stream_type == 0xBA:
        # TrueHD: group1 fixed 24-bit, group2 unused.
        rate_bits = br.bits(4)
        br.bits(4)
        ch_mod_s0 = br.bits(2)
        ch_mod_s1 = br.bits(2)
        ch_arr5 = br.bits(5)
        ch_mod_s2 = br.bits(2)
        ch_arr13 = br.bits(13)
    elif stream_type == 0xBB:
        group1_quant = br.bits(4)
        group2_quant = br.bits(4)
        rate_bits = br.bits(4)
        group2_rate_bits = br.bits(4)
        br.bits(11)
        ch_arr5 = br.bits(5)
    def mlp_samplerate(code):
        if code == 0x0F:
            return 0
        return (44100 if code & 0x8 else 48000) << (code & 0x7)
    sample_rate = mlp_samplerate(rate_bits)
    group2_rate = mlp_samplerate(group2_rate_bits) if stream_type == 0xBB else 0
    group1_bits = 24 if stream_type == 0xBA else 0
    group2_bits = 0
    access_unit_size = 40 << (rate_bits & 7)
    # After the channel section ffmpeg skips 48 bits, then reads VBR etc.
    br.bits(48)
    is_vbr = bool(br.bit())
    peak_field = br.bits(15)
    peak_bitrate = (peak_field * sample_rate + 8) >> 4
    num_substreams = br.bits(4)
    br.bits(2)
    ext_substream_info = br.bits(2)
    substream_info = br.bits(8)
    stream1_map = ch_arr5
    stream2_map = ch_arr13
    return {
        "offset": offset,
        "stream_type": stream_type,
        "header_size_bytes": header_size,
        "extension_present": bool(has_extension),
        "extension_words": extension_words,
        "stored_checksum_le": stored_checksum,
        "calculated_crc16": calc_crc,
        "checksum_residual": calc,
        "checksum_ok": calc == 0,
        "sample_rate_hz": sample_rate,
        "group2_sample_rate_hz": group2_rate,
        "group1_bits": group1_bits,
        "group2_bits": group2_bits,
        "access_unit_samples": access_unit_size,
        "peak_bitrate_bps": peak_bitrate,
        "is_vbr": is_vbr,
        "num_substreams": num_substreams,
        "extended_substream_info": ext_substream_info,
        "substream_info": "0x%02x" % substream_info,
        "channel_modifier_stream0": ch_mod_s0,
        "channel_modifier_stream1": ch_mod_s1,
        "channel_modifier_stream2": ch_mod_s2,
        "stream1_channel_arrangement": stream1_map,
        "stream2_channel_arrangement": stream2_map,
    }


def has_major_sync(data, packet_offset):
    if len(data) - packet_offset < 8:
        return False
    word = struct.unpack_from(">I", data, packet_offset + 4)[0]
    return (word & 0xFFFFFFFE) == 0xF8726FBA


def read_access_unit_header(data, offset):
    """Return (declared_total_bytes, parity_nibble, timing) or None."""
    if len(data) - offset < 4:
        return None
    word0 = struct.unpack_from(">H", data, offset)[0]
    timing = struct.unpack_from(">H", data, offset + 2)[0]
    length_words = word0 & 0x0FFF
    return length_words * 2, (word0 >> 12) & 0xF, timing


def parity_check(data, offset, num_substreams):
    """FFmpeg mlp_parser parity over AU + substream headers (non-sync frames)."""
    parity = 0
    pos = offset
    for idx in range(-1, num_substreams):
        parity ^= data[pos]
        parity ^= data[pos + 1]
        pos += 2
        if idx < 0 or data[pos - 2] & 0x80:
            parity ^= data[pos]
            parity ^= data[pos + 1]
            pos += 2
    return ((((parity >> 4) ^ parity) & 0xF) == 0xF), pos


def parse_substream_headers(data, payload_start, num_substreams, restart_frame):
    """Per FFmpeg mlpdec: 4 flag bits + 12-bit end offset (words).

    extraword (bit15) appends a 16-bit word on TrueHD restart frames.
    """
    segments = []
    pos = payload_start
    data_cursor = payload_start
    for substr in range(num_substreams):
        hword = struct.unpack_from(">H", data, pos)[0]
        extraword = bool(hword & 0x8000)
        nonrestart = bool(hword & 0x4000)
        checkdata = bool(hword & 0x2000)
        end_words = hword & 0x0FFF
        pos += 2
        extra_bytes = 0
        if extraword:
            extra_bytes = 2
            pos += 2
        segments.append({
            "substream": substr,
            "header_word": "0x%04x" % hword,
            "extraword": extraword,
            "nonrestart": nonrestart,
            "checkdata": checkdata,
            "restart": restart_frame,
            "header_extra_bytes": extra_bytes,
            "end_offset_bytes": end_words * 2,
        })
    return segments, pos


def restart_checksum8(buf, bit_size):
    """ff_mlp_restart_checksum (crc_1D, poly 0x1d, init from first byte)."""
    table = []
    for idx in range(256):
        reg = idx
        for _ in range(8):
            reg = ((reg << 1) ^ 0x1D) & 0xFF if reg & 0x80 else (reg << 1) & 0xFF
        table.append(reg)
    num_bytes = (bit_size + 2) // 8
    crc = table[buf[0] & 0x3F]
    for idx in range(1, num_bytes - 1):
        crc = table[crc ^ buf[idx]]
    crc ^= buf[num_bytes - 1]
    for bit_idx in range((bit_size + 2) & 7):
        crc <<= 1
        if crc & 0x100:
            crc ^= 0x11D
        crc ^= (buf[num_bytes] >> (7 - bit_idx)) & 1
    return crc & 0xFF


def read_restart_header(data, byte_offset, max_matrix_std=63):
    """Parse one substream restart header; returns channel partition info."""
    br = BitReader(data, byte_offset * 8)
    param_present = br.bit()
    restart_present = br.bit()
    start = br.pos
    sync_word = br.bits(13)
    sync_ok = sync_word == (0x31EA >> 1)
    noise_type = br.bit()
    br.bits(16)  # output timestamp
    min_channel = br.bits(4)
    max_channel = br.bits(4)
    max_matrix_channel = br.bits(4)
    noise_shift = br.bits(4)
    noisegen_seed = br.bits(23)
    br.bits(19)
    data_check_present = br.bit()
    lossless_check = br.bits(8)
    br.bits(16)
    ch_assign = []
    for _ch in range(max_matrix_channel + 1):
        ch_assign.append(br.bits(6))
    bit_size = br.pos - start
    stored_checksum = br.bits(8)
    calc = restart_checksum8(data[byte_offset:], bit_size)
    coded_mask = ((1 << (max_channel - min_channel + 1)) - 1) << min_channel
    return {
        "param_present": param_present,
        "restart_present": restart_present,
        "sync_ok": sync_ok,
        "noise_type": noise_type,
        "min_channel": min_channel,
        "max_channel": max_channel,
        "max_matrix_channel": max_matrix_channel,
        "noise_shift": noise_shift,
        "noisegen_seed": noisegen_seed,
        "data_check_present": data_check_present,
        "coded_channels_mask": "0x%02x" % coded_mask,
        "matrix_to_output_assignment": ch_assign,
        "restart_checksum_stored": stored_checksum,
        "restart_checksum_calc": calc,
        "checksum_ok": stored_checksum == calc,
        "header_bits": bit_size + 8,
    }


def index_access_units(data):
    """Walk every access unit, honouring truncated final frames."""
    total = len(data)
    offset = 0
    frames = []
    truncated = []
    parity_ok = True
    parity_checks = 0
    while offset < total:
        hdr = read_access_unit_header(data, offset)
        if hdr is None:
            truncated.append({
                "frame_index": len(frames),
                "offset": offset,
                "reason": "fewer than 4 header bytes remain",
                "bytes_present": total - offset,
            })
            break
        declared_bytes, parity_nibble, timing = hdr
        sync = has_major_sync(data, offset)
        present = min(declared_bytes, total - offset)
        complete = declared_bytes <= total - offset
        entry = {
            "frame_index": len(frames),
            "offset": offset,
            "declared_bytes": declared_bytes,
            "bytes_present": present,
            "complete": complete,
            "major_sync": sync,
            "timing": timing,
        }
        if not complete:
            entry["missing_bytes"] = declared_bytes - present
            truncated.append(entry)
            frames.append(entry)
            break
        if not sync:
            ok, _ = parity_check(data, offset, num_substreams=2)
            parity_checks += 1
            parity_ok = parity_ok and ok
            entry["parity_ok"] = ok
        frames.append(entry)
        offset += declared_bytes
    return frames, truncated, parity_ok, parity_checks


def substream_stats(data, frames, num_substreams):
    """Per-substream frame presence, payload bytes and restart headers."""
    stats = {idx: {
        "frames_present": 0,
        "frames_complete": 0,
        "bytes": 0,
        "major_sync_frames": 0,
        "restart_headers": 0,
        "restart_detail": None,
    } for idx in range(num_substreams)}
    for frame in frames:
        off = frame["offset"]
        sync = frame["major_sync"]
        # header_size = 4 (AU hdr) + major sync hdr (if present).
        header_size = 4 + (read_major_sync(data, off + 4)["header_size_bytes"]
                           if sync else 0)
        hdr_block_start = off + header_size
        segments, after_headers = parse_substream_headers(
            data, hdr_block_start, num_substreams, restart_frame=sync)
        substr_header_size = after_headers - hdr_block_start
        data_base = after_headers
        prev_end = 0
        for seg in segments:
            idx = seg["substream"]
            end = seg["end_offset_bytes"]
            seg_bytes = end - prev_end
            prev_end = end
            available = max(0, min(seg_bytes,
                                    frame["bytes_present"] - (data_base - off)))
            stats[idx]["frames_present"] += 1
            if frame["complete"]:
                stats[idx]["frames_complete"] += 1
                stats[idx]["bytes"] += seg_bytes
            else:
                stats[idx]["bytes"] += available
            if sync:
                stats[idx]["major_sync_frames"] += 1
                stats[idx]["restart_headers"] += 1
                # Restart header: 2 flag bits then 13-bit sync at bit 2.
                rh = read_restart_header(data, data_base + prev_end - seg_bytes)
                if stats[idx]["restart_detail"] is None:
                    stats[idx]["restart_detail"] = rh
    return stats
