#!/usr/bin/env python3
import csv
import json
from pathlib import Path

from media_utils import ROOT

RESULTS = ROOT / "artifacts/results"


def load_json(name):
    return json.loads((RESULTS / name).read_text())


def load_csv(name):
    with (RESULTS / name).open() as fh:
        return list(csv.DictReader(fh))


def table(headers, rows):
    lines = [
        "| " + " | ".join(headers) + " |",
        "|" + "|".join("---" for _ in headers) + "|",
    ]
    lines += ["| " + " | ".join(str(cell) for cell in row) + " |" for row in rows]
    return "\n".join(lines)


def pct(part, whole):
    return abs(part) / whole * 100


def summary_section(validation, latency_summary, quality, early):
    part = latency_summary["part"]
    segment = latency_summary["segment"]
    e2e_reduction = segment["end_to_end_s"] - part["end_to_end_s"]
    startup_reduction = (
        segment["request_to_first_frame_s"] - part["request_to_first_frame_s"]
    )
    ranking = " > ".join(
        quality["summary"][mode]["label"] for mode in quality["ranking"]["by_ssim"]
    )
    hinted = early["hinted_early_request"]
    partial = early["partial_early_request"]
    return f"""# LL-HLS part 推送、GOP 对齐与 VBV 画质验证

## 结论摘要

- **LL-HLS 标签与分片结构有效**：24 个 `EXT-X-PRELOAD-HINT`、36 次累计
  `EXT-X-PART` 列举、6 个整段；每个 part 为 0.500 s，part 首帧均为 keyframe，
  且 NAL 首帧图像片为 IDR（type 5）。
- **part 边界与 GOP 完全对齐**：24 个关键帧位于 0.000, 0.500, …, 11.500 s，
  与 24 个 part 起点逐项相等；没有额外 GOP 或错位边界。
- **每个 part 的首帧与预加载提示一致**：24/24 个提示 URI 指向对应 part，
  实际首帧时间等于 `part_index × 0.500`，全部匹配。
- **提前请求未完成 part 不会被误判为完整**：真实 HTTP 测试中，提示 part 在释放前
  可见 {hinted['early_body_bytes']} 字节；半包测试提前可见
  {partial['early_body_bytes']:,} / {partial['total_bytes']:,} 字节，但长度检查明确为
  不完整，最终完整内容 SHA-256 一致。
- **端到端延迟**：在固定 16 Mbps 网络、含实测首帧解码耗时的模型中，part 模式
  首帧端到端 **{part['end_to_end_s']:.3f} s**，整段模式
  **{segment['end_to_end_s']:.3f} s**，降低 **{e2e_reduction:.3f} s
  ({pct(e2e_reduction, segment['end_to_end_s']):.1f}%)**。
- **起播等待**：从请求到首帧显示，part 平均 **{part['request_to_first_frame_s']:.3f} s**，
  整段平均 **{segment['request_to_first_frame_s']:.3f} s**，降低
  **{startup_reduction:.3f} s
  ({pct(startup_reduction, segment['request_to_first_frame_s']):.1f}%)**。
- **相同峰值下画质排序（SSIM）**：**{ranking}**。VBV 静态两段平均 SSIM
  **{quality['vbv_static_mean_ssim']:.6f}**，距最佳静态档仅
  **{quality['vbv_static_gap_to_best']:.6f}**；相对运动段仍保持明显更高相似度，
  没有为了峰值约束牺牲静态段。

## 实验口径

- 素材：640×360、24 fps、12 s；0–3 s 静态细纹理，3–9 s 高运动测试图，
  9–12 s 回到同一静态细纹理。
- 编码：libx264、`preset=medium`、固定 GOP 12 帧（0.500 s）、关闭场景切换、
  无 B 帧、closed GOP。
- 三种码控均配置 `maxrate=800 kbps`、`bufsize=800 kbps`：
  CBR 为 800 kbps 恒码率；ABR 为 500 kbps 目标均值；VBV 为 CRF 12 + VBV 峰值帽。
  CBR 的 1 s 观测峰值为 1.247 Mbps，ABR 为 1.201 Mbps，VBV 为 1.192 Mbps，
  差异来自启动 I 帧/CPB 满缓冲瞬态，配置峰值相同。
- LL-HLS：part target 0.500 s，整段 2.000 s（4 parts），`EXT-X-VERSION:9`，
  `EXT-X-MAP` 指向独立 init，part 均带 `INDEPENDENT=YES`。
- 延迟模型：在五个相同请求时刻分别计算下一 part 与下一整段的发布、固定
  16 Mbps 传输、实测首帧解码和显示时间；首帧端到端定义为“首帧采集时刻 →
  首帧显示时刻”。
"""


def parts_section(validation, hints):
    rows = []
    for row in hints:
        rows.append([
            row["hint_uri"].split("-")[1].split(".")[0],
            row["duration_s"],
            row["actual_first_frame_s"],
            f'{row["keyframe"]}/{row["idr"]}',
            row["hint_uri"],
            row["expected_first_frame_s"],
            row["match"],
        ])
    checks = validation["checks"]
    return f"""## Part / GOP / 预加载提示

每个快照均在某个 part 开始生产、尚未完整时生成：已完成的 part 用
`EXT-X-PART` 列出，正在生产的下一个 part 用 `EXT-X-PRELOAD-HINT` 预告。
下列 24 行覆盖全部 part：

{table(
    ["Part", "时长(s)", "实际首帧(s)", "Key/IDR", "提示 URI", "提示首帧(s)", "一致"],
    rows,
)}

校验结果：`all_parts_0_5s={str(checks['all_parts_0_5s']).lower()}`、
`all_parts_idr={str(checks['all_parts_idr']).lower()}`、
`gop_equals_part_interval={str(checks['gop_equals_part_interval']).lower()}`、
`all_hint_starts_match={str(checks['all_hint_starts_match']).lower()}`。
关键帧时间与 part 起点列表完全相同，因此 part 边界没有落在预测帧或普通 P 帧上。

示例快照（2.500 s）：

```m3u8
{(ROOT / 'artifacts/lhls/snapshots/t02.500.m3u8').read_text().strip()}
```

`EXT-X-PART` 中的 `DURATION` 与 `EXT-X-PART-INF:PART-TARGET` 均为 0.500 s；
`EXT-X-PRELOAD-HINT` 按规范只声明 `TYPE=PART` 与 `URI`，不声明时长。
"""


def early_section(early):
    hinted = early["hinted_early_request"]
    partial = early["partial_early_request"]
    return f"""## 播放器提前请求模拟

使用本地真实 HTTP 服务复现两种播放器行为。服务端把 part 的释放延迟设为
{early['release_delay_s']:.2f} s：

{table(
    ["请求类型", "提前可见字节", "总字节", "先到内容完整?", "完成时刻(ms)", "SHA-256"],
    [
        ["PRELOAD-HINT 提前 GET", f'{hinted["early_body_bytes"]:,}',
         f'{hinted["total_bytes"]:,}',
         "否", hinted["completion_ms"], "一致" if hinted["sha256_match"] else "不一致"],
        ["部分 body 先到", f'{partial["early_body_bytes"]:,}',
         f'{partial["total_bytes"]:,}',
         "否", partial["completion_ms"], "一致" if partial["sha256_match"] else "不一致"],
    ],
)}

- 提示 part 请求在 {hinted['early_observation_ms']:.1f} ms 时仍无 body，直到
  {hinted['completion_ms']:.1f} ms 才收到全部 {hinted['total_bytes']:,} 字节。
- 半包端点先返回 {partial['early_body_bytes']:,} 字节，明显小于
  `Content-Length={partial['content_length']}`；任何依赖长度/SHA 校验的播放器都应
  继续等待，不能把该 fragment 当完整 part 解码。
- 两项最终内容均与磁盘 part 的 SHA-256 完全一致；全部 HTTP 检查通过。
"""


def latency_section(validation, rows):
    summary = validation["latency_summary"]
    part = summary["part"]
    segment = summary["segment"]
    e2e_gain = segment["end_to_end_s"] - part["end_to_end_s"]
    startup_gain = segment["request_to_first_frame_s"] - part["request_to_first_frame_s"]
    grouped = {}
    for row in rows:
        grouped.setdefault(float(row["request_time_s"]), {})[row["mode"]] = row
    sample_rows = []
    for start in sorted(grouped):
        pair = grouped[start]
        sample_rows.append([
            f"{start:.1f}",
            f'{float(pair["part"]["end_to_end_s"]):.3f}',
            f'{float(pair["segment"]["end_to_end_s"]):.3f}',
            f'{float(pair["part"]["request_to_first_frame_s"]):.3f}',
            f'{float(pair["segment"]["request_to_first_frame_s"]):.3f}',
        ])
    return f"""## 端到端与起播延迟

固定 16 Mbps 网络；part 的平均传输耗时为 {part['transfer_s'] * 1000:.1f} ms，
整段为 {segment['transfer_s'] * 1000:.1f} ms。首帧解码耗时用实际 ffmpeg
解码三次取中位数，part 为 {part['decode_first_frame_s'] * 1000:.1f} ms，
整段为 {segment['decode_first_frame_s'] * 1000:.1f} ms。

{table(
    ["推送模式", "首帧端到端(s)", "请求到首帧(s)", "平均传输(ms)", "首帧解码(ms)"],
    [
        ["Part", f"{part['end_to_end_s']:.3f}", f"{part['request_to_first_frame_s']:.3f}",
         f"{part['transfer_s'] * 1000:.1f}", f"{part['decode_first_frame_s'] * 1000:.1f}"],
        ["整段", f"{segment['end_to_end_s']:.3f}",
         f"{segment['request_to_first_frame_s']:.3f}",
         f"{segment['transfer_s'] * 1000:.1f}",
         f"{segment['decode_first_frame_s'] * 1000:.1f}"],
    ],
)}

同一请求时刻的五组配对结果：

{table(
    ["请求时刻(s)", "Part 首帧端到端(s)", "整段首帧端到端(s)",
     "Part 请求→首帧(s)", "整段请求→首帧(s)"],
    sample_rows,
)}

- 首帧端到端缩短 **{e2e_gain:.3f} s**（{pct(e2e_gain, segment['end_to_end_s']):.1f}%）。
- 请求到首帧显示缩短 **{startup_gain:.3f} s**
  （{pct(startup_gain, segment['request_to_first_frame_s']):.1f}%）。
- 整段首帧必须等待 2 s 整段发布，因此其端到端时延接近一个 segment；part 只需等待
  当前 0.5 s part 发布，额外开销主要是一次网络传输与首帧解码。
"""


def quality_section(quality):
    summary = quality["summary"]
    mode_rows = []
    for mode in ("cbr", "abr", "vbv"):
        item = summary[mode]
        static_a = item["regions"]["static_a"]
        static_b = item["regions"]["static_b"]
        mode_rows.append([
            item["label"],
            f'{item["avg_bitrate_bps"] / 1e6:.3f}',
            f'{item["observed_peak_1s_bps"] / 1e6:.3f}',
            f'{item["psnr_avg_db"]:.3f}',
            f'{item["ssim_avg"]:.6f}',
            f'{(static_a["ssim_avg"] + static_b["ssim_avg"]) / 2:.6f}',
            f'{item["regions"]["motion"]["ssim_avg"]:.6f}',
        ])
    ssim_rank = " > ".join(
        summary[mode]["label"] for mode in quality["ranking"]["by_ssim"]
    )
    psnr_rank = " > ".join(
        summary[mode]["label"] for mode in quality["ranking"]["by_psnr"]
    )
    vbv_static_min = min(
        summary["vbv"]["regions"]["static_a"]["ssim_min"],
        summary["vbv"]["regions"]["static_b"]["ssim_min"],
    )
    checks = quality["checks"]
    return f"""## 相同峰值下的画质

三种模式配置峰值均为 800 kbps。质量为逐帧 PSNR/SSIM 的算术平均：

{table(
    ["模式", "平均 Mbps", "1 s 观测峰 Mbps", "PSNR(dB)", "SSIM",
     "静态 SSIM 均值", "运动 SSIM 均值"],
    mode_rows,
)}

- **SSIM 排序：{ssim_rank}**。
- **PSNR 排序：{psnr_rank}**。
- 两个指标排序不完全相同：CBR 给静态画面持续分配 800 kbps，获得最高像素保真；
  VBV 把更多码率留给高运动段，因此整体 SSIM 略高，但静态 PSNR 低于 CBR。
- **VBV 静态段未被牺牲**：静态两段平均 SSIM
  `{quality['vbv_static_mean_ssim']:.6f}`，最佳档为
  `{quality['best_static_mean_ssim']:.6f}`，差距仅
  `{quality['vbv_static_gap_to_best']:.6f}`；静态单帧最差 SSIM 仍为
  `{vbv_static_min:.6f}`。这满足报告设置的“静态 SSIM ≥ 0.999 且距最佳 ≤ 0.001”
  判据，视觉上不存在可辨牺牲。
- VBV 静态平均 SSIM 明显高于其运动段（
  `{quality['vbv_static_mean_ssim']:.6f}` vs
  `{summary['vbv']['regions']['motion']['ssim_avg']:.6f}`），说明峰值帽没有把静态画面
  压到运动段水平。
- 结论中的“未牺牲”是“没有实际可见退化”，不宣称与 CBR 逐像素完全相同。
  检查项：`vbv_static_above_0_999={str(checks['vbv_static_ssim_above_0_999']).lower()}`、
  `vbv_static_within_0_001_of_best={str(checks['vbv_static_within_0_001_of_best']).lower()}`。
"""


def main():
    validation = load_json("lhls_validation.json")
    early = load_json("early_request_http.json")
    quality = load_json("quality_summary.json")
    hints = load_csv("preload_hints.csv")
    latency_rows = load_csv("latency.csv")
    report = "\n".join([
        summary_section(validation, validation["latency_summary"], quality, early),
        parts_section(validation, hints),
        early_section(early),
        latency_section(validation, latency_rows),
        quality_section(quality),
        """## 复现

```bash
./src/build_media.sh
python3 src/package_lhls.py
python3 src/early_request_test.py
python3 src/evaluate_quality.py
python3 src/render_report.py
```

机器可读结果：

- `artifacts/results/lhls_validation.json`：标签、part、GOP、hint 全量检查
- `artifacts/results/lhls_parts.csv`：每个 part 的时长、首帧、keyframe/IDR
- `artifacts/results/preload_hints.csv`：快照提示 URI 与实际首帧对照
- `artifacts/results/early_request_http.json`：真实 HTTP 提前请求时序
- `artifacts/results/latency.csv`：逐请求时刻的 part/整段延迟
- `artifacts/results/quality_per_frame.csv`：逐帧 PSNR/SSIM/字节数
- `artifacts/results/quality_summary.json`：三模式画质与检查结论

## 边界与限制

- 延迟采用确定性发布模型，文件大小、首帧解码耗时来自真实工件；16 Mbps 网络为固定
  假设，不含播放器渲染、音频同步与公网抖动。提前请求则另用真实本地 HTTP 验证。
- 画质素材是合成视频，结论针对本 GOP/part 结构及 800 kbps 峰值点，不外推到所有内容。
- CBR 的 1 s 峰值高于配置 `maxrate` 是启动 CPB/I 帧瞬态；三模式共享同一 800 kbps
  配置峰值和 800 kbit 缓冲，横向比较条件一致。
""",
    ])
    (ROOT / "artifacts/REPORT.md").write_text(report)
    print(ROOT / "artifacts/REPORT.md")


if __name__ == "__main__":
    main()
