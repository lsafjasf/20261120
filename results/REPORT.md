# AV1 Film Grain Synthesis 实验报告

测试序列：testsrc2+时域噪声，320x240 @24fps，48 帧，yuv420p 8-bit。
编码：libaom-av1（cpu-used 8, crf 30, g 48），`-denoise-noise-level` = 8 / 20 / 40 三档（enc/grain_l{8,20,40}.ivf）。
合成器：scripts/film_grain.py，按 AV1 spec 8.8.5.3 移植 dav1d C 参考实现（LFSR + 高斯 LUT + AR 滤波 + scaling LUT + 32x32 块 overlap 混合）。

## 1. film_grain_params 读取与校验
- 每帧参数经 ffprobe `-filmgrain 0` side data 导出（results/params_l*.json），含 seed、y/uv points、ar_coeffs、scaling_shift、grain_scale_shift、overlap_flag 等。
- 例：l20 第 0 帧 seed=7391，11 个 luma 点、9/10 个 chroma 点，ar_coeff_lag=3，scaling_shift=11。
- Python 合成器输出与 dav1d 解码器自带合成（grain on）**逐位一致**（三档各 48 帧，0 个不匹配样本）。

## 2. 开/关合成逐帧差异（results/diff_on_off.json）
| 档位 | Y std | Y MAD | U std | V std |
|---|---|---|---|---|
| l8  | 0.79 | 0.54 | 0.76 | 0.76 |
| l20 | 2.22 | 1.74 | 2.14 | 2.16 |
| l40 | 2.95 | 2.33 | 3.00 | 3.01 |

噪声为零均值、强度随档位单调上升，色度与亮度噪声量级相当。

## 3. 三参数调节 + 可复现性（同 seed 两次合成 sha256 完全一致）
| 配置 | Y 噪声 std | U 噪声 std | 可复现 | 耗时 ms/帧 |
|---|---|---|---|---|
| intensity x0.5 / x1.0 / x1.5 | 1.15 / 2.25 / 3.36 | 1.11 / 2.16 / 3.23 | 是 | ~101-109 |
| granularity shift 0 / 1 / 2 | 2.25 / 1.15 / 0.62 | 2.16 / 1.11 / 0.60 | 是 | ~98-150 |
| chroma x0.0 / x1.0 / x2.0 | 2.25（不变） | 0.00 / 2.16 / 4.00 | 是 | ~113-133 |

- 强度（scaling 点值）线性控制噪声幅度；粒度（grain_scale_shift）每 +1 幅度约减半；色度噪声独立可调且不影响亮度。
- 9 个配置各自重复两次合成，输出字节级 sha256 完全相同 → 合成对 (params, seed) 完全确定。

## 4. 各参数档位计算耗时（纯 Python 参考实现，320x240）
- l8: 75.9 ms/帧；l20: 82.4 ms/帧；l40: 101.6 ms/帧（AR 系数/点数越多越慢）。

## 5. 不同 seed 的噪声统计分布（平坦帧，l20 参数）
- 6 个 seed（7391/1/2/12345/43690/65535）：均值 |m|<=0.07，std 2.23-2.31，偏度 |s|<=0.09，峰度 2.86-3.14（≈高斯）。
- 两两 KS 统计量 0.011-0.024 → 不同 seed 只改变噪声实现，不改变其统计分布。

## 6. 坏帧隐藏波形相关系数下限
策略：丢失帧 k 用前一帧复制隐藏（A），或前一帧去噪帧 + 用丢失帧自身 seed 重合成 grain（B）。
| 丢失帧 | r(A) | r(B) | MSE(A) | MSE(B) |
|---|---|---|---|---|
| 10 | 0.9690 | 0.9705 | 200.6 | 190.6 |
| 20 | 0.9757 | 0.9772 | 159.5 | 149.4 |
| 30 | 0.9714 | 0.9728 | 187.1 | 178.0 |

- 波形（Pearson）相关系数下限：A=0.9690，B=0.9705。
- B 在所有丢失帧上 r 更高、MSE 更低 → 按 seed 重合成 grain 的隐藏策略未引入额外失真（噪声统计与真实帧一致）。
