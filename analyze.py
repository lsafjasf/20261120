"""分析管线:
1) 对关滤波解码帧施加自研 H.264 去块滤波, 输出每帧 BS 分布与耗时
2) 开/关滤波块边界像素差对比
3) 多 QP 下 PSNR 对比
4) 真实边缘保持验证
5) 真实边缘 vs 块效应边界 两类在噪声下的分离度与阈值合理性
"""
import time
import numpy as np
from deblock import deblock_frame, ALPHA, BETA
from gen_video import TRUE_EDGE_COLS, TRUE_EDGE_ROWS, W, H, N

QPS = [20, 30, 40]


def load_luma(path):
    raw = np.fromfile(path, np.uint8)
    fsz = W * H * 3 // 2
    n = raw.size // fsz
    return raw[:n * fsz].reshape(n, fsz)[:, :W * H].reshape(n, H, W)


def psnr(a, b):
    mse = np.mean((a.astype(np.float64) - b.astype(np.float64)) ** 2)
    return 99.0 if mse == 0 else 10 * np.log10(255.0 ** 2 / mse)


def boundary_diffs(y):
    """所有 4 像素网格边界上的 |p0-q0| 数组."""
    v = np.abs(np.diff(y.astype(np.int16), axis=1))[:, 3::4].ravel()
    hz = np.abs(np.diff(y.astype(np.int16), axis=0))[3::4, :].ravel()
    return np.concatenate([v, hz])


def edge_gradient(y, cols, rows):
    g = [np.abs(np.diff(y.astype(np.int16), axis=1))[:, c - 1].mean() for c in cols]
    g += [np.abs(np.diff(y.astype(np.int16), axis=0))[r - 1, :].mean() for r in rows]
    return float(np.mean(g))


def main():
    orig = load_luma("test.yuv")
    dec = {qp: {m: load_luma(f"enc/qp{qp}_{m}.yuv") for m in ("on", "off")}
           for qp in QPS}

    print("=" * 72)
    print("[1] 自研去块滤波: 每帧滤波强度(BS)分布与耗时 (作用于关滤波解码帧)")
    print("=" * 72)
    mine = {}
    for qp in QPS:
        hist_all = np.zeros(5, np.int64)
        times, frames = [], []
        for f in range(N):
            t0 = time.perf_counter()
            out, hist = deblock_frame(dec[qp]["off"][f], qp)
            times.append((time.perf_counter() - t0) * 1e3)
            hist_all += hist
            frames.append(out)
        mine[qp] = np.stack(frames)
        total = hist_all.sum()
        print(f"QP={qp}: 总边界段={total}  "
              f"BS0/1/2/3/4 = " +
              " / ".join(f"{h}({h/total*100:.1f}%)" for h in hist_all))
        print(f"       每帧耗时: 均值 {np.mean(times):.2f} ms, "
              f"最小 {np.min(times):.2f}, 最大 {np.max(times):.2f} ms")
        for f in (0, N // 2, N - 1):
            _, h = deblock_frame(dec[qp]["off"][f], qp)
            print(f"       帧{f:02d} BS分布: {h.tolist()}")

    print()
    print("=" * 72)
    print("[2] 块边界像素差 |p0-q0| (开滤波 / 关滤波 / 自研滤波), 全帧平均")
    print("=" * 72)
    for qp in QPS:
        for name, seq in (("x264开滤波", dec[qp]["on"]),
                          ("x264关滤波", dec[qp]["off"]),
                          ("自研滤波  ", mine[qp])):
            d = np.concatenate([boundary_diffs(f) for f in seq])
            bins = [0, 1, 2, 4, 8, 16, 32, 256]
            cnt = np.histogram(d, bins=bins)[0] / d.size * 100
            dist = " ".join(f"{bins[i]}-{bins[i+1]}:{cnt[i]:4.1f}%"
                            for i in range(len(cnt)))
            print(f"QP={qp} {name}: 均值 {d.mean():6.2f}  "
                  f"P50 {np.percentile(d,50):4.0f}  "
                  f"P90 {np.percentile(d,90):4.0f}  "
                  f"P99 {np.percentile(d,99):4.0f}")
            print(f"         分布: {dist}")
        print()

    print("=" * 72)
    print("[3] 不同 QP 下去块滤波对整体画质的影响 (亮度 PSNR vs 原始, dB)")
    print("=" * 72)
    print(f"{'QP':>4} {'关滤波':>8} {'开滤波':>8} {'自研滤波':>8} {'开-关增益':>9}")
    for qp in QPS:
        p_off = np.mean([psnr(orig[f], dec[qp]["off"][f]) for f in range(N)])
        p_on = np.mean([psnr(orig[f], dec[qp]["on"][f]) for f in range(N)])
        p_my = np.mean([psnr(orig[f], mine[qp][f]) for f in range(N)])
        print(f"{qp:>4} {p_off:8.2f} {p_on:8.2f} {p_my:8.2f} {p_on-p_off:+9.3f}")

    print()
    print("=" * 72)
    print("[4] 真实边缘保持验证 (真实边缘处梯度, 滤波后/滤波前 应≈1)")
    print("=" * 72)
    for qp in QPS:
        for name, seq in (("x264开滤波", dec[qp]["on"]),
                          ("自研滤波  ", mine[qp])):
            g_before = np.mean([edge_gradient(dec[qp]["off"][f],
                                              TRUE_EDGE_COLS, TRUE_EDGE_ROWS)
                                for f in range(N)])
            g_after = np.mean([edge_gradient(seq[f], TRUE_EDGE_COLS,
                                             TRUE_EDGE_ROWS)
                               for f in range(N)])
            print(f"QP={qp} {name}: 滤波前 {g_before:6.2f} -> 滤波后 "
                  f"{g_after:6.2f}  保持率 {g_after/g_before*100:5.1f}%")

    print()
    print("=" * 72)
    print("[5] 两类边界(真实边缘 vs 块效应)在噪声下的分离度与阈值合理性")
    print("=" * 72)
    true_cols = set(TRUE_EDGE_COLS) | {c - 1 for c in TRUE_EDGE_COLS}
    for qp in QPS:
        alpha, beta = int(ALPHA[qp]), int(BETA[qp])
        d_true, d_art = [], []
        for f in range(N):
            y = dec[qp]["off"][f].astype(np.int16)
            vd = np.abs(np.diff(y, axis=1))
            for x in range(4, W - 3, 4):
                (d_true if x in true_cols else d_art).extend(vd[:, x - 1])
        d_true, d_art = np.array(d_true), np.array(d_art)
        mu_t, mu_a = d_true.mean(), d_art.mean()
        sd_t, sd_a = d_true.std(), d_art.std()
        dprime = abs(mu_t - mu_a) / np.sqrt((sd_t**2 + sd_a**2) / 2)
        # 阈值判定: 真实边缘应 |p0-q0|>=alpha(不被滤), 块效应应 <alpha(被滤)
        tpr = (d_true >= alpha).mean()
        tnr = (d_art < alpha).mean()
        print(f"QP={qp}: alpha={alpha} beta={beta}")
        print(f"  真实边缘 |p0-q0|: 均值 {mu_t:6.2f} ± {sd_t:5.2f}")
        print(f"  块效应   |p0-q0|: 均值 {mu_a:6.2f} ± {sd_a:5.2f}")
        print(f"  分离度 d' = {dprime:.2f}  "
              f"真实边缘正确排除率 {tpr*100:.1f}%  "
              f"块效应正确滤波率 {tnr*100:.1f}%")


if __name__ == "__main__":
    main()
