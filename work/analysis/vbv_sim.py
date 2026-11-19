"""VBV / HRD leaky-bucket simulation with the x264 fullness recursion.

Recursion (VBR HRD, buffer starts full after initial buffering delay B/R):
    F[0]   = B - bits[0]
    F[n]   = F[n-1] + R/fps - bits[n]
An underflow occurs if F would fall below 0 before frame n is removed.
Occupancy above B means the encoder relied on a larger CPB than this bucket
(we clip for display and count saturation events separately).
"""
import csv, json, os, statistics

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CSVDIR = os.path.join(ROOT, "work/analysis/csv")
FPS = 30

CFG = {
    "crf":        (None, None),
    "abr":        (None, None),
    "vbv_native": (4_000_000, 8_000_000),
    "crf_cap":    (4_000_000, 8_000_000),
    "abr_cap":    (4_000_000, 8_000_000),
    "vbv_tight":  (4_000_000, 4_000_000),
}
REF_R, REF_B = 4_000_000, 8_000_000

def simulate(sizes, maxrate, bufsize):
    add = maxrate / FPS
    occ = float(bufsize) - sizes[0] * 8
    series, under, sat = [occ], 0, 0
    if occ < 0:
        under += 1
        occ = 0.0
    for s in sizes[1:]:
        occ += add - s * 8
        if occ < -1e-6:
            under += 1
        if occ > bufsize + 1e-6:
            sat += 1
        occ = min(max(occ, 0.0), float(bufsize))
        series.append(occ)
    return series, under, sat

def load(name):
    with open(os.path.join(CSVDIR, f"{name}.csv")) as fh:
        return list(csv.DictReader(fh))

def main():
    report = {}
    for name, (r, b) in CFG.items():
        rows = load(name)
        sizes = [int(x["size_ffprobe"]) for x in rows]
        use_r, use_b = (r, b) if r else (REF_R, REF_B)
        series, under, sat = simulate(sizes, use_r, use_b)
        frac = [s / use_b for s in series]
        with open(os.path.join(CSVDIR, f"{name}.vbv.csv"), "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["frame", "t", "occ_bits", "occ_frac"])
            for i, s in enumerate(series):
                w.writerow([i, f"{i/FPS:.3f}", f"{s:.1f}", f"{s/use_b:.4f}"])
        report[name] = dict(
            maxrate_bps=use_r, bufsize_bits=use_b,
            underflows=under, cpb_saturation_events=sat,
            min_fill_frac=round(min(frac), 4),
            mean_fill_frac=round(statistics.fmean(frac), 4),
            fill_stdev_frac=round(statistics.pstdev(frac), 4),
            fill_range_frac=round(max(frac) - min(frac), 4),
            simulated_with=("reference bucket 4M/8M (stream itself uncapped)"
                            if r is None else "native HRD params"),
        )
    with open(os.path.join(ROOT, "work/analysis/vbv_report.json"), "w") as fh:
        json.dump(report, fh, indent=2)
    print(json.dumps(report, indent=2))

if __name__ == "__main__":
    main()
