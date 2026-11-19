"""Plot per-frame QP, frame bytes, instantaneous bitrate and VBV fill curves."""
import csv, os
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CSVDIR = os.path.join(ROOT, "work/analysis/csv")
OUT = os.path.join(ROOT, "artifacts/figures")
FPS = 30
BURSTS = [(3, 7), (14, 18)]
CORE = ["crf", "abr", "vbv_native"]
CAPS = ["crf_cap", "abr_cap", "vbv_native", "vbv_tight"]
COLOR = dict(crf="#d62728", abr="#1f77b4", vbv_native="#2ca02c",
             crf_cap="#ff7f0e", abr_cap="#9467bd", vbv_tight="#8c564b")
LABEL = dict(crf="CRF uncapped", abr="ABR uncapped", vbv_native="VBV 4M/B8M",
             crf_cap="CRF+cap 4M/B8M", abr_cap="ABR+cap 4M/B8M",
             vbv_tight="VBV tight 4M/B4M")

def load(name, suffix="csv"):
    fname = f"{name}.{suffix}.csv" if suffix != "csv" else f"{name}.csv"
    with open(os.path.join(CSVDIR, fname)) as fh:
        return list(csv.DictReader(fh))

def load_psnr(name):
    out = []
    with open(os.path.join(ROOT, f"work/metrics/{name}.psnr.csv")) as fh:
        header = fh.readline()
        for line in fh:
            kv = dict(tok.split(":", 1) for tok in line.split())
            out.append(kv)
    return out

def shade(ax):
    for a, b in BURSTS:
        ax.axvspan(a, b, color="orange", alpha=0.12, lw=0)
    ax.set_xlabel("time (s)")
    ax.grid(alpha=0.3)

def fig_qp():
    fig, ax = plt.subplots(figsize=(11, 5))
    for n in CORE:
        r = load(n)
        ax.plot([float(x["t"]) for x in r], [float(x["qp"]) for x in r],
                lw=1, color=COLOR[n], label=LABEL[n])
    shade(ax); ax.set_ylabel("frame QP"); ax.set_title("Per-frame QP (core modes)")
    ax.legend(); fig.tight_layout(); fig.savefig(os.path.join(OUT, "01_qp_core.png"), dpi=140)
    plt.close(fig)

def fig_bytes():
    fig, ax = plt.subplots(figsize=(11, 5))
    for n in CORE:
        r = load(n)
        ax.plot([float(x["t"]) for x in r],
                [int(x["size_ffprobe"]) / 1000 for x in r],
                lw=1, color=COLOR[n], label=LABEL[n])
    shade(ax); ax.set_ylabel("frame size (KB)")
    ax.set_title("Per-frame coded size (core modes)")
    ax.legend(); fig.tight_layout(); fig.savefig(os.path.join(OUT, "02_frame_bytes_core.png"), dpi=140)
    plt.close(fig)

def fig_bitrate():
    fig, axes = plt.subplots(2, 1, figsize=(11, 8), sharex=True)
    for n in CORE:
        r = load(n)
        t = [float(x["t"]) for x in r]
        inst = [int(x["size_ffprobe"]) * 8 * FPS / 1e6 for x in r]
        axes[0].plot(t, inst, lw=1, color=COLOR[n], label=LABEL[n])
    for n in CAPS:
        r = load(n)
        t = [float(x["t"]) for x in r]
        inst = [int(x["size_ffprobe"]) * 8 * FPS / 1e6 for x in r]
        axes[1].plot(t, inst, lw=1, color=COLOR[n], label=LABEL[n])
    axes[1].axhline(4, color="k", ls="--", lw=1, label="maxrate 4 Mbps")
    for ax in axes:
        shade(ax); ax.set_ylabel("instant bitrate (Mbps)"); ax.legend()
    axes[0].set_title("Instantaneous per-frame bitrate: uncapped vs VBV-capped")
    fig.tight_layout(); fig.savefig(os.path.join(OUT, "03_bitrate.png"), dpi=140)
    plt.close(fig)

def fig_vbv():
    fig, axes = plt.subplots(2, 1, figsize=(11, 8), sharex=True)
    # top: core 3 against the 4M/8M reference bucket
    for n in CORE:
        r = load(n, "vbv")
        axes[0].plot([float(x["t"]) for x in r],
                     [float(x["occ_frac"]) * 100 for x in r],
                     lw=1, color=COLOR[n], label=LABEL[n])
    axes[0].axhline(0, color="red", ls="--", lw=1, label="underflow line")
    # bottom: native VBV variants on their own HRD bucket
    for n in ["vbv_native", "crf_cap", "abr_cap", "vbv_tight"]:
        r = load(n, "vbv")
        axes[1].plot([float(x["t"]) for x in r],
                     [float(x["occ_frac"]) * 100 for x in r],
                     lw=1, color=COLOR[n], label=LABEL[n])
    axes[1].axhline(0, color="red", ls="--", lw=1)
    for ax in axes:
        shade(ax); ax.set_ylabel("VBV fill (% of CPB)"); ax.legend()
    axes[0].set_title("VBV buffer fill level (top: same 4M/8M reference bucket)")
    fig.tight_layout(); fig.savefig(os.path.join(OUT, "04_vbv_fill.png"), dpi=140)
    plt.close(fig)

def fig_quality():
    fig, ax = plt.subplots(figsize=(11, 5))
    for n in CORE:
        rows = load_psnr(n)
        t = [(int(r["n"]) - 1) / FPS for r in rows]
        ax.plot(t, [float(r["psnr_avg"]) for r in rows], lw=1,
                color=COLOR[n], label=LABEL[n])
    shade(ax); ax.set_ylabel("PSNR (dB)"); ax.set_title("Per-frame PSNR (core modes)")
    ax.legend(); fig.tight_layout(); fig.savefig(os.path.join(OUT, "05_psnr.png"), dpi=140)
    plt.close(fig)

if __name__ == "__main__":
    fig_qp(); fig_bytes(); fig_bitrate(); fig_vbv(); fig_quality()
    print(sorted(os.listdir(OUT)))
