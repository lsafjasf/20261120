"""Per-frame/per-plane max absolute difference between original and loopback YUV."""
import csv, json, os
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
W, H, FPS, N = 960, 540, 30, 540
Y0, UVW, UVH = W * H, W // 2, H // 2
FRAME = Y0 + 2 * (UVW * UVH)
NAMES = ["crf", "abr", "vbv_native", "crf_cap", "abr_cap", "vbv_tight"]

def frames(path):
    raw = np.fromfile(path, dtype=np.uint8)
    assert raw.size == FRAME * N, (path, raw.size)
    return raw.reshape(N, FRAME)

def main():
    src = frames(os.path.join(ROOT, "work/source_960x540_30.yuv")).astype(np.int16)
    report = {}
    out_csv = os.path.join(ROOT, "work/analysis/loopback_perframe.csv")
    with open(out_csv, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["name", "frame", "max_abs_y", "max_abs_u", "max_abs_v", "max_abs_all",
                    "mae_y"])
        for name in NAMES:
            dec = frames(os.path.join(ROOT, f"work/yuv/{name}.yuv")).astype(np.int16)
            mad = np.abs(dec - src)
            y = mad[:, :Y0]
            u = mad[:, Y0:Y0 + UVW * UVH]
            v = mad[:, Y0 + UVW * UVH:]
            per_frame_max = mad.max(axis=1)
            for i in range(N):
                w.writerow([name, i, int(y[i].max()), int(u[i].max()), int(v[i].max()),
                            int(per_frame_max[i]), round(float(y[i].mean()), 4)])
            report[name] = dict(
                max_abs_diff_overall=int(mad.max()),
                max_abs_y=int(y.max()), max_abs_u=int(u.max()), max_abs_v=int(v.max()),
                mae_all=round(float(mad.mean()), 4), mae_y=round(float(y.mean()), 4),
                p99_abs_all=int(np.percentile(mad, 99)),
                frames_with_max_ge_64=int((per_frame_max >= 64).sum()),
                frames_with_max_ge_32=int((per_frame_max >= 32).sum()),
                frame_argmax=int(per_frame_max.argmax()),
            )
    with open(os.path.join(ROOT, "work/analysis/loopback_report.json"), "w") as fh:
        json.dump(report, fh, indent=2)
    print(json.dumps(report, indent=2))

if __name__ == "__main__":
    main()
