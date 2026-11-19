"""Risk-table report generation.

For every take, saves the representative original frame plus a
line-brightness profile figure (space-time heatmap and per-frame
profile), then assembles a Markdown risk table covering all takes.
"""

from __future__ import annotations

import json
import os

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

RISK_LABEL = {"high": "高", "medium": "中", "low": "低"}
VERDICT_LABEL = {
    "scan_band": "扫描条带",
    "localized_object": "局部运动物体(非扫描)",
    "static_structure": "静态结构(非扫描)",
    "clean": "未检出",
}


def _save_frame_image(frames: np.ndarray, path: str) -> None:
    mid = frames[len(frames) // 2]
    plt.figure(figsize=(6, 4))
    plt.imshow(mid, cmap="gray", vmin=0, vmax=255)
    plt.title("representative frame")
    plt.colorbar(label="brightness")
    plt.tight_layout()
    plt.savefig(path, dpi=110)
    plt.close()


def _save_profile_plot(axes_full: dict, primary_axis: str, path: str) -> None:
    res = axes_full[primary_axis]
    hp, norm = res["highpass"], res["norm"]
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 4),
                                   gridspec_kw={"width_ratios": [1.4, 1]})
    im = ax1.imshow(hp.T, aspect="auto", cmap="RdBu_r",
                    vmin=-0.3, vmax=0.3, origin="upper")
    ax1.set_xlabel("frame")
    ax1.set_ylabel("line index")
    ax1.set_title(f"line brightness (temporal high-pass, axis={primary_axis})")
    fig.colorbar(im, ax=ax1, label="relative deviation")
    mid = hp.shape[0] // 2
    ax2.plot(norm[mid], np.arange(norm.shape[1]), label="raw (norm.)")
    ax2.plot(hp[mid], np.arange(hp.shape[1]), label="high-passed")
    ax2.axvline(1.0, color="gray", ls="--", lw=0.8)
    ax2.set_ylim(norm.shape[1], 0)
    ax2.set_xlabel("relative brightness")
    ax2.set_ylabel("line index")
    ax2.set_title(f"profile @ frame {mid}")
    ax2.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)


def _fmt(x, nd=3):
    return "—" if x is None else f"{x:.{nd}f}"


def _take_row(res: dict, rel: str) -> str:
    log = res["log"]
    axis = res.get("primary_axis")
    band = res["axes"].get(axis, {}) if axis else {}
    cells = [
        res["take"],
        f'1/{round(1 / log["exposure_s"])}' if log["exposure_s"] < 1 else str(log["exposure_s"]),
        _fmt(log["fps"], 3),
        _fmt(log["trigger_phase_s"] * 1000, 2) + " ms",
        VERDICT_LABEL[res["verdict"]],
        _fmt(band.get("band_width_lines"), 1),
        band.get("direction", "—") if res["verdict"] == "scan_band" else "—",
        _fmt(band.get("period_frames"), 2),
        _fmt(band.get("mean_depth"), 3),
        RISK_LABEL[res["risk"]],
        f"[帧]({rel}/{res['take']}_frame.png) [剖面]({rel}/{res['take']}_profile.png)",
    ]
    return "| " + " | ".join(str(c) for c in cells) + " |"


def write_report(results: list, out_dir: str) -> str:
    os.makedirs(out_dir, exist_ok=True)
    lines = ["# LED 墙快门设置预检报告", ""]
    header = ("| 测试条 | 快门(s) | 帧率(fps) | 触发相位 | 判定 | 带宽(行) | "
              "移动方向 | 跨帧周期 | 平均深度 | 风险 | 证据 |")
    sep = "|" + "---|" * 11
    lines += [header, sep]
    clean_results = []
    for res in results:
        take = res["take"]
        frame_png = os.path.join(out_dir, f"{take}_frame.png")
        prof_png = os.path.join(out_dir, f"{take}_profile.png")
        _save_frame_image(res["_frames"], frame_png)
        axis = res.get("primary_axis") or "row"
        _save_profile_plot(res["_axes_full"], axis, prof_png)
        lines.append(_take_row(res, "."))
        clean = {k: v for k, v in res.items() if not k.startswith("_")}
        clean_results.append(clean)

    lines.append("")
    lines.append("## 歧义与补拍建议")
    lines.append("")
    any_amb = False
    for res in results:
        if res.get("ambiguous"):
            any_amb = True
            c = res["scan_rate_candidates"]
            res_str = " 或 ".join(f"{r:.2f}" for r in c["residues_mod_fps"])
            lines.append(
                f"- **{res['take']}**：观测漂移无法唯一确定扫描频率（帧率混叠）。"
                f"候选为 f ≡ {res_str} (mod {c['fps']:.3f}) Hz，"
                f"在 {c['f_min']:.0f}–{c['f_max']:.0f} Hz 内共 {c['count']} 个。")
            for tip in res.get("reshoot_advice", []):
                lines.append(f"  - {tip}")
    if not any_amb:
        lines.append("- 无歧义项。")
    lines.append("")
    lines.append("## 对照样例核验")
    lines.append("")
    for res in results:
        if res["log"].get("kind") == "control":
            ok = res["verdict"] != "scan_band"
            mark = "通过" if ok else "失败"
            lines.append(f"- {res['take']}（{res['log'].get('note', '')}）："
                         f"判定为 {VERDICT_LABEL[res['verdict']]}，未被标为扫描条带 → {mark}")
    lines.append("")
    report_path = os.path.join(out_dir, "report.md")
    with open(report_path, "w") as fh:
        fh.write("\n".join(lines))
    with open(os.path.join(out_dir, "results.json"), "w") as fh:
        json.dump(clean_results, fh, indent=2, ensure_ascii=False)
    return report_path
