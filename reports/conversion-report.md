# Equirect → Cube Map 验证报告

生成时间：2026-11-19T17:55:55.115Z

## 转换参数

| 项目 | 值 |
| --- | --- |
| 源图 | 2048 x 1024 equirectangular |
| 输出 | 6 × 512 x 512 |
| 采样 | bilinear, longitude-wrap, polar clamp |
| 接缝策略 | direction-matched edge duplication + eight-corner synchronization |
| 方向策略 | continuous unit direction vectors; no Euler interpolation across faces |
| 转换耗时 | 3940.962 ms |

## 接缝检查

**PASS: all seams are exact**

- 12 条共享边，共 18,432 次通道比较
- 最大单通道差值：0
- 最大亮度差：0
- 平均绝对通道差：0
- 不一致像素：0
- 六面接缝像素差：0（严格为零）

## 重投影误差

- 最大像素误差：5.684e-14 px
- 平均像素误差：4.681e-15 px
- 最大方向角误差：0.000e+0°

## 旋转更新关系

`Δθ = Ω / f`，其中 `Ω` 为角速度，`f` 为更新频率。基准 `Ω = 30°/s`，10 秒确定性时钟：

| 更新频率 | 每帧角度 | 预期更新 | 实际更新 | 掉帧 |
| --- | --- | --- | --- | --- |
| 60 Hz | 0.500000° | 600 | 600 | 0 |
| 90 Hz | 0.333333° | 900 | 900 | 0 |
| 120 Hz | 0.250000° | 1200 | 1200 | 0 |

**PASS: deterministic rotation ticks have no dropped updates**
