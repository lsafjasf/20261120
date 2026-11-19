# 融合画质评分报告

数据集：`synthetic-controlled-degradation`，10 个 2 秒片段（640x360@24fps，每片段 48 帧）。

## 1. 融合评分方案

每个候选帧与同序号参考帧做全参考比较，得到三类分量：

- 视觉信息保真 `fidelity`：亮度 SSIM(0.50)、色度 SSIM(0.20)、归一化 PSNR(0.30) 加权。
- 细节保持 `detailRetention`：Sobel 梯度能量保持率(0.55) 与梯度空间相关性(0.45) 加权；细节损失 `detailLoss = 1 - detailRetention`。
- 运动加权输出：运动指数由参考帧帧间平均绝对差得到，池化权重 `w = 1 + motionIndex`；
  运动保真 `motionFidelity` 为候选保留的参考时域变化比例（单向，只惩罚运动丢失，噪声归入保真分量）。

分段质量指数 `q = 0.45·fidelity + 0.35·detail + 0.20·motion`，
融合分数 `fusedScore = 100 · q^4.0`。

## 2. 分量权重与分数区间

| 分量 | 权重 | 分量区间 | 含义 |
| --- | --- | --- | --- |
| fidelity（视觉信息保真） | 0.45 | [0, 1] | 亮度/色度结构与像素误差 |
| detail（细节保持） | 0.35 | [0, 1] | 边缘能量与位置保持，损失为 1-该值 |
| motion（运动保真） | 0.20 | [0, 1] | 时域运动变化的保留比例 |
| 融合质量指数 q | 1.00 | [0, 1] | 三分量加权和 |
| fusedScore | - | [0, 100] | q 的 4.0 次幂映射 |

子项权重：保真内部 lumaSSIM 0.50 / chromaSSIM 0.20 / PSNR 0.30；
细节内部 gradientEnergy 0.55 / gradientCorrelation 0.45。

### 分段（band）区间

| band | 分数区间 |
| --- | --- |
| excellent | [85, 100] |
| good | [65, 85] |
| fair | [50, 65] |
| poor | [35, 50] |
| bad | [0, 35] |

## 3. 分段分数（按融合排名）

| 排名 | 片段 | 时间(s) | 失真类型 | fusedScore | band | fidelity | detailLoss | motionFid | 参考分 | MOS |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | clip-01 | 0-2 | pristine | 100.00 | excellent | 1.000 | 0.000 | 1.000 | 100.00 | 4.6 |
| 2 | clip-05 | 8-10 | h264-crf32 | 72.93 | good | 0.918 | 0.065 | 0.917 | 97.55 | 3.4 |
| 3 | clip-02 | 2-4 | noise-light | 71.83 | good | 0.828 | 0.006 | 1.000 | 84.45 | 4.1 |
| 4 | clip-03 | 4-6 | blur-light | 55.84 | fair | 0.918 | 0.232 | 0.912 | 98.40 | 3.8 |
| 5 | clip-07 | 12-14 | h264-crf40 | 43.02 | poor | 0.842 | 0.220 | 0.791 | 94.62 | 3.1 |
| 6 | clip-06 | 10-12 | blur-medium | 35.32 | poor | 0.853 | 0.383 | 0.856 | 96.24 | 2.7 |
| 7 | clip-08 | 14-16 | noise-heavy | 26.23 | bad | 0.406 | 0.049 | 1.000 | 40.21 | 2.4 |
| 8 | clip-04 | 6-8 | downscale-2x | 17.96 | bad | 0.760 | 0.597 | 0.839 | 91.28 | 2.8 |
| 9 | clip-09 | 16-18 | downscale-blur | 16.44 | bad | 0.740 | 0.588 | 0.797 | 89.46 | 1.9 |
| 10 | clip-10 | 18-20 | blur-noise-extreme | 8.81 | bad | 0.350 | 0.464 | 1.000 | 37.05 | 1.3 |

## 4. 与参考实现的排名一致性

| 指标 | 数值 |
| --- | --- |
| Spearman（融合 vs 参考） | 0.7212 |
| Kendall τ（融合 vs 参考） | 0.6000 |
| Spearman（融合 vs 主观） | 0.9273 |
| Spearman（参考 vs 主观） | 0.6848 |

显著不一致阈值：|rankDelta| >= 2；rankDelta = 融合排名 - 参考排名，负值表示融合排名更靠前。

| 片段 | 起止时间(s) | 融合排名 | 参考排名 | rankDelta | 失真类型 | 原因 |
| --- | --- | --- | --- | --- | --- | --- |
| clip-02 | 2-4 | 3 | 8 | -5 | noise-light | 噪声不破坏边缘与运动，luma SSIM 对颗粒过度惩罚；融合分因细节/运动保留完好而排名更高 |
| clip-03 | 4-6 | 4 | 2 | +2 | blur-light | 模糊/降采样摧毁高频细节，SSIM 在平坦区域仍偏高，细节分量大幅下降导致融合排名更低 |
| clip-06 | 10-12 | 6 | 4 | +2 | blur-medium | 模糊/降采样摧毁高频细节，SSIM 在平坦区域仍偏高，细节分量大幅下降导致融合排名更低 |
| clip-08 | 14-16 | 7 | 9 | -2 | noise-heavy | 噪声不破坏边缘与运动，luma SSIM 对颗粒过度惩罚；融合分因细节/运动保留完好而排名更高 |
| clip-04 | 6-8 | 8 | 6 | +2 | downscale-2x | 模糊/降采样摧毁高频细节，SSIM 在平坦区域仍偏高，细节分量大幅下降导致融合排名更低 |
| clip-09 | 16-18 | 9 | 7 | +2 | downscale-blur | 模糊/降采样摧毁高频细节，SSIM 在平坦区域仍偏高，细节分量大幅下降导致融合排名更低 |

排名不一致片段起止时间清单：

- clip-02（noise-light）：2s–4s，rankDelta=-5
- clip-03（blur-light）：4s–6s，rankDelta=+2
- clip-06（blur-medium）：10s–12s，rankDelta=+2
- clip-08（noise-heavy）：14s–16s，rankDelta=-2
- clip-04（downscale-2x）：6s–8s，rankDelta=+2
- clip-09（downscale-blur）：16s–18s，rankDelta=+2

## 5. 低分片段与主观判断核对

阈值：fusedScore < 36，主观 MOS < 3。

- 融合低分集合：clip-04, clip-06, clip-08, clip-09, clip-10
- 主观低分集合：clip-04, clip-06, clip-08, clip-09, clip-10
- 集合完全一致：True（precision=1, recall=1, F1=1）

## 6. Key status 播放器行为对照

| key status | 播放行为 | 播放器状态 | 暂停 | 报错 | 动作 |
| --- | --- | --- | --- | --- | --- |
| usable | continue | playing | False | False | keep-playing |
| expired | pause | paused-license-renewal | True | False | renew-license |
| output-restricted | pause | error-output-restricted | True | True | show-hdcp-error |

