"""Aggregate timing (3 runs), bitrate/QP statistics and produce a master summary JSON."""
import csv, glob, json, os, statistics, re

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FPS, N, DUR = 30, 540, 18.0
NAMES = ["crf", "abr", "vbv_native", "crf_cap", "abr_cap", "vbv_tight"]
LABEL = {
    "crf": "CRF (uncapped, crf23)",
    "abr": "ABR (2.5M, uncapped)",
    "vbv_native": "VBV (crf23, max4M/B8M)",
    "crf_cap": "CRF+cap (crf23, max4M/B8M)",
    "abr_cap": "ABR+cap (2.5M, max4M/B8M)",
    "vbv_tight": "VBV tight (crf23, max4M/B4M)",
}

def read_csv(name, suffix):
    p = os.path.join(ROOT, f"work/analysis/csv/{name}.{suffix}")
    with open(p) as fh:
        return list(csv.DictReader(fh))

def timing():
    out = {}
    for name in NAMES:
        walls, cpus = [], []
        for r in (1, 2, 3):
            txt = open(os.path.join(ROOT, f"work/logs/{name}.r{r}.time")).read()
            m = re.search(r"WALL_ELAPSED=([\d.]+) USER_CPU=([\d.]+) SYS_CPU=([\d.]+)", txt)
            walls.append(float(m.group(1)))
            cpus.append(float(m.group(2)) + float(m.group(3)))
        out[name] = dict(
            wall_mean=round(statistics.fmean(walls), 3),
            wall_stdev=round(statistics.pstdev(walls), 3),
            wall_runs=walls,
            cpu_mean=round(statistics.fmean(cpus), 2),
            fps_mean=round(N / statistics.fmean(walls), 1),
        )
    return out

def main():
    tim = timing()
    vbv = json.load(open(os.path.join(ROOT, "work/analysis/vbv_report.json")))
    loop = json.load(open(os.path.join(ROOT, "work/analysis/loopback_report.json")))
    metrics = {}
    for name in NAMES:
        psnr_log = open(os.path.join(ROOT, f"work/metrics/{name}.psnr.log")).read()
        ssim_log = open(os.path.join(ROOT, f"work/metrics/{name}.ssim.log")).read()
        psnr = float(re.search(r"average:([\d.]+)", psnr_log).group(1))
        ssim = float(re.search(r"SSIM Y:[\d.]+ .*?All:([\d.]+)", ssim_log).group(1))
        frames = read_csv(name, "csv")
        sizes = [int(r["size_ffprobe"]) for r in frames]
        qps = [float(r["qp"]) for r in frames]
        total_bits = sum(sizes) * 8
        # instantaneous per-frame bitrate (window=1 frame)
        inst = [s * 8 * FPS / 1e6 for s in sizes]          # Mbps
        # 1-second sliding-window peak bitrate
        win = [sum(sizes[max(0, i - FPS + 1):i + 1]) * 8
               / (min(i + 1, FPS) / FPS) / 1e6 for i in range(N)]
        metrics[name] = dict(
            label=LABEL[name],
            avg_bitrate_mbps=round(total_bits / DUR / 1e6, 3),
            peak_instant_mbps=round(max(inst), 3),
            peak_1s_window_mbps=round(max(win), 3),
            bitrate_stdev_mbps=round(statistics.pstdev(inst), 3),
            bitrate_cv=round(statistics.pstdev(inst) / statistics.fmean(inst), 3),
            qp_mean=round(statistics.fmean(qps), 2),
            qp_stdev=round(statistics.pstdev(qps), 2),
            qp_min=min(qps), qp_max=max(qps),
            psnr_avg=psnr, ssim_all=ssim,
            vbv=vbv[name], loopback=loop[name], timing=tim[name],
        )
    with open(os.path.join(ROOT, "work/analysis/summary.json"), "w") as fh:
        json.dump(metrics, fh, indent=2)
    for name in NAMES:
        m = metrics[name]
        print(f"{name:11s} avg={m['avg_bitrate_mbps']:6.3f} peak1s={m['peak_1s_window_mbps']:6.3f} "
              f"inst={m['peak_instant_mbps']:7.3f} CV={m['bitrate_cv']:5.2f} "
              f"QP={m['qp_mean']:5.2f}+-{m['qp_stdev']:4.2f} PSNR={m['psnr_avg']:6.2f} "
              f"SSIM={m['ssim_all']:.4f} wall={m['timing']['wall_mean']}s")

if __name__ == "__main__":
    main()
