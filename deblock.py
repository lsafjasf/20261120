"""H.264 (AVC) 亮度去块滤波实现.

滤波核严格按 H.264 规范 8.7 节:
  - alpha/beta/tc0 门限表 (表 8-16 / 8-17)
  - BS=1..3 普通滤波, BS=4 强滤波(含 ap/aq<beta 的 3 抽头强滤波)
由于不解析码流, BS(边界强度)由局部块效应启发式推导:
边缘两侧对比度越大且两侧内部越平坦, 判定为越强的块效应边界.
"""
import numpy as np

ALPHA = np.array([0]*16 + [4, 4, 5, 6, 7, 8, 10, 12, 14, 16, 18, 20, 23, 25,
                           28, 32, 36, 40, 45, 50, 56, 63, 71, 80, 90, 101,
                           113, 127, 144, 162, 182, 203, 226, 255, 255])
BETA = np.array([0]*16 + [2, 2, 2, 3, 3, 3, 4, 4, 5, 6, 6, 7, 8, 9, 10, 11,
                          12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24,
                          25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36])
_TC0_ROWS = [
    [0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,1,1,1,1,1,1,1,1,1,1,2,2,2,2,3,3,3,4,4,4],
    [0,0,0,0,0,0,0,1,1,1,1,1,1,1,1,1,2,2,2,2,2,2,2,3,3,3,3,4,4,4,5,5,6,6,7,8],
    [0,0,0,0,0,0,1,1,1,1,1,1,1,1,1,2,2,2,2,2,2,3,3,3,3,4,4,4,5,6,6,7,8,9,10,11],
]
TC0 = np.array([[0]*16 + r for r in _TC0_ROWS])  # [bs-1][indexA]


def _clip3(lo, hi, x):
    return min(hi, max(lo, x))


def derive_bs(p0, q0, p1, q1, alpha, beta):
    """启发式 BS 推导: 依据边缘跳变与两侧平坦度."""
    d = abs(p0 - q0)
    flat = (abs(p1 - p0) < beta) and (abs(q1 - q0) < beta)
    if d >= alpha or not flat:
        return 0
    if d >= (alpha * 3) // 4:
        return 4
    if d >= alpha // 2:
        return 3
    if d >= alpha // 4:
        return 2
    return 1


def filter_line(p3, p2, p1, p0, q0, q1, q2, q3, bs, index_a):
    """对一条跨边界的 8 像素线做 H.264 去块滤波, 返回滤波后的 8 像素."""
    alpha, beta = int(ALPHA[index_a]), int(BETA[index_a])
    ap, aq = abs(p2 - p0), abs(q2 - q0)
    if not (abs(p0 - q0) < alpha and abs(p1 - p0) < beta and abs(q1 - q0) < beta):
        return p3, p2, p1, p0, q0, q1, q2, q3
    if bs == 4 and ap < beta and abs(p0 - q0) < ((alpha >> 2) + 2):
        p0n = (p2 + 2 * p1 + 2 * p0 + 2 * q0 + q1 + 4) >> 3
        p1n = (p2 + p1 + p0 + q0 + 2) >> 2
        p2n = (2 * p3 + 3 * p2 + p1 + p0 + q0 + 4) >> 3
    else:
        tc = TC0[min(bs, 3) - 1][index_a] + (ap < beta) + (aq < beta)
        delta = _clip3(-tc, tc, ((q0 - p0) * 4 + (p1 - q1) + 4) >> 3)
        p0n = min(255, max(0, p0 + delta))
        p1n, p2n = p1, p2
        if ap < beta:
            p1n = p1 + _clip3(-TC0[min(bs, 3) - 1][index_a],
                              TC0[min(bs, 3) - 1][index_a],
                              (p2 + ((p0 + q0 + 1) >> 1) - 2 * p1) >> 1)
    if bs == 4 and aq < beta and abs(p0 - q0) < ((alpha >> 2) + 2):
        q0n = (q2 + 2 * q1 + 2 * q0 + 2 * p0 + p1 + 4) >> 3
        q1n = (q2 + q1 + q0 + p0 + 2) >> 2
        q2n = (2 * q3 + 3 * q2 + q1 + q0 + p0 + 4) >> 3
    else:
        tc = TC0[min(bs, 3) - 1][index_a] + (ap < beta) + (aq < beta)
        delta = _clip3(-tc, tc, ((q0 - p0) * 4 + (p1 - q1) + 4) >> 3)
        q0n = min(255, max(0, q0 - delta))
        q1n, q2n = q1, q2
        if aq < beta:
            q1n = q1 + _clip3(-TC0[min(bs, 3) - 1][index_a],
                              TC0[min(bs, 3) - 1][index_a],
                              (q2 + ((p0 + q0 + 1) >> 1) - 2 * q1) >> 1)
    return p3, p2n, p1n, p0n, q0n, q1n, q2n, q3


def deblock_frame(y, qp):
    """对一帧亮度做去块滤波. 返回 (滤波后帧, bs_hist[0..4]).

    按 4x4 网格处理所有块边界, 先垂直边缘后水平边缘(与规范一致).
    """
    h, w = y.shape
    out = y.astype(np.int32).copy()
    index_a = min(51, max(0, qp))
    bs_hist = np.zeros(5, np.int64)
    # 垂直边缘: 列 x-1(p0) | x(q0)
    for x in range(4, w - 3, 4):
        for yy in range(0, h, 4):
            rows = slice(yy, min(yy + 4, h))
            p3, p2, p1, p0 = (out[rows, x - 4], out[rows, x - 3],
                              out[rows, x - 2], out[rows, x - 1])
            q0, q1, q2, q3 = (out[rows, x], out[rows, x + 1],
                              out[rows, x + 2], out[rows, x + 3])
            for i in range(p0.shape[0]):
                bs = derive_bs(int(p0[i]), int(q0[i]), int(p1[i]), int(q1[i]),
                               int(ALPHA[index_a]), int(BETA[index_a]))
                bs_hist[bs] += 1
                if bs == 0:
                    continue
                r = filter_line(int(p3[i]), int(p2[i]), int(p1[i]), int(p0[i]),
                                int(q0[i]), int(q1[i]), int(q2[i]), int(q3[i]),
                                bs, index_a)
                out[yy + i, x - 4:x + 4] = r
    # 水平边缘: 行 yy-1(p0) | yy(q0)
    for yy in range(4, h - 3, 4):
        for x in range(0, w, 4):
            cols = slice(x, min(x + 4, w))
            p3, p2, p1, p0 = (out[yy - 4, cols], out[yy - 3, cols],
                              out[yy - 2, cols], out[yy - 1, cols])
            q0, q1, q2, q3 = (out[yy, cols], out[yy + 1, cols],
                              out[yy + 2, cols], out[yy + 3, cols])
            for i in range(p0.shape[0]):
                bs = derive_bs(int(p0[i]), int(q0[i]), int(p1[i]), int(q1[i]),
                               int(ALPHA[index_a]), int(BETA[index_a]))
                bs_hist[bs] += 1
                if bs == 0:
                    continue
                r = filter_line(int(p3[i]), int(p2[i]), int(p1[i]), int(p0[i]),
                                int(q0[i]), int(q1[i]), int(q2[i]), int(q3[i]),
                                bs, index_a)
                out[yy - 4:yy + 4, x + i] = r
    return np.clip(out, 0, 255).astype(np.uint8), bs_hist
