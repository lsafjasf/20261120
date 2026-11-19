"""Parse x264 debug logs into per-frame CSVs (display order) and validate vs ffprobe."""
import csv, glob, os, re, subprocess, json

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FF = os.path.join(ROOT, "tools/ffmpeg-7.0.2-amd64-static/ffprobe")
LOGDIR = os.path.join(ROOT, "work/logs")
OUTDIR = os.path.join(ROOT, "work/analysis/csv")
os.makedirs(OUTDIR, exist_ok=True)
LINE = re.compile(r"frame=\s*(\d+) QP=([\d.]+) NAL=\d+ Slice:(\w+) Poc:(\d+).*size=(\d+) bytes")

def parse_log(path):
    rows = []
    idr_count = -1
    prev_poc = -1
    with open(path, errors="replace") as fh:
        for line in fh:
            m = LINE.search(line)
            if not m:
                continue
            enc_idx, qp, sl, poc, size = m.groups()
            enc_idx, poc, size = int(enc_idx), int(poc), int(size)
            if sl == "I":
                idr_count += 1
                prev_poc = -1
            gop = idr_count
            global_poc = gop * 60 + poc
            rows.append(dict(enc_idx=enc_idx, poc=global_poc, gop=gop,
                             qp=float(qp), stype=sl, size=size))
    rows.sort(key=lambda r: r["poc"])
    for i, r in enumerate(rows):
        r["frame"] = i
        r["t"] = i / 30.0
    return rows

def ffprobe_sizes(mp4):
    out = subprocess.run([FF, "-v", "error", "-select_streams", "v",
                          "-show_entries", "frame=pkt_size,pict_type",
                          "-of", "csv=p=0", mp4], capture_output=True, text=True, check=True).stdout
    sizes, types = [], []
    for line in out.strip().splitlines():
        parts = line.split(",")
        sizes.append(int(parts[0])); types.append(parts[1])
    return sizes, types

def main():
    summary = {}
    for log in sorted(glob.glob(os.path.join(LOGDIR, "*.r1.log"))):
        name = os.path.basename(log).split(".r1.log")[0]
        rows = parse_log(log)
        assert len(rows) == 540, (name, len(rows))
        mp4 = os.path.join(ROOT, f"work/enc/{name}.r1.mp4")
        fp_sizes, fp_types = ffprobe_sizes(mp4)
        assert len(fp_sizes) == 540, (name, len(fp_sizes))
        x_sizes = [r["size"] for r in rows]
        # cross-check totals (mp4 muxes identical NAL payload; allow tiny container variance)
        tot_x, tot_f = sum(x_sizes), sum(fp_sizes)
        diff = abs(tot_x - tot_f) / tot_x
        with open(os.path.join(OUTDIR, f"{name}.csv"), "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["frame", "t", "enc_idx", "stype", "qp", "size_x264", "size_ffprobe"])
            for r, fs in zip(rows, fp_sizes):
                w.writerow([r["frame"], f"{r['t']:.3f}", r["enc_idx"], r["stype"],
                            r["qp"], r["size"], fs])
        summary[name] = dict(frames=len(rows), bytes_x264=tot_x,
                             bytes_ffprobe=tot_f, rel_diff=round(diff, 5))
    print(json.dumps(summary, indent=1))

if __name__ == "__main__":
    main()
