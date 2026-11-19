#!/usr/bin/env python3
"""Render the measured results into REPORT.md."""

from __future__ import annotations

import json
from pathlib import Path

from common import PRIMARY_KBPS, RESULTS, ROOT


def table(headers: list[str], rows: list[list[str]]) -> str:
    lines = [
        "| " + " | ".join(headers) + " |",
        "|" + "|".join(["---"] * len(headers)) + "|",
    ]
    lines.extend("| " + " | ".join(row) + " |" for row in rows)
    return "\n".join(lines)


def cell_map(results: dict[str, object]) -> dict[tuple[str, str, int], dict[str, object]]:
    return {
        (cell["encoder"], cell["clip"], int(cell["desired_kbps"])): cell
        for cell in results["cells"]
    }


def pct(value: float) -> str:
    return f"{value * 100:.2f}%"


def main() -> None:
    results = json.loads((RESULTS / "results.json").read_text())
    comparisons = json.loads((RESULTS / "comparisons.json").read_text())
    reproducibility = json.loads((RESULTS / "reproducibility.json").read_text())
    partition = json.loads((RESULTS / "partition_impulse.json").read_text())
    cells = cell_map(results)

    primary_rows = []
    for clip in ("static", "motion"):
        for encoder in ("x264", "x265"):
            cell = cells[(encoder, clip, PRIMARY_KBPS)]
            primary_rows.append(
                [
                    clip,
                    encoder,
                    f"{cell['achieved_kbps']:.2f}",
                    f"{cell['ssim']['ssim_all']:.5f}",
                    f"{cell['ssim']['ssim_y']:.5f}",
                    f"{cell['high_frequency']['hf_energy_ratio']:.4f}",
                    f"{cell['high_frequency']['hf_correlation']:.4f}",
                    f"{cell['high_frequency']['relative_hf_error']:.4f}",
                    str(cell["buffer"]["violations"]),
                    f"{cell['elapsed_s']:.3f}",
                    f"{cell['cpu_s']:.3f}",
                ]
            )

    sweep_rows = []
    for desired in results["config"]["sweep_kbps"]:
        for encoder in ("x264", "x265"):
            for clip in ("static", "motion"):
                cell = cells[(encoder, clip, desired)]
                sweep_rows.append(
                    [
                        str(desired),
                        clip,
                        encoder,
                        f"{cell['achieved_kbps']:.2f}",
                        f"{cell['ssim']['ssim_all']:.5f}",
                        f"{cell['elapsed_s']:.3f}",
                        "yes" if cell["converged"] else "no",
                    ]
                )

    allocation_rows = []
    for clip in ("static", "motion"):
        for encoder in ("x264", "x265"):
            allocation = cells[(encoder, clip, PRIMARY_KBPS)]["allocation"]
            types = allocation["by_frame_type"]
            allocation_rows.append(
                [
                    clip,
                    encoder,
                    pct(types.get("I", {}).get("fraction", 0)),
                    pct(types.get("P", {}).get("fraction", 0)),
                    pct(types.get("B", {}).get("fraction", 0)),
                    f"{allocation['coefficient_of_variation']:.3f}",
                    f"{allocation['p95_over_mean']:.3f}",
                    f"{allocation['max_over_mean']:.3f}",
                ]
            )

    repro_rows = []
    for key in sorted(reproducibility):
        group = reproducibility[key]
        repro_rows.append(
            [
                key,
                "yes" if group["bitstream_identical"] else "no",
                "yes" if group["decoded_identical"] else "no",
                f"{group['ssim_range']:.1e}",
                f"{group['hf_range']:.1e}",
                f"{group['time_mean_s']:.3f} ± {group['time_std_s']:.3f}",
                pct(group["time_cv"]),
                pct(group["cpu_time_cv"]),
            ]
        )

    partition_rows = []
    for name, test in partition["tests"].items():
        partition_rows.append(
            [
                name,
                " / ".join(test["partitions"]),
                str(test["a_vs_b"]["max_abs_diff_lsb"]),
                f"{test['a_vs_b']['mean_abs_diff_lsb']:.6f}",
                str(test["a_vs_b"]["psnr_db"]),
                "yes" if test["within_tolerance"] else "no",
            ]
        )

    static_cmp = comparisons["clips"]["static"]
    motion_cmp = comparisons["clips"]["motion"]
    report = f"""# x264 与 x265 等码率对比实验

## 结论摘要

- 主实验点为约 **{PRIMARY_KBPS} kbps**。通过逐编码器闭环搜索，四组实际码率为
  x264 静态 {static_cmp['x264_kbps']:.2f}、x265 静态 {static_cmp['x265_kbps']:.2f}、
  x264 高运动 {motion_cmp['x264_kbps']:.2f}、x265 高运动 {motion_cmp['x265_kbps']:.2f} kbps；
  同一素材内最大码率差 **{comparisons['max_rate_difference_pct']:.3f}%**，满足 ±1% 判据。
- 在相同实际码率下，x265 的 SSIM 明显更高：静态提高
  **{static_cmp['ssim_all']['delta_x265_minus_x264']:.4f}**（{static_cmp['ssim_all']['x264']:.5f} →
  {static_cmp['ssim_all']['x265']:.5f}），高运动提高
  **{motion_cmp['ssim_all']['delta_x265_minus_x264']:.4f}**（{motion_cmp['ssim_all']['x264']:.5f} →
  {motion_cmp['ssim_all']['x265']:.5f}）。
- x265 的代价是编码更慢：静态约为 x264 的
  **{static_cmp['encode_time_s']['x265_over_x264']:.2f}×**，高运动约为
  **{motion_cmp['encode_time_s']['x265_over_x264']:.2f}×**（两次编码墙钟中位数）。
- 500 ms 漏桶、初始满缓冲下，静态两者均为 **2 次**违规，来自第 0/60 帧 I 帧；
  高运动 x264 为 **8 次**、x265 为 **5 次**。本实验未启用编码器 VBV，因此该指标反映
  码率分配的瞬时突发，而非 HRD 合规性。
- 高频细节应按“结构相关性 + 归一化高频误差”判定：x265 在两种素材上均更好，
  高运动尤其明显（相关 0.6018 vs 0.0271）。静态的原始高频**能量**保留率 x265
  反而低 1.67 个百分点，说明其静态画面有轻微平滑，但纹理结构仍更接近原片；
  不能只凭高频能量大小宣称“更清晰”。
- 三次独立运行的码流 SHA-256、解码 MD5、SSIM 与高频指标完全一致；质量结论可复现。
  墙钟耗时 CV 为 1.4%–4.5%，CPU 时间 CV 为 1.2%–5.0%。
- 无损脉冲响应测试中，x265 `tiles=1x1/2x2` 和 x264 `partitions=none/all` 的输出
  均为 **0 LSB 最大绝对差、0 个失配样本、PSNR=inf**，远在容差内。

## 实验设置

- 素材：640×360、30 fps、90 帧（3 s），YUV420p；静态序列无帧间运动，
  高运动序列每帧水平平移 9 px，并叠加正弦垂直平移与移动圆盘。
- 编码：FFmpeg 7.0.2 静态构建，`libx264` / `libx265`，`preset=medium`，
  GOP 60，单线程，两次 ABR。每个目标点自动调整控制码率，使实际码率贴近目标；
  曲线横轴始终使用实际码率。
- 质量：逐帧 SSIM 的 90 帧均值（All 和 Y 均给出）。
- 高频指标：5 点高通（像素减 4 邻域均值）后的能量保留比、源/失真高频平面
  相关系数，以及归一化绝对高频误差。相关越高、归一化误差越低表示细节越清晰。
- 缓冲：500 ms、容量按实际码率计算、初始 100% 满；每帧先到达
  `bitrate/fps`，再扣除该帧实际比特；不足即记一次违规。
- 分区：128×128 脉冲响应，含位于 tile 边界的高斯脉冲；无损 `qp=0`。

## 主实验点

{table(
    ["素材", "编码器", "实际 kbps", "SSIM All", "SSIM Y", "HF 能量比", "HF 相关", "HF 归一误差", "缓冲违规", "墙钟 s", "CPU s"],
    primary_rows,
)}

![SSIM 与耗时曲线](artifacts/figures/ssim_time_curves.png)

![码率-失真曲线](artifacts/figures/rd_curve.png)

## 五点扫描

{table(["目标 kbps", "素材", "编码器", "实际 kbps", "SSIM All", "墙钟中位 s", "±1% 内"], sweep_rows)}

少数扫描点的目标值受编码器整数 kbps 量化影响未进入 ±1%，但曲线按实际码率绘制，
不会把目标值的偏差误当成同等码率下的质量增益；主实验点四组全部收敛。

## 缓冲违规

静态素材的违规帧固定为 `[0, 60]`，即两个 I 帧；x265 高运动违规帧为
`{cells[('x265', 'motion', PRIMARY_KBPS)]['buffer']['violation_frames']}`，
 x264 为 `{cells[('x264', 'motion', PRIMARY_KBPS)]['buffer']['violation_frames']}`。
x265 高运动把更多码率集中到 I 帧（见下节），虽然违规次数更少，但个别 P 帧突发更大，
在 500 ms 满启动缓冲模型下出现 5 次；x264 的 B 帧分散策略留下 8 次。

![缓冲水位](artifacts/figures/primary_buffer_level.png)

## 码率分配差异

{table(["素材", "编码器", "I 帧占比", "P 帧占比", "B 帧占比", "帧比特 CV", "P95/均值", "最大/均值"], allocation_rows)}

- 静态素材几乎全部预算进入 2 个 I 帧：x264 为 98.27% I，x265 为 96.82% I。
- 高运动素材差异显著：x265 的 I 帧占比为 30.87%，x264 仅 7.18%；
  x265 的 B 帧占比为 10.51%，x264 为 28.21%。x265 更偏向少量高质量参考帧，
  x264 更偏向 B 帧间补偿。
- 因此 x265 高运动帧比特的 CV 为 2.293，x264 为 1.018；最大/均值分别为
  15.02 与 4.91。x265 的同码率质量优势伴随更强的瞬时码率集中。

![码率分配](artifacts/figures/primary_bit_allocation.png)

## 高频细节

{table(
    ["素材", "编码器", "HF 能量保留", "HF 相关", "HF 归一误差"],
    [[
        clip,
        encoder,
        f"{cells[(encoder, clip, PRIMARY_KBPS)]['high_frequency']['hf_energy_ratio']:.4f}",
        f"{cells[(encoder, clip, PRIMARY_KBPS)]['high_frequency']['hf_correlation']:.4f}",
        f"{cells[(encoder, clip, PRIMARY_KBPS)]['high_frequency']['relative_hf_error']:.4f}",
    ] for clip in ("static", "motion") for encoder in ("x264", "x265")],
)}

![高频指标](artifacts/figures/hf_detail.png)

![局部细节](artifacts/figures/primary_detail_crops.png)

结论：**相同实际码率下，x265 的高频纹理结构更清晰**。高运动素材的证据最强：
相关从 0.0271 升至 0.6018、归一化高频误差从 0.8142 降至 0.6289，局部图中的
纹理保留也明显更好。静态素材相关从 0.8896 升至 0.9532、误差从 0.3649 降至
0.2347，但高频能量比从 0.9106 降至 0.8939；所以静态应表述为“结构更准确、
略有平滑”，而不是“高频能量更高”。

## 可复现性

{table(["主实验组", "码流一致", "解码一致", "SSIM 极差", "HF 极差", "墙钟均值 ± 标准差 s", "墙钟 CV", "CPU CV"], repro_rows)}

三次运行产生相同码流哈希与解码哈希，SSIM/高频指标极差为 0。耗时仍有秒级以下的
主机调度波动，因此报告使用中位数并用 CV 量化不确定性。

## 分区脉冲响应

{table(["测试", "分区", "A/B 最大差 LSB", "A/B 平均差 LSB", "A/B PSNR", "容差内"], partition_rows)}

容差设置为最大绝对差 ≤ 1 LSB、平均绝对差 ≤ 0.01 LSB。两组测试实际均为 0 LSB，
因此这两种分区在相同脉冲响应下的输出差异在数值容差内，且与源图逐样本一致。

## 复现命令

```bash
make all
make verify
```

等价的分步命令：

```bash
PYTHONPATH=/home/hj/userfeedback/uid4-0042/.deps:$PWD/src python3 src/generate_sources.py
PYTHONPATH=/home/hj/userfeedback/uid4-0042/.deps:$PWD/src python3 src/run_experiment.py
PYTHONPATH=/home/hj/userfeedback/uid4-0042/.deps:$PWD/src python3 src/partition_test.py
PYTHONPATH=/home/hj/userfeedback/uid4-0042/.deps:$PWD/src python3 src/make_detail_crops.py
PYTHONPATH=/home/hj/userfeedback/uid4-0042/.deps:$PWD/src python3 src/verify_results.py
```

机器可读结果：`artifacts/results/results.json`、`summary.csv`、`per_frame.csv`、
`comparisons.json`、`reproducibility.json`、`verification.json`。
"""
    output = ROOT / "REPORT.md"
    output.write_text(report)
    print(output.relative_to(ROOT))


if __name__ == "__main__":
    main()
