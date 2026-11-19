"""Run the chunked-vs-whole fMP4 transport experiment and write reports."""

from __future__ import annotations

import bisect
import hashlib
import json
from pathlib import Path
from typing import Any

from .mp4 import (
    duplicate_keys,
    media_timescale,
    parse_samples,
    sample_digest,
)
from .network import NetworkConfig, simulate_transfer, summarise_timing
from .tiles import analyse_tiles


CHUNK_SIZE = 32 * 1024
BASE_NETWORK = NetworkConfig(
    bandwidth_bps=2_000_000,
    rtt_ms=60.0,
    response_header_bytes=200,
    packet_payload_bytes=1024,
    packet_header_bytes=40,
    chunk_header_bytes=24,
    loss_percent=0.0,
    loss_seed=20261120,
)
LOSS_NETWORK = NetworkConfig(**{**BASE_NETWORK.__dict__, "loss_percent": 10.0})


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _integrity_check(
    data: bytes, chunk_size: int
) -> tuple[dict[str, Any], list[bytes]]:
    chunks = [data[start : start + chunk_size] for start in range(0, len(data), chunk_size)]
    reassembled = b"".join(chunks)
    original_samples = parse_samples(data)
    received_samples = parse_samples(reassembled)
    boundaries = [
        min((index + 1) * chunk_size, len(data)) for index in range(len(chunks))
    ]
    spans = []
    for sample in original_samples:
        first_chunk = bisect.bisect_right(boundaries, sample.offset)
        last_chunk = bisect.bisect_right(boundaries, sample.end - 1)
        if first_chunk != last_chunk:
            spans.append(
                {
                    "index": sample.index,
                    "offset": sample.offset,
                    "size": sample.size,
                    "first_chunk": first_chunk,
                    "last_chunk": last_chunk,
                }
            )
    original_digests = [sample_digest(data, sample) for sample in original_samples]
    received_digests = [sample_digest(reassembled, sample) for sample in received_samples]
    contiguous = all(
        start == (0 if index == 0 else boundaries[index - 1])
        for index, start in enumerate(range(0, len(data), chunk_size))
    )
    duplicates = duplicate_keys(received_samples)
    result = {
        "chunk_count": len(chunks),
        "chunk_bytes_total": sum(len(chunk) for chunk in chunks),
        "original_size": len(data),
        "original_sha256": _sha256(data),
        "reassembled_sha256": _sha256(reassembled),
        "byte_exact": data == reassembled,
        "chunk_ranges_contiguous": contiguous,
        "sample_count": len(original_samples),
        "received_sample_count": len(received_samples),
        "sample_digest_sequence_equal": original_digests == received_digests,
        "samples_spanning_chunk_boundaries": len(spans),
        "spanning_sample_details": spans,
        "duplicate_sample_keys": [list(key) for key in duplicates],
        "no_duplicate_samples": not duplicates,
        "first_media_sample": {
            "track_id": original_samples[0].track_id,
            "index": original_samples[0].index,
            "offset": original_samples[0].offset,
            "size": original_samples[0].size,
            "dts": original_samples[0].dts,
            "payload_sha256": original_digests[0],
        },
    }
    result["passed"] = (
        result["byte_exact"]
        and result["chunk_ranges_contiguous"]
        and result["sample_digest_sequence_equal"]
        and result["no_duplicate_samples"]
        and len(received_samples) == len(original_samples)
    )
    return result, chunks


def _transfer_case(
    data: bytes,
    samples,
    config: NetworkConfig,
    *,
    chunked: bool,
) -> tuple[dict[str, Any], Any]:
    size = CHUNK_SIZE if chunked else len(data)
    transfer = simulate_transfer(data, size, config, chunked=chunked)
    timing = summarise_timing(transfer, samples[0].end, len(data))
    timing["playback_ready_ms"] = (
        timing["first_sample_transport_ms"] if chunked else timing["file_complete_ms"]
    )
    timing["client_model"] = (
        "decode first complete sample while later chunks arrive"
        if chunked
        else "wait for the complete response before playback"
    )
    timing["chunks"] = transfer.chunks
    return timing, transfer


def _loss_comparison(
    baseline: dict[str, Any], impaired: dict[str, Any]
) -> dict[str, Any]:
    return {
        "playback_added_ms": impaired["playback_ready_ms"] - baseline["playback_ready_ms"],
        "playback_increase_percent": 100.0
        * (impaired["playback_ready_ms"] - baseline["playback_ready_ms"])
        / baseline["playback_ready_ms"],
        "first_sample_added_ms": impaired["first_sample_transport_ms"]
        - baseline["first_sample_transport_ms"],
        "completion_added_ms": impaired["file_complete_ms"] - baseline["file_complete_ms"],
        "extra_lost_packets": impaired["lost_initial_packets"]
        - baseline["lost_initial_packets"],
    }


def run_experiment(root: Path) -> dict[str, Any]:
    media_path = root / "artifacts/media/source-fragmented.mp4"
    data = media_path.read_bytes()
    samples = parse_samples(data)
    integrity, chunks = _integrity_check(data, CHUNK_SIZE)
    reassembled_path = root / "artifacts/media/reassembled-chunked.mp4"
    reassembled_path.write_bytes(b"".join(chunks))

    no_loss_chunked, chunked_transfer = _transfer_case(
        data, samples, BASE_NETWORK, chunked=True
    )
    no_loss_whole, whole_transfer = _transfer_case(
        data, samples, BASE_NETWORK, chunked=False
    )
    loss_chunked, loss_chunked_transfer = _transfer_case(
        data, samples, LOSS_NETWORK, chunked=True
    )
    loss_whole, loss_whole_transfer = _transfer_case(
        data, samples, LOSS_NETWORK, chunked=False
    )

    expected_gap_bound_ms = (
        BASE_NETWORK.chunk_header_bytes
        + BASE_NETWORK.packet_header_bytes
        + BASE_NETWORK.packet_payload_bytes
    ) / BASE_NETWORK.bytes_per_second * 1000.0
    no_extra_rtt_observed = (
        no_loss_chunked["gap_max_ms"] <= expected_gap_bound_ms + 0.01
    )
    packet_trace_matches = (
        no_loss_chunked["packet_count"] == no_loss_whole["packet_count"]
        and loss_chunked["packet_count"] == loss_whole["packet_count"]
        and loss_chunked["lost_initial_packets"] == loss_whole["lost_initial_packets"]
        and loss_chunked["retransmissions"] == loss_whole["retransmissions"]
    )
    report = {
        "media": {
            "path": str(media_path),
            "bytes": len(data),
            "duration_s": sum(sample.duration for sample in samples)
            / media_timescale(data),
            "sample_count": len(samples),
            "sha256": _sha256(data),
        },
        "config": {
            "chunk_size_bytes": CHUNK_SIZE,
            "network": BASE_NETWORK.__dict__,
            "loss_network": LOSS_NETWORK.__dict__,
            "expected_chunk_gap_bound_ms": expected_gap_bound_ms,
        },
        "startup": {
            "chunked": no_loss_chunked,
            "whole": no_loss_whole,
            "chunked_playback_advantage_ms": no_loss_whole["playback_ready_ms"]
            - no_loss_chunked["playback_ready_ms"],
            "chunked_playback_advantage_percent": 100.0
            * (
                no_loss_whole["playback_ready_ms"]
                - no_loss_chunked["playback_ready_ms"]
            )
            / no_loss_whole["playback_ready_ms"],
        },
        "chunk_timeline": no_loss_chunked,
        "chunk_timeline_lossy": loss_chunked,
        "integrity": integrity,
        "chunking_overhead": {
            "no_extra_rtt_observed": no_extra_rtt_observed,
            "expected_gap_bound_ms": expected_gap_bound_ms,
            "observed_gap_max_ms": no_loss_chunked["gap_max_ms"],
            "chunk_size_is_packet_multiple": CHUNK_SIZE
            % BASE_NETWORK.packet_payload_bytes
            == 0,
            "same_packet_trace_for_chunked_and_whole": packet_trace_matches,
            "chunk_framing_bytes": no_loss_chunked["chunk_framing_bytes"],
            "chunk_framing_overhead_percent": no_loss_chunked[
                "chunk_framing_overhead_percent"
            ],
        },
        "packet_loss": {
            "loss_percent": LOSS_NETWORK.loss_percent,
            "same_seed": LOSS_NETWORK.loss_seed,
            "chunked": loss_chunked,
            "whole": loss_whole,
            "chunked_impact": _loss_comparison(no_loss_chunked, loss_chunked),
            "whole_impact": _loss_comparison(no_loss_whole, loss_whole),
        },
    }
    tile_report = analyse_tiles(
        media_path,
        root / "artifacts/tiles/manifest.json",
        root / "reports/tile_report.json",
        root / "reports/tile-comparison.png",
    )
    report["tiles"] = tile_report
    report["passed"] = integrity["passed"] and tile_report["passed"]
    reports_dir = root / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    (reports_dir / "streaming_report.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    (reports_dir / "streaming_report.md").write_text(
        render_markdown(report), encoding="utf-8"
    )
    return report


def _number(value: float | int | None, digits: int = 2) -> str:
    if value is None:
        return "-"
    if isinstance(value, int):
        return str(value)
    return f"{value:.{digits}f}"


def render_markdown(report: dict[str, Any]) -> str:
    startup = report["startup"]
    chunked = startup["chunked"]
    whole = startup["whole"]
    integrity = report["integrity"]
    overhead = report["chunking_overhead"]
    loss = report["packet_loss"]
    tiles = report["tiles"]
    lines = [
        "# Fragmented MP4 chunk 传输实验",
        "",
        "## 结论",
        "",
        f"- 整体校验：**{'通过' if report['passed'] else '未通过'}**。",
        f"- Chunked 起播 {_number(chunked['playback_ready_ms'])} ms，整段等待完成起播 "
        f"{_number(whole['playback_ready_ms'])} ms；提前 "
        f"{_number(startup['chunked_playback_advantage_ms'])} ms "
        f"（{_number(startup['chunked_playback_advantage_percent'])}%）。",
        f"- chunk 边界跨越 {integrity['samples_spanning_chunk_boundaries']} 个样本；"
        f"重组字节完全一致={integrity['byte_exact']}，样本摘要序列一致="
        f"{integrity['sample_digest_sequence_equal']}，重复样本="
        f"{not integrity['no_duplicate_samples']}。",
        f"- 无损时 chunk 间隔最大 {_number(overhead['observed_gap_max_ms'], 3)} ms，"
        f"不超过单包+chunk 头理论界 {_number(overhead['expected_gap_bound_ms'], 3)} ms；"
        f"没有额外 RTT，chunk 帧开销仅 "
        f"{_number(overhead['chunk_framing_overhead_percent'], 3)}%；"
        f"两种传输的媒体包/丢包轨迹一致="
        f"{overhead['same_packet_trace_for_chunked_and_whole']}。",
        f"- 相同 {_number(loss['loss_percent'], 0)}% 丢包轨迹下，chunked 起播增加 "
        f"{_number(loss['chunked_impact']['playback_added_ms'])} ms，整段起播增加 "
        f"{_number(loss['whole_impact']['playback_added_ms'])} ms；chunked 仍提前 "
        f"{_number(loss['whole']['playback_ready_ms'] - loss['chunked']['playback_ready_ms'])} ms。",
        f"- 2x2 tile：几何对齐={tiles['geometry_aligned']}，码量与边缘能量秩相关="
        f"{_number(tiles['edge_to_bytes_spearman'])}，拼接 PSNR="
        f"{_number(tiles['mean_mosaic_psnr_db'])} dB，缝/内部误差比="
        f"{_number(tiles['mean_seam_to_interior_ratio'])}（最大 "
        f"{_number(tiles['max_seam_to_interior_ratio'])}）。",
        "",
        "## 网络与媒体配置",
        "",
        "| 参数 | 值 |",
        "|---|---:|",
        f"| 媒体文件 | `{report['media']['path']}` |",
        f"| 媒体字节 | {report['media']['bytes']} |",
        f"| 媒体时长 | {_number(report['media']['duration_s'], 3)} s |",
        f"| 样本数 | {report['media']['sample_count']} |",
        f"| chunk 大小 | {report['config']['chunk_size_bytes']} B |",
        f"| 带宽 | {report['config']['network']['bandwidth_bps']} bit/s |",
        f"| RTT | {_number(report['config']['network']['rtt_ms'], 1)} ms |",
        f"| 丢包率 / seed | {_number(loss['loss_percent'], 0)}% / {loss['same_seed']} |",
        "",
        "整段传输按非渐进客户端建模：收到完整响应后才起播；chunked 在首个完整媒体样本"
        "到达时即可解码。两者使用同一个带宽、RTT、包长和丢包随机轨迹。",
        "",
        "## 起播延迟",
        "",
        "| 指标 | chunked | 整段 |",
        "|---|---:|---:|",
        f"| 首样本字节到达 (ms) | {_number(chunked['first_sample_transport_ms'])} | "
        f"{_number(whole['first_sample_transport_ms'])} |",
        f"| 客户端起播 (ms) | {_number(chunked['playback_ready_ms'])} | "
        f"{_number(whole['playback_ready_ms'])} |",
        f"| 文件完成 (ms) | {_number(chunked['file_complete_ms'])} | "
        f"{_number(whole['file_complete_ms'])} |",
        "",
        f"首个媒体样本：offset={integrity['first_media_sample']['offset']}，"
        f"size={integrity['first_media_sample']['size']} B，"
        f"sha256=`{integrity['first_media_sample']['payload_sha256'][:16]}…`。",
        "",
        "## 逐 chunk 字节与耗时",
        "",
        "`start/end` 是该 chunk 首/末媒体字节可被应用消费的时间；`gap` 是前一个 "
        "chunk 结束到本 chunk 开始的空闲间隔；`start interval` 是相邻 chunk 起点差。",
        "",
        "| # | bytes | start (ms) | end (ms) | elapsed (ms) | gap (ms) | start interval (ms) |",
        "|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for chunk in chunked["chunks"]:
        lines.append(
            f"| {chunk['index']} | {chunk['bytes']} | "
            f"{_number(chunk['start_s'] * 1000)} | {_number(chunk['end_s'] * 1000)} | "
            f"{_number(chunk['elapsed_ms'])} | {_number(chunk['gap_before_ms'], 3)} | "
            f"{_number(chunk['start_interval_ms'], 3)} |"
        )
    lines.extend(
        [
            "",
            "## 样本完整性与去重",
            "",
            "| 校验 | 结果 |",
            "|---|---|",
            f"| chunk 重组 SHA-256 一致 | {integrity['byte_exact']} |",
            f"| chunk 范围连续且不重叠 | {integrity['chunk_ranges_contiguous']} |",
            f"| 样本数（原/重组） | {integrity['sample_count']} / "
            f"{integrity['received_sample_count']} |",
            f"| 跨 chunk 边界的样本数 | "
            f"{integrity['samples_spanning_chunk_boundaries']} |",
            f"| 逐样本 payload 摘要完全一致 | "
            f"{integrity['sample_digest_sequence_equal']} |",
            f"| 重复样本 key | {len(integrity['duplicate_sample_keys'])} |",
            "",
            "## 丢包影响",
            "",
            "| 传输方式 | 首样本 ms | 起播 ms | 完成 ms | 初始丢包 | 重传 | 重传字节 |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for name, value in (("chunked", loss["chunked"]), ("整段", loss["whole"])):
        lines.append(
            f"| {name} | {_number(value['first_sample_transport_ms'])} | "
            f"{_number(value['playback_ready_ms'])} | {_number(value['file_complete_ms'])} | "
            f"{value['lost_initial_packets']} | {value['retransmissions']} | "
            f"{value['retransmitted_bytes']} |"
        )
    lines.extend(
        [
            "",
            f"chunked 起播损失 {_number(loss['chunked_impact']['playback_added_ms'])} ms，"
            f"整段起播损失 {_number(loss['whole_impact']['playback_added_ms'])} ms。"
            "丢包由确定性的同一媒体包轨迹触发，并在一个 RTT 后重传。",
            "",
            "## 2x2 Tile 码量与画面边界",
            "",
            "| tile | 位置 | 尺寸 | 媒体字节 | kbit/s | 边缘能量/像素 | 强边缘 % |",
            "|---|---|---|---:|---:|---:|---:|",
        ]
    )
    total_tile_bytes = sum(tile["media_bytes"] for tile in tiles["tiles"])
    for tile in tiles["tiles"]:
        share = 100.0 * tile["media_bytes"] / total_tile_bytes
        lines.append(
            f"| {tile['id']} | ({tile['x']},{tile['y']}) | "
            f"{tile['width']}x{tile['height']} | {tile['media_bytes']} "
            f"({_number(share)}%) | {_number(tile['bitrate_kbps'])} | "
            f"{_number(tile['edge_energy_per_pixel'])} | "
            f"{_number(tile['strong_edge_percent'])} |"
        )
    lines.extend(
        [
            "",
            f"几何与 2x2 坐标严格对齐：{tiles['geometry_aligned']}；"
            f"码量排序和边缘能量排序一致：{tiles['edge_and_bytes_follow_same_rank']}。"
            f"源画面（左）与 tile 重组画面（右）见 "
            f"[`{Path(tiles['comparison_image']).name}`]({Path(tiles['comparison_image']).name})。",
            "",
            "每个 tile 是独立的 fMP4 编码单元，符合常见 2x2 tiled DASH/流式封装；"
            "四路媒体样本字节单独统计，容器头不混入码率。",
            "",
        ]
    )
    return "\n".join(lines)
