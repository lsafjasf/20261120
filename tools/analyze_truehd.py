#!/usr/bin/env python3
"""Drive the TrueHD parser and emit a structured analysis report."""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from truehd_parser import (  # noqa: E402
    CH_MAP_5BIT,
    SYNC_WORD,
    has_major_sync,
    index_access_units,
    read_major_sync,
    substream_stats,
)


def analyze(path):
    data = open(path, "rb").read()
    sync_offsets = []
    idx = 0
    while True:
        found = data.find(SYNC_WORD, idx)
        if found < 0:
            break
        sync_offsets.append(found)
        idx = found + 1
    major = read_major_sync(data, sync_offsets[0])
    num_substreams = major["num_substreams"]

    frames, truncated, parity_ok, parity_checks = index_access_units(data)
    stats = substream_stats(data, frames, num_substreams)

    complete = sum(1 for f in frames if f["complete"])
    sample_rate = major["sample_rate_hz"]
    au_samples = major["access_unit_samples"]
    duration_seconds = len(frames) * au_samples / sample_rate

    substreams = []
    for idx_sub in range(num_substreams):
        st = stats[idx_sub]
        bitrate = st["bytes"] * 8 * sample_rate / (
            st["frames_complete"] * au_samples)
        substreams.append({
            "substream": idx_sub,
            "frames_present": st["frames_present"],
            "frames_complete": st["frames_complete"],
            "bytes": st["bytes"],
            "bitrate_bps": round(bitrate, 1),
            "bitrate_kbps": round(bitrate / 1000, 2),
            "major_sync_frames": st["major_sync_frames"],
            "restart_headers": st["restart_headers"],
            "restart_detail": st["restart_detail"],
        })

    arrangement = major["stream1_channel_arrangement"]
    declared_groups = CH_MAP_5BIT.get(arrangement, [])

    # Rebuild channel mapping from restart-header partitions.
    rebuilt = []
    coded_masks = []
    partitions = []
    for idx_sub in range(num_substreams):
        rh = stats[idx_sub]["restart_detail"]
        mask = int(rh["coded_channels_mask"], 16)
        coded_masks.append(rh["coded_channels_mask"])
        partitions.append((idx_sub, mask, rh["matrix_to_output_assignment"]))
    for output_ch in range(len(declared_groups)):
        owner = next((idx_sub for idx_sub, mask, _ in partitions
                      if mask & (1 << output_ch)), None)
        mask = next(mask for idx_sub, mask, _ in partitions if idx_sub == owner)
        assignment = next(a for idx_sub, _, a in partitions if idx_sub == owner)
        matrix_ch = assignment.index(output_ch) if output_ch in assignment else None
        rebuilt.append({
            "output_index": output_ch,
            "channel": declared_groups[output_ch],
            "substream": owner,
            "matrix_channel": matrix_ch,
            "coded": bool(mask & (1 << output_ch)),
        })

    union_count = 0
    overlap = False
    seen = 0
    for mask_hex in coded_masks:
        mask = int(mask_hex, 16)
        overlap = overlap or bool(seen & mask)
        seen |= mask
    union_count = bin(seen).count("1")

    gap_counts = {}
    sync_frame_indexes = [f["frame_index"] for f in frames if f["major_sync"]]
    for a, b in zip(sync_frame_indexes, sync_frame_indexes[1:]):
        gap = b - a
        gap_counts[gap] = gap_counts.get(gap, 0) + 1

    tail = []
    for entry in truncated:
        off = entry["offset"]
        present = entry["bytes_present"]
        declared = entry["declared_bytes"]
        record = dict(entry)
        record["present_byte_range_half_open"] = [off, off + present]
        record["present_byte_range_inclusive"] = [off, off + present - 1]
        record["full_declared_range_half_open"] = [off, off + declared]
        record["full_declared_range_inclusive"] = [off, off + declared - 1]
        record["missing_byte_range_half_open"] = [
            off + present, off + declared]
        tail.append(record)

    return {
        "file": path,
        "file_size_bytes": len(data),
        "stream_type": "TrueHD" if major["stream_type"] == 0xBA else "MLP",
        "major_sync": {
            "first_sync_offset_bytes": sync_offsets[0],
            "header_size_bytes": major["header_size_bytes"],
            "extension_present": major["extension_present"],
            "extension_words": major["extension_words"],
            "checksum_ok": major["checksum_ok"],
            "stored_checksum_le": major["stored_checksum_le"],
            "calculated_crc16": major["calculated_crc16"],
            "checksum_residual": major["checksum_residual"],
            "sample_rate_hz": sample_rate,
            "group2_sample_rate_hz": major["group2_sample_rate_hz"],
            "group1_bits": major["group1_bits"],
            "group2_bits": major["group2_bits"],
            "access_unit_samples": au_samples,
            "is_vbr": major["is_vbr"],
            "peak_bitrate_bps": major["peak_bitrate_bps"],
            "num_substreams": num_substreams,
            "extended_substream_info": major["extended_substream_info"],
            "substream_info": major["substream_info"],
            "stream1_channel_arrangement": arrangement,
            "stream2_channel_arrangement": major["stream2_channel_arrangement"],
            "channel_modifiers": [
                major["channel_modifier_stream0"],
                major["channel_modifier_stream1"],
                major["channel_modifier_stream2"],
            ],
        },
        "frames": {
            "total_access_units_indexed": len(frames),
            "complete_access_units": complete,
            "major_sync_count": len(sync_offsets),
            "major_sync_frame_gap_counts": gap_counts,
            "all_parity_checks_ok": parity_ok,
            "parity_checks": parity_checks,
            "first_offsets": [f["offset"] for f in frames[:8]],
        },
        "substreams": substreams,
        "channels": {
            "declared": {
                "stream1_arrangement": "0x%02x" % arrangement,
                "stream2_arrangement": "0x%02x" % major["stream2_channel_arrangement"],
                "declared_channel_count": len(declared_groups),
                "groups": declared_groups,
            },
            "rebuilt_mapping": rebuilt,
            "rebuilt_channel_count": union_count,
            "substream_count": num_substreams,
            "substream_coded_masks": coded_masks,
            "substream_partitions_overlap_free": not overlap,
            "declared_vs_rebuilt_channels_equal":
                len(declared_groups) == union_count,
            "substream_count_consistent":
                num_substreams == len([m for m in coded_masks if int(m, 16)]),
        },
        "duration_seconds": round(duration_seconds, 3),
        "truncated_tail_frames": tail,
    }


def main(argv):
    reports = [analyze(path) for path in argv[1:]]
    print(json.dumps(reports, indent=2))


if __name__ == "__main__":
    main(sys.argv)
