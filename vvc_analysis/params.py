"""H.266/VVC parameter-set parsing and elementary-stream inventory."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any

from .annexb import (
    BitReader,
    BitstreamError,
    NalUnit,
    iter_nal_units,
    preserve_unknown_nal,
    rbsp_from_ebsp,
)


CHROMA_NAMES = {0: "4:0:0", 1: "4:2:0", 2: "4:2:2", 3: "4:4:4"}
PROFILE_NAMES = {
    1: "Main_10",
    2: "Main_10_444",
    3: "Main_10_Still_Picture",
    4: "Main_10_444_Still_Picture",
    5: "Multilayer_Main_10",
    9: "Multilayer_Main_10_444",
    10: "Multilayer_Main_10_Still_Picture",
    11: "Multilayer_Main_10_444_Still_Picture",
}


@dataclass
class ParameterSet:
    kind: str
    id: int
    nal: NalUnit
    values: dict[str, Any]
    error: str | None = None


@dataclass
class StreamAnalysis:
    data: bytes
    nals: list[NalUnit] = field(default_factory=list)
    parameter_sets: list[ParameterSet] = field(default_factory=list)
    unknown: list[dict] = field(default_factory=list)
    parse_errors: list[dict] = field(default_factory=list)


def _parse_constraint_info(reader: BitReader) -> dict[str, int]:
    values: dict[str, int] = {}
    if reader.read_flag():
        reader.read_bits(3)
        values["max_bitdepth_constraint"] = 16 - reader.read_bits(4)
        values["max_chroma_format_constraint"] = 3 - reader.read_bits(2)
        reader.read_bits(10)
        reader.read_bits(6)
        values["max_log2_ctu_size_constraint"] = (3 - reader.read_bits(2)) + 5
        reader.read_bits(3)
        reader.read_bits(6)
        reader.read_bits(16)
        reader.read_bits(13)
        reader.read_bits(6)
        reserved_bits = reader.read_bits(8)
        if reserved_bits:
            reader.read_bits(reserved_bits)
    reader.align_zero()
    return values


def _parse_profile_tier_level(
    reader: BitReader, profile_tier_present: bool, max_sub_layers_minus1: int
) -> dict[str, Any]:
    values: dict[str, Any] = {}
    if profile_tier_present:
        profile_idc = reader.read_bits(7)
        values["general_profile_idc"] = profile_idc
        values["general_profile"] = PROFILE_NAMES.get(profile_idc, f"profile_{profile_idc}")
        values["general_tier_flag"] = reader.read_flag()
    values["general_level_idc"] = reader.read_bits(8)
    values["frame_only_constraint_flag"] = reader.read_flag()
    values["multilayer_enabled_flag"] = reader.read_flag()
    if profile_tier_present:
        values.update(_parse_constraint_info(reader))

    present = [
        reader.read_flag() for _ in range(max(max_sub_layers_minus1, 0))
    ]
    reader.align_zero()
    for flag in reversed(present):
        if flag:
            reader.read_bits(8)
    if profile_tier_present:
        sub_profiles = reader.read_bits(8)
        for _ in range(sub_profiles):
            reader.read_bits(32)
        values["num_sub_profiles"] = sub_profiles
    return values


def _parse_vps(reader: BitReader) -> dict[str, Any]:
    return {
        "vps_video_parameter_set_id": reader.read_bits(4),
        "vps_max_layers_minus1": reader.read_bits(6),
        "vps_max_sublayers_minus1": reader.read_bits(3),
    }


def _parse_pps(reader: BitReader) -> dict[str, Any]:
    return {
        "pps_pic_parameter_set_id": reader.read_bits(6),
        "pps_seq_parameter_set_id": reader.read_bits(4),
        "pps_mixed_nalu_types_in_pic_flag": reader.read_flag(),
        "pps_pic_width_in_luma_samples": reader.read_ue(),
        "pps_pic_height_in_luma_samples": reader.read_ue(),
    }


def _subpic_parameters(
    reader: BitReader, width: int, height: int, ctb_size: int
) -> None:
    num_subpics_minus1 = reader.read_ue()
    if num_subpics_minus1 == 0:
        return
    independent = reader.read_flag()
    same_size = reader.read_flag()
    width_in_ctbs = (width + ctb_size - 1) // ctb_size
    height_in_ctbs = (height + ctb_size - 1) // ctb_size
    width_bits = (width_in_ctbs - 1).bit_length()
    height_bits = (height_in_ctbs - 1).bit_length()
    for index in range(num_subpics_minus1 + 1):
        if not same_size or index == 0:
            if index > 0 and width > ctb_size:
                reader.read_bits(width_bits)
            if index > 0 and height > ctb_size:
                reader.read_bits(height_bits)
            if index < num_subpics_minus1 and width > ctb_size:
                reader.read_bits(width_bits)
            if index < num_subpics_minus1 and height > ctb_size:
                reader.read_bits(height_bits)
        if not independent:
            reader.read_flag()
            reader.read_flag()
    subpic_id_len_minus1 = reader.read_ue()
    mapping_explicit = reader.read_flag()
    if mapping_explicit and reader.read_flag():
        for _ in range(num_subpics_minus1 + 1):
            reader.read_bits(subpic_id_len_minus1 + 1)


def _parse_sps(reader: BitReader) -> dict[str, Any]:
    values: dict[str, Any] = {}
    values["sps_seq_parameter_set_id"] = reader.read_bits(4)
    values["sps_video_parameter_set_id"] = reader.read_bits(4)
    max_sub_layers_minus1 = reader.read_bits(3)
    values["sps_max_sublayers_minus1"] = max_sub_layers_minus1
    chroma_format_idc = reader.read_bits(2)
    values["sps_chroma_format_idc"] = chroma_format_idc
    values["chroma_format"] = CHROMA_NAMES[chroma_format_idc]
    log2_ctu_size_minus5 = reader.read_bits(2)
    ctb_size = 1 << (log2_ctu_size_minus5 + 5)
    values["sps_log2_ctu_size_minus5"] = log2_ctu_size_minus5
    ptl_present = reader.read_flag()
    values["sps_ptl_dpb_hrd_params_present_flag"] = ptl_present
    if ptl_present:
        values["profile_tier_level"] = _parse_profile_tier_level(
            reader, True, max_sub_layers_minus1
        )

    values["sps_gdr_enabled_flag"] = reader.read_flag()
    rpr_enabled = reader.read_flag()
    values["sps_ref_pic_resampling_enabled_flag"] = rpr_enabled
    if rpr_enabled:
        values["sps_res_change_in_clvs_allowed_flag"] = reader.read_flag()

    width = reader.read_ue()
    height = reader.read_ue()
    values["sps_pic_width_max_in_luma_samples"] = width
    values["sps_pic_height_max_in_luma_samples"] = height

    sub_width_c = 1 if chroma_format_idc in (0, 3) else 2
    sub_height_c = 2 if chroma_format_idc == 1 else 1
    crop = [0, 0, 0, 0]
    conformance_window = reader.read_flag()
    values["sps_conformance_window_flag"] = conformance_window
    if conformance_window:
        crop = [reader.read_ue() for _ in range(4)]
        values["sps_conf_win_left_offset"] = crop[0]
        values["sps_conf_win_right_offset"] = crop[1]
        values["sps_conf_win_top_offset"] = crop[2]
        values["sps_conf_win_bottom_offset"] = crop[3]
    values["display_width"] = width - sub_width_c * (crop[0] + crop[1])
    values["display_height"] = height - sub_height_c * (crop[2] + crop[3])

    subpic_info = reader.read_flag()
    values["sps_subpic_info_present_flag"] = subpic_info
    if subpic_info:
        _subpic_parameters(reader, width, height, ctb_size)

    bit_depth_minus8 = reader.read_ue()
    values["sps_bitdepth_minus8"] = bit_depth_minus8
    values["bit_depth"] = 8 + bit_depth_minus8
    values["resolution"] = f"{values['display_width']}x{values['display_height']}"
    return values


def _parse_parameter_set(nal: NalUnit) -> ParameterSet:
    kind = {14: "VPS", 15: "SPS", 16: "PPS"}[nal.nal_type]
    reader = BitReader(rbsp_from_ebsp(nal.payload))
    try:
        if kind == "VPS":
            values = _parse_vps(reader)
            set_id = values["vps_video_parameter_set_id"]
        elif kind == "SPS":
            values = _parse_sps(reader)
            set_id = values["sps_seq_parameter_set_id"]
        else:
            values = _parse_pps(reader)
            set_id = values["pps_pic_parameter_set_id"]
        return ParameterSet(kind=kind, id=set_id, nal=nal, values=values)
    except (BitstreamError, IndexError, KeyError) as exc:
        id_reader = BitReader(rbsp_from_ebsp(nal.payload))
        id_bits = {"VPS": 4, "SPS": 4, "PPS": 6}[kind]
        set_id = id_reader.read_bits(id_bits) if id_reader.bits_left >= id_bits else -1
        return ParameterSet(
            kind=kind,
            id=set_id,
            nal=nal,
            values={},
            error=f"{type(exc).__name__}: {exc}",
        )


def _parameter_set_inventory(
    parameter_sets: list[ParameterSet],
) -> tuple[list[dict], list[dict]]:
    grouped: dict[tuple[str, int, str], dict] = {}
    order: list[tuple[str, int, str]] = []
    revisions: dict[tuple[str, int], int] = defaultdict(int)
    for item in parameter_sets:
        key = (item.kind, item.id, item.nal.sha256)
        if key not in grouped:
            revisions[(item.kind, item.id)] += 1
            grouped[key] = {
                "kind": item.kind,
                "id": item.id,
                "key": f"{item.kind}:{item.id}:{item.nal.sha256[:12]}",
                "revision": revisions[(item.kind, item.id)],
                "first_start_offset": item.nal.start_offset,
                "first_nal_offset": item.nal.nal_offset,
                "first_start_code_hex": (
                    "00000001" if item.nal.prefix_length == 4 else "000001"
                ),
                "first_prefix_length": item.nal.prefix_length,
                "first_nal_index": item.nal.index,
                "occurrences": 0,
                "payload_sha256": item.nal.sha256,
                "values": item.values,
                "error": item.error,
            }
            order.append(key)
        grouped[key]["occurrences"] += 1

    inventory = [grouped[key] for key in order]
    sps_by_id: dict[int, dict] = {}
    links = []
    for item in parameter_sets:
        if item.kind == "SPS" and not item.error:
            sps_by_id[item.id] = item.values
            continue
        if item.kind != "PPS" or item.error:
            continue
        sps_id = item.values["pps_seq_parameter_set_id"]
        sps = sps_by_id.get(sps_id)
        links.append(
            {
                "pps_id": item.id,
                "sps_id": sps_id,
                "nal_index": item.nal.index,
                "nal_offset": item.nal.nal_offset,
                "sps_resolution": sps.get("resolution") if sps else None,
                "sps_bit_depth": sps.get("bit_depth") if sps else None,
                "sps_chroma_format": sps.get("chroma_format") if sps else None,
                "resolved": sps is not None,
            }
        )
    return inventory, links


def _sps_events(parameter_sets: list[ParameterSet]) -> list[dict]:
    events: list[dict] = []
    previous: dict[str, Any] | None = None
    for item in parameter_sets:
        if item.kind != "SPS" or item.error:
            continue
        current = {key: item.values.get(key) for key in (
            "resolution",
            "bit_depth",
            "chroma_format",
            "sps_pic_width_max_in_luma_samples",
            "sps_pic_height_max_in_luma_samples",
        )}
        if previous is None or current != previous:
            events.append(
                {
                    "event": "parameter_set_switch" if previous else "initial",
                    "sps_id": item.id,
                    "nal_index": item.nal.index,
                    "start_offset": item.nal.start_offset,
                    "nal_offset": item.nal.nal_offset,
                    "before": previous,
                    "after": current,
                }
            )
            previous = current
    return events


def analyze_stream(
    data: bytes,
    unknown_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Parse a VVC Annex B stream and return a JSON-serializable inventory."""
    nals = list(iter_nal_units(data))
    parameter_sets: list[ParameterSet] = []
    unknown: list[dict] = []
    parse_errors: list[dict] = []
    unknown_output = Path(unknown_dir) if unknown_dir is not None else None

    for nal in nals:
        if nal.nal_type in (14, 15, 16):
            parsed = _parse_parameter_set(nal)
            parameter_sets.append(parsed)
            if parsed.error:
                parse_errors.append(
                    {
                        "nal_index": nal.index,
                        "nal_offset": nal.nal_offset,
                        "kind": parsed.kind,
                        "error": parsed.error,
                    }
                )
        if nal.is_unknown:
            if unknown_output is not None:
                unknown.append(preserve_unknown_nal(nal, unknown_output))
            else:
                unknown.append({**nal.summary(), "raw_hex": nal.raw.hex()})

    inventory, pps_links = _parameter_set_inventory(parameter_sets)
    type_counts = Counter(nal.nal_type for nal in nals)
    byte_counts: dict[int, int] = defaultdict(int)
    first_offsets: dict[int, int] = {}
    for nal in nals:
        byte_counts[nal.nal_type] += len(nal.raw)
        first_offsets.setdefault(nal.nal_type, nal.start_offset)

    unknown_stats = []
    for nal_type in sorted({item["nal_type"] for item in unknown}):
        items = [item for item in unknown if item["nal_type"] == nal_type]
        unknown_stats.append(
            {
                "nal_type": nal_type,
                "type_name": items[0]["type_name"],
                "count": len(items),
                "total_bytes": sum(item["size"] for item in items),
                "first_start_offset": items[0]["start_offset"],
                "sha256": [item["sha256"] for item in items],
            }
        )

    return {
        "stream_size": len(data),
        "nal_count": len(nals),
        "nal_type_distribution": [
            {
                "nal_type": nal_type,
                "type_name": next(
                    nal.type_name for nal in nals if nal.nal_type == nal_type
                ),
                "count": count,
                "total_bytes": byte_counts[nal_type],
                "first_start_offset": first_offsets[nal_type],
            }
            for nal_type, count in sorted(type_counts.items())
        ],
        "nals": [nal.summary() for nal in nals],
        "parameter_sets": inventory,
        "pps_links": pps_links,
        "sps_events": _sps_events(parameter_sets),
        "unknown_nal_distribution": unknown_stats,
        "unknown_nals": unknown,
        "parse_errors": parse_errors,
    }


def analyze_file(
    path: str | Path, unknown_dir: str | Path | None = None
) -> dict[str, Any]:
    return analyze_stream(Path(path).read_bytes(), unknown_dir=unknown_dir)


def write_json(report: dict[str, Any], path: str | Path) -> None:
    Path(path).write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
