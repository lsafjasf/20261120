"""Assemble the final Markdown report from every pipeline artifact."""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.config import BANDS, CONFIG  # noqa: E402
from src.key_status import KEY_STATUS_ROWS  # noqa: E402

OUT = ROOT / "outputs"


def main() -> None:
    segments = list(csv.DictReader(open(OUT / "segment_scores.csv")))
    comparison = json.loads((OUT / "rank_comparison.json").read_text())
    lines: list[str] = []
    append = lines.append

    append("# 融合画质评分报告")
    append("")
    append("数据集：`synthetic-controlled-degradation`，10 个 2 秒片段（640x360@24fps，每片段 48 帧）。")
    append("")
    _append_scheme(append)
    _append_weights(append)
    _append_bands(append)
    _append_segment_table(append, segments)
    _append_rank_section(append, comparison, segments)
    _append_low_audit(append, comparison["lowScoreAudit"])
    _append_key_status(append)
    (OUT / "report.md").write_text("\n".join(lines) + "\n")
    print(f"wrote {OUT / 'report.md'}")


def _append_scheme(append) -> None:
    append("## 1. 融合评分方案")
    append("")
    append("每个候选帧与同序号参考帧做全参考比较，得到三类分量：")
    append("")
    append("- 视觉信息保真 `fidelity`：亮度 SSIM(0.50)、色度 SSIM(0.20)、归一化 PSNR(0.30) 加权。")
    append("- 细节保持 `detailRetention`：Sobel 梯度能量保持率(0.55) 与梯度空间相关性(0.45) 加权；细节损失 `detailLoss = 1 - detailRetention`。")
    append("- 运动加权输出：运动指数由参考帧帧间平均绝对差得到，池化权重 `w = 1 + motionIndex`；")
    append("  运动保真 `motionFidelity` 为候选保留的参考时域变化比例（单向，只惩罚运动丢失，噪声归入保真分量）。")
    append("")
    append("分段质量指数 `q = 0.45·fidelity + 0.35·detail + 0.20·motion`，")
    append(f"融合分数 `fusedScore = 100 · q^{CONFIG['scoreGamma']}`。")
    append("")


def _append_weights(append) -> None:
    append("## 2. 分量权重与分数区间")
    append("")
    append("| 分量 | 权重 | 分量区间 | 含义 |")
    append("| --- | --- | --- | --- |")
    append("| fidelity（视觉信息保真） | 0.45 | [0, 1] | 亮度/色度结构与像素误差 |")
    append("| detail（细节保持） | 0.35 | [0, 1] | 边缘能量与位置保持，损失为 1-该值 |")
    append("| motion（运动保真） | 0.20 | [0, 1] | 时域运动变化的保留比例 |")
    append("| 融合质量指数 q | 1.00 | [0, 1] | 三分量加权和 |")
    append(f"| fusedScore | - | [0, 100] | q 的 {CONFIG['scoreGamma']} 次幂映射 |")
    append("")
    append("子项权重：保真内部 lumaSSIM 0.50 / chromaSSIM 0.20 / PSNR 0.30；")
    append("细节内部 gradientEnergy 0.55 / gradientCorrelation 0.45。")
    append("")


def _append_bands(append) -> None:
    append("### 分段（band）区间")
    append("")
    append("| band | 分数区间 |")
    append("| --- | --- |")
    for name, low, high in BANDS:
        upper = "100" if name == "excellent" else f"{high:g}"
        append(f"| {name} | [{low:g}, {upper}] |")
    append("")


def _append_segment_table(append, segments: list[dict]) -> None:
    append("## 3. 分段分数（按融合排名）")
    append("")
    append("| 排名 | 片段 | 时间(s) | 失真类型 | fusedScore | band | fidelity | detailLoss | motionFid | 参考分 | MOS |")
    append("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for row in sorted(segments, key=lambda item: int(item["fusedRank"])):
        append(
            "| {rank} | {id} | {start}-{end} | {dist} | {score:.2f} | {band} | "
            "{fid:.3f} | {loss:.3f} | {motion:.3f} | {ref:.2f} | {mos} |".format(
                rank=row["fusedRank"],
                id=row["id"],
                start=row["start"],
                end=row["end"],
                dist=row["distortion"],
                score=float(row["fusedScore"]),
                band=row["band"],
                fid=float(row["fidelity"]),
                loss=float(row["detailLoss"]),
                motion=float(row["motionFidelity"]),
                ref=float(row["referenceScore"]),
                mos=row["subjectiveMos"],
            )
        )
    append("")


def _append_rank_section(append, comparison: dict, segments: list[dict]) -> None:
    append("## 4. 与参考实现的排名一致性")
    append("")
    append("| 指标 | 数值 |")
    append("| --- | --- |")
    append(f"| Spearman（融合 vs 参考） | {comparison['comparison']['spearmanFusedVsReference']:.4f} |")
    append(f"| Kendall τ（融合 vs 参考） | {comparison['comparison']['kendallTauFusedVsReference']:.4f} |")
    append(f"| Spearman（融合 vs 主观） | {comparison['comparison']['spearmanFusedVsSubjective']:.4f} |")
    append(f"| Spearman（参考 vs 主观） | {comparison['comparison']['spearmanReferenceVsSubjective']:.4f} |")
    append("")
    append(
        f"显著不一致阈值：|rankDelta| >= {CONFIG['significantRankDelta']}；"
        "rankDelta = 融合排名 - 参考排名，负值表示融合排名更靠前。"
    )
    append("")
    append("| 片段 | 起止时间(s) | 融合排名 | 参考排名 | rankDelta | 失真类型 | 原因 |")
    append("| --- | --- | --- | --- | --- | --- | --- |")
    for item in comparison["mismatches"]:
        append(
            f"| {item['id']} | {item['start']}-{item['end']} | "
            f"{item['fusedRank']} | {item['referenceRank']} | "
            f"{item['rankDelta']:+d} | {item['distortion']} | {item['reason']} |"
        )
    append("")
    append("排名不一致片段起止时间清单：")
    append("")
    for item in comparison["mismatches"]:
        append(
            f"- {item['id']}（{item['distortion']}）："
            f"{item['start']}s–{item['end']}s，rankDelta={item['rankDelta']:+d}"
        )
    append("")


def _append_low_audit(append, audit: dict) -> None:
    append("## 5. 低分片段与主观判断核对")
    append("")
    append(
        f"阈值：fusedScore < {audit['thresholds']['fusedScoreBelow']:g}，"
        f"主观 MOS < {audit['thresholds']['subjectiveMosBelow']:g}。"
    )
    append("")
    append(f"- 融合低分集合：{', '.join(audit['fusedLow'])}")
    append(f"- 主观低分集合：{', '.join(audit['subjectiveLow'])}")
    append(f"- 集合完全一致：{audit['exactSetMatch']}（precision={audit['precision']:g}, recall={audit['recall']:g}, F1={audit['f1']:g}）")
    append("")


def _append_key_status(append) -> None:
    append("## 6. Key status 播放器行为对照")
    append("")
    append("| key status | 播放行为 | 播放器状态 | 暂停 | 报错 | 动作 |")
    append("| --- | --- | --- | --- | --- | --- |")
    for row in KEY_STATUS_ROWS:
        append(
            f"| {row['status']} | {row['playback']} | {row['playerState']} | "
            f"{row['pause']} | {row['error']} | {row['action']} |"
        )
    append("")


if __name__ == "__main__":
    main()
