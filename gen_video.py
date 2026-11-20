"""生成合成测试序列: 含已知位置的真实边缘、平滑渐变与噪声, 输出 YUV420."""
import numpy as np

W, H, N = 352, 288, 30
rng = np.random.default_rng(42)

# 真实边缘位置(列/行), 用于后续验证"真实边缘未被平滑"
TRUE_EDGE_COLS = [88, 200]
TRUE_EDGE_ROWS = [144]


def make_frame(t):
    y = np.zeros((H, W), np.float64)
    gx = np.linspace(40, 120, W)[None, :]
    gy = np.linspace(20, 60, H)[:, None]
    y += gx + gy
    y[:, TRUE_EDGE_COLS[0]:] += 100
    y[:, TRUE_EDGE_COLS[1]:] -= 60
    y[TRUE_EDGE_ROWS[0]:, :] += 40
    bx, by = 30 + t, 40 + t // 2
    y[by:by + 32, bx:bx + 32] += 60
    tex = 15 * np.sin(np.arange(W)[None, :] * 0.9) * np.sin(np.arange(H)[:, None] * 0.7)
    y[180:260, 220:330] += tex[180:260, 220:330]
    y += rng.normal(0, 2.0, (H, W))
    return np.clip(y, 0, 255)


def main():
    with open("test.yuv", "wb") as f:
        for t in range(N):
            y = make_frame(t)
            u = np.full((H // 2, W // 2), 128.0) + rng.normal(0, 1.0, (H // 2, W // 2))
            v = np.full((H // 2, W // 2), 128.0) + rng.normal(0, 1.0, (H // 2, W // 2))
            f.write(y.astype(np.uint8).tobytes())
            f.write(np.clip(u, 0, 255).astype(np.uint8).tobytes())
            f.write(np.clip(v, 0, 255).astype(np.uint8).tobytes())
    print("wrote test.yuv", W, H, N)


if __name__ == "__main__":
    main()
