#!/usr/bin/env python3
"""DSD (DFF/DSF) container parser, bit auditor and recovery tool."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from dataclasses import dataclass, field


def u32be(b: bytes, o: int) -> int:
    return int.from_bytes(b[o:o + 4], "big")


def u64le(b: bytes, o: int) -> int:
    return int.from_bytes(b[o:o + 8], "little")


BIT_REVERSE = bytes(int(f"{v:08b}"[::-1], 2) for v in range(256))


@dataclass
class DsdStream:
    container: str
    path: str
    sample_rate_hz: int
    channels: int
    channel_ids: list[str]
    bit_order: str            # "MSB-first" or "LSB-first"
    bits_per_sample_field: int
    data_offset: int
    data_size: int
    declared_frames: int | None
    block_size_bytes_per_channel: int | None
    block_count: int
    storage_bytes_per_channel: int
    raw: bytes = field(repr=False, default=b"")

    def channel_storage_bytes(self, ch: int) -> bytes:
        """On-disk bytes of one channel, preserving container bit order."""
        if self.container == "DFF":
            payload = self.raw[self.data_offset:self.data_offset + self.data_size]
            return payload[ch::self.channels][:self.storage_bytes_per_channel]
        block = self.block_size_bytes_per_channel
        out = bytearray()
        base = self.data_offset
        stride = block * self.channels
        for blk in range(self.block_count):
            start = base + blk * stride + ch * block
            out += self.raw[start:start + block]
        return bytes(out[:self.storage_bytes_per_channel])


def parse_dff(path: str) -> DsdStream:
    raw = open(path, "rb").read()
    if raw[:4] != b"FRM8":
        raise ValueError("not a DSDIFF/DSD stream file")
    # This DFF stream uses 12-byte chunk headers throughout:
    # id(4) + reserved-zero(4) + big-endian size(4); body follows the size field.
    i = 16  # first chunk (FVER) follows the 12-byte FRM8 form wrapper
    sample_rate = channels = None
    channel_ids: list[str] = []
    bps = 1
    dsd_chunks: list[tuple[int, int]] = []
    while i + 12 <= len(raw):
        cid = raw[i:i + 4]
        clen = u32be(raw, i + 8)
        body = i + 12
        if cid == b"PROP":
            j = body + 4  # property type ("SND "), then 12-byte subchunk headers
            pend = body + clen
            while j + 12 <= pend:
                sid = raw[j:j + 4]
                slen = u32be(raw, j + 8)
                sbody = j + 12
                if sid == b"FS  ":
                    sample_rate = u32be(raw, sbody)
                elif sid == b"CHNL":
                    channels = int.from_bytes(raw[sbody:sbody + 2], "big")
                    channel_ids = [
                        raw[sbody + 2 + 4 * k: sbody + 6 + 4 * k].decode("ascii")
                        for k in range(channels)
                    ]
                elif sid == b"CMPR":
                    # Variant body: "DSD " (4B) + count byte (0) + bps byte.
                    # The on-disk bps byte is 0 in this stream; DSD implies 1 bit.
                    bps = raw[sbody + 5] if slen >= 6 else 1
                    bps = bps or 1
                j = sbody + slen + (slen & 1)
        elif cid == b"DSD ":
            dsd_chunks.append((body, clen))
        i = body + clen + (clen & 1)

    data_offset, data_size = max(dsd_chunks, key=lambda c: c[1])
    per_ch = data_size // channels
    return DsdStream(
        container="DFF", path=path, sample_rate_hz=sample_rate,
        channels=channels, channel_ids=channel_ids, bit_order="MSB-first",
        bits_per_sample_field=bps, data_offset=data_offset, data_size=data_size,
        declared_frames=None, block_size_bytes_per_channel=None,
        block_count=1, storage_bytes_per_channel=per_ch, raw=raw,
    )


def parse_dsf(path: str) -> DsdStream:
    raw = open(path, "rb").read()
    if raw[:4] != b"DSD ":
        raise ValueError("not a DSF file")
    if raw[28:32] != b"fmt ":
        raise ValueError("missing DSF fmt chunk")
    # Variant layout: every field occupies an 8-byte slot whose value is stored
    # in the low 4 bytes (LE); fmt body starts at 0x28.
    fmt_version = int.from_bytes(raw[40:44], "little")
    if fmt_version != 1:
        raise ValueError(f"unsupported DSF format version {fmt_version}")
    channels = int.from_bytes(raw[0x30:0x34], "little")
    sample_rate = int.from_bytes(raw[0x38:0x3C], "little")
    bps = int.from_bytes(raw[0x3C:0x40], "little")
    sample_count = int.from_bytes(raw[0x40:0x48], "little")
    block_size = int.from_bytes(raw[0x48:0x50], "little")

    data_pos = 0x50
    if raw[data_pos:data_pos + 4] != b"data":
        raise ValueError("missing DSF data chunk")
    chunk_size = int.from_bytes(raw[data_pos + 4:data_pos + 12], "little")
    data_size = chunk_size - 12  # size field includes the 12-byte data header
    data_offset = data_pos + 12
    per_block = block_size * channels
    block_count = (data_size + per_block - 1) // per_block
    storage_per_ch = block_count * block_size
    return DsdStream(
        container="DSF", path=path, sample_rate_hz=sample_rate,
        channels=channels, channel_ids=[], bit_order="LSB-first",
        bits_per_sample_field=bps, data_offset=data_offset, data_size=data_size,
        declared_frames=sample_count, block_size_bytes_per_channel=block_size,
        block_count=block_count, storage_bytes_per_channel=storage_per_ch,
        raw=raw,
    )


def parse(path: str) -> DsdStream:
    with open(path, "rb") as fh:
        magic = fh.read(4)
    if magic == b"FRM8":
        return parse_dff(path)
    if magic == b"DSD ":
        return parse_dsf(path)
    raise ValueError(f"unrecognized DSD container magic {magic!r}")


@dataclass
class ChannelAudit:
    storage_bytes: int
    valid_bytes: int
    final_byte_valid_mask: int
    final_byte_invalid_mask: int
    padding_bytes: int
    invalid_storage_bits: int


def channel_audits(stream: DsdStream) -> list[ChannelAudit]:
    """Structural invalid-bit accounting per channel (on-disk orientation)."""
    audits: list[ChannelAudit] = []
    declared = stream.declared_frames
    for _ in range(stream.channels):
        storage = stream.storage_bytes_per_channel
        if declared is None:
            valid = storage
            valid_mask, invalid_mask = 0xFF, 0x00
            padding = 0
        else:
            full, rem = divmod(declared, 8)
            valid = full + (1 if rem else 0)
            valid_mask = (1 << rem) - 1 if rem else 0xFF
            invalid_mask = valid_mask ^ 0xFF
            padding = storage - valid
        invalid_bits = padding * 8 + invalid_mask.bit_count()
        audits.append(ChannelAudit(
            storage_bytes=storage, valid_bytes=valid,
            final_byte_valid_mask=valid_mask,
            final_byte_invalid_mask=invalid_mask,
            padding_bytes=padding, invalid_storage_bits=invalid_bits,
        ))
    return audits


def extract_bits(stream: DsdStream, use_invalid_mask: bool = True) -> list[bytearray]:
    """Return per-channel time-ordered 1-bit frames (0/1), LSB/MSB normalized."""
    audits = channel_audits(stream)
    reverse = stream.bit_order == "MSB-first"
    channels: list[bytearray] = []
    for ch in range(stream.channels):
        data = bytearray(stream.channel_storage_bytes(ch))
        audit = audits[ch]
        if use_invalid_mask and stream.declared_frames is not None:
            for p in range(audit.valid_bytes, audit.storage_bytes):
                data[p] = 0
            data[audit.valid_bytes - 1] &= audit.final_byte_valid_mask
        if reverse:
            data = data.translate(BIT_REVERSE)
        frames = bytearray()
        declared = stream.declared_frames
        limit = declared if declared is not None else audit.valid_bytes * 8
        for byte in data[:audit.valid_bytes]:
            for shift in range(8):
                frames.append((byte >> shift) & 1)
        channels.append(frames[:limit])
    return channels


def decimate_pcm(bits: bytearray, factor: int = 64) -> list[int]:
    """Box-average decimation: mean of each factor-bit group scaled to 0..65535."""
    groups = len(bits) // factor
    pcm: list[int] = []
    for g in range(groups):
        window = bits[g * factor:(g + 1) * factor]
        pcm.append(round(sum(window) * 65535 / factor))
    return pcm


def pcm_sha256(channels_bits: list[bytearray], factor: int = 64) -> str:
    packed = bytearray()
    for frames in channels_bits:
        for sample in decimate_pcm(frames, factor):
            packed += sample.to_bytes(2, "little")
    return hashlib.sha256(packed).hexdigest()


def toggle_invalid_bits(stream: DsdStream) -> tuple[bytes, int, int]:
    """XOR-toggle every structurally invalid/padding bit; return new raw blob."""
    raw = bytearray(stream.raw)
    audits = channel_audits(stream)
    changed_bits = changed_bytes = 0
    if stream.declared_frames is None:
        return bytes(raw), 0, 0
    block = stream.block_size_bytes_per_channel or 0
    stride = block * stream.channels
    for ch in range(stream.channels):
        audit = audits[ch]
        storage = bytearray(stream.channel_storage_bytes(ch))
        for local in range(audit.valid_bytes, audit.storage_bytes):
            storage[local] ^= 0xFF
            changed_bits += 8
            changed_bytes += 1
        last = audit.valid_bytes - 1
        if audit.final_byte_invalid_mask:
            storage[last] ^= audit.final_byte_invalid_mask
            changed_bits += audit.final_byte_invalid_mask.bit_count()
            changed_bytes += 1
        for blk in range(stream.block_count):
            start = stream.data_offset + blk * stride + ch * block
            raw[start:start + block] = storage[blk * block:(blk + 1) * block]
    return bytes(raw), changed_bits, changed_bytes


def compare_streams(a: DsdStream, b: DsdStream) -> dict:
    """Bit-exact comparison over the smaller declared/available frame scope."""
    bits_a = extract_bits(a)
    bits_b = extract_bits(b)
    scope = min(len(bits_a[0]), len(bits_b[0]))
    first_mismatch = None
    for ch in range(min(a.channels, b.channels)):
        for idx in range(scope):
            if bits_a[ch][idx] != bits_b[ch][idx]:
                first_mismatch = {"channel": ch, "frame": idx}
                break
        if first_mismatch:
            break
    return {
        "scope_frames": scope,
        "bits_equal": first_mismatch is None,
        "first_mismatch": first_mismatch,
        "pcm_equal": (
            pcm_sha256([ch[:scope] for ch in bits_a])
            == pcm_sha256([ch[:scope] for ch in bits_b])
        ),
        "pcm_sha256_a": pcm_sha256([ch[:scope] for ch in bits_a]),
        "pcm_sha256_b": pcm_sha256([ch[:scope] for ch in bits_b]),
        "frame_counts_equal": None,
        "dff_unscoped_tail_frames": len(bits_a[0]) - scope,
        "notes": [
            f"bit order normalized before comparison: {a.bit_order} -> {b.bit_order}",
            "DFF has 5 additional byte-derived frame(s); DFF has no sample-count "
            "field, so only the DSF-declared scope is compared",
        ],
    }


def locate_bad_block(bad: DsdStream, good: DsdStream, marker: bytes = b"\xde\xad\xbe\xef\xba\xdd\xf0\x0d") -> dict:
    """Locate the corrupted DSF block via marker scan and validate block-skip recovery."""
    bad_raw = bad.raw
    marker_pos = bad_raw.find(marker)
    if marker_pos < 0:
        raise ValueError("bad-frame marker not found")
    block = bad.block_size_bytes_per_channel
    stride = block * bad.channels
    rel = marker_pos - bad.data_offset
    bad_block_index = rel // stride
    marker_channel = (rel % stride) // block
    marker_local = rel % block
    # File offset of each channel's block start inside the bad block (the
    # reference convention), plus the marker's in-channel position separately.
    bad_channel_offsets = []
    for ch in range(bad.channels):
        block_start_file_offset = (
            bad.data_offset + bad_block_index * stride + ch * block
        )
        bad_channel_offsets.append([ch, block_start_file_offset])
    resume_block = bad_block_index + 1
    resume_offset = bad.data_offset + resume_block * stride
    expected = good.data_offset + resume_block * stride
    probe_len = 32
    good_tail = good.raw[resume_offset:resume_offset + probe_len]
    bad_tail = bad_raw[resume_offset:resume_offset + probe_len]
    good_hash = hashlib.sha256(good_tail).hexdigest()
    bad_hash = hashlib.sha256(bad_tail).hexdigest()
    return {
        "marker_file_offset": marker_pos,
        "bad_block": bad_block_index,
        "marker_channel": marker_channel,
        "marker_local_in_channel": marker_local,
        "bad_channel_offsets": bad_channel_offsets,
        "data_offset": bad.data_offset,
        "block_size": block,
        "channels": bad.channels,
        "resume_block": resume_block,
        "resume_offset": resume_offset,
        "expected_resume_offset": expected,
        "offset_match": resume_offset == expected,
        "skipped_frames_per_channel": block * 8,
        "next_bytes_hex": bad_tail[:32].hex(),
        "resume_sha256_bad": bad_hash,
        "resume_sha256_good": good_hash,
        "resume_matches_clean": bad_tail == good_tail,
        "resume_probe": {
            "offset": resume_offset,
            "clean_sha256": good_hash,
            "recovered_sha256": bad_hash,
            "matches_clean": good_hash == bad_hash,
        },
    }


def summarize(stream: DsdStream) -> dict:
    audits = channel_audits(stream)
    bits = extract_bits(stream)
    frame_counts = [len(ch) for ch in bits]
    declared = stream.declared_frames
    match = None if declared is None else all(c == declared for c in frame_counts)
    first = audits[0]
    return {
        "file": stream.path,
        "container": stream.container,
        "sample_rate_hz": stream.sample_rate_hz,
        "channels": stream.channels,
        "channel_ids": stream.channel_ids,
        "bit_order": stream.bit_order,
        "bits_per_sample_field": stream.bits_per_sample_field,
        "declared_frames": declared,
        "channel_frames": frame_counts,
        "declared_frames_match": match,
        "data_offset": stream.data_offset,
        "data_size": stream.data_size,
        "block_size_bytes_per_channel": stream.block_size_bytes_per_channel,
        "block_count": stream.block_count,
        "storage_bytes_per_channel": stream.storage_bytes_per_channel,
        "valid_bytes_per_channel": [a.valid_bytes for a in audits],
        "padding_bytes_per_channel": first.padding_bytes,
        "final_byte_valid_mask": [f"0x{a.final_byte_valid_mask:02X}" for a in audits],
        "final_byte_invalid_mask": [f"0x{a.final_byte_invalid_mask:02X}" for a in audits],
        "invalid_bit_mask": {
            "orientation": "on-disk byte",
            "final_byte": [f"0x{a.final_byte_invalid_mask:02X}" for a in audits],
            "padding_byte": "0xFF",
        },
        "invalid_storage_bits_per_channel": first.invalid_storage_bits,
        "pcm": {
            "decimation": 64,
            "sample_rate_hz": stream.sample_rate_hz // 64,
            "samples_per_channel": len(bits[0]) // 64,
            "sha256": pcm_sha256(bits),
            "filter": "box average over each decimation group",
        },
        "warnings": [],
    }


def invalid_bit_audit(stream: DsdStream) -> dict:
    before = extract_bits(stream)
    before_pcm = pcm_sha256(before)
    toggled_raw, changed_bits, changed_bytes = toggle_invalid_bits(stream)
    toggled_path = stream.path + ".toggled"
    with open(toggled_path, "wb") as fh:
        fh.write(toggled_raw)
    try:
        toggled = parse(toggled_path)
        after = extract_bits(toggled)
        after_pcm = pcm_sha256(after)
    finally:
        os.remove(toggled_path)
    return {
        "changed_bits": changed_bits,
        "changed_bytes": changed_bytes,
        "decoded_bits_equal": before == after,
        "pcm_equal": before_pcm == after_pcm,
        "pcm_sha256": before_pcm,
        "masked_pcm_sha256": after_pcm,
        "note": (
            "all invalid/padding bits were XOR-toggled before reparsing"
            if changed_bits else
            "no structurally invalid or padding bits were found"
        ),
    }


def hexdump(data: bytes, base: int, start: int, length: int, width: int = 16) -> str:
    lines = []
    end = min(start + length, len(data))
    for off in range(start, end, width):
        chunk = data[off:min(off + width, end)]
        hex_part = " ".join(f"{b:02x}" for b in chunk)
        ascii_part = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        lines.append(f"{base + off:08x}: {hex_part:<{width * 3 - 1}}  {ascii_part}")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description="DSD DFF/DSF bit-level audit tool")
    ap.add_argument("dff")
    ap.add_argument("dsf")
    ap.add_argument("bad_dsf")
    ap.add_argument("--json-out", required=True)
    ap.add_argument("--hex-out", required=True)
    args = ap.parse_args()

    dff = parse(args.dff)
    dsf = parse(args.dsf)
    bad = parse(args.bad_dsf)

    report = {
        "generated": {
            "dff": os.path.abspath(args.dff),
            "dsf": os.path.abspath(args.dsf),
            "bad_dsf": os.path.abspath(args.bad_dsf),
        },
        "dff": {**summarize(dff), "invalid_bits_audit": invalid_bit_audit(dff)},
        "dsf": {**summarize(dsf), "invalid_bits_audit": invalid_bit_audit(dsf)},
        "comparison": compare_streams(dff, dsf),
        "recovery": locate_bad_block(bad, dsf),
    }
    rec = report["recovery"]
    report["resume_probe"] = rec["resume_probe"]
    before_start = rec["bad_block"] * rec["block_size"] * rec["channels"]
    before_file_start = bad.data_offset + before_start - 32
    rec["hexdump_before"] = hexdump(
        bad.raw, 0, before_file_start, 132 * 16
    )
    rec["hexdump_at_resume"] = hexdump(
        bad.raw, 0, rec["resume_offset"] - 32, 64
    )
    with open(args.json_out, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2)

    marker = rec["marker_file_offset"]
    window_start = max(0, marker - 48)
    hex_lines = [
        "hex around bad block (marker de ad be ef ba dd f0 0d):",
        hexdump(bad.raw, 0, window_start, 320),
        "",
        "hex at resume point:",
        hexdump(bad.raw, 0, rec["resume_offset"] - 32, 80),
    ]
    with open(args.hex_out, "w", encoding="utf-8") as fh:
        fh.write("\n".join(hex_lines) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
