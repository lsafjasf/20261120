# HRTF 距离衰减、空气吸收与混响预延迟实验报告

零依赖实现（仅 Node.js 内置能力 + 浏览器原生 Web Audio API），分为离线数值实验
（`src/`）和浏览器听感验证（`web/`）两部分。

## 1. 模型与实现

- **距离衰减（反平方律）**：`L(d) = -20 log10(d / d_ref)`，`d_ref = 1 m`，
  见 `src/physics.js:distanceGainDb`。距离每加倍约 −6.02 dB。
- **空气吸收（ISO 9613-1:1993）**：完整实现氧/氮分子双弛豫公式
  （`src/physics.js:airAbsorptionCoefficient`），默认条件 20 °C、101.325 kPa、
  50 %RH，输出 dB/m。空气损失 = `alpha(f) × d`，随频率和距离单调增大。
- **空气吸收滤波器**：8 个 RBJ high-shelf biquad（250 Hz–16 kHz 对数锚点）
  级联，对 ISO 目标曲线做线性最小二乘拟合（`src/airFilter.js:fitAirAbsorptionFilter`），
  得到可实时运行的最小相位 IIR；浏览器中一一对应到 8 个 `BiquadFilterNode`。
- **HRTF**：浏览器使用 `PannerNode`（`panningModel = "HRTF"`，浏览器内置实测
  HRIR 数据）；离线无实测 HRIR，使用通用自由场耳廓幅度模型
  （`src/airFilter.js:genericHrtfDb`：2.7 kHz 增益峰、9 kHz 陷波），并在报告中标注。
- **混响预延迟**：`predelay = 20 ms 基础 + d/c`（`c = 343.2 m/s`）。
  离线用带预延迟间隙的指数衰减合成 RIR 做 FFT 卷积；浏览器用带同样预延迟的
  `ConvolverNode`。
- **响度**：时域 RMS（dBFS）与 IEC 61672-1 A 计权 RMS 两套指标。

## 2. 运行方式

```bash
node src/experiment.js   # 离线实验，产物写入 artifacts/results/
node src/ranking.js      # 排名一致性分析（合成演示数据）
node test/verify.js      # 19 项数值/物理正确性断言
# 听感验证：浏览器直接打开 web/index.html（需用户点击“开始播放”）
```

## 3. 空气吸收系数表（ISO 9613-1，20 °C / 50 %RH）

完整数据见 `artifacts/results/air_absorption_coefficients.csv`：

| 频率 (Hz) | alpha (dB/m) | 1 m 损失 (dB) | 8 m 损失 (dB) | 32 m 损失 (dB) |
|---:|---:|---:|---:|---:|
| 31.5 | 3.14e-5 | 0.000 | 0.000 | 0.001 |
| 125 | 4.40e-4 | 0.000 | 0.004 | 0.014 |
| 500 | 2.73e-3 | 0.003 | 0.022 | 0.087 |
| 1000 | 4.66e-3 | 0.005 | 0.037 | 0.149 |
| 2000 | 9.89e-3 | 0.010 | 0.079 | 0.316 |
| 4000 | 2.97e-2 | 0.030 | 0.237 | 0.949 |
| 8000 | 1.05e-1 | 0.105 | 0.842 | 3.369 |
| 12500 | 2.38e-1 | 0.238 | 1.901 | 7.604 |
| 16000 | 3.65e-1 | 0.365 | 2.916 | 11.665 |

低频几乎无吸收，8 kHz 以上快速上升，16 kHz 在 32 m 处损失达 11.7 dB，
与标准曲线趋势一致（`artifacts/results/air_absorption_curves.svg`）。

## 4. 近/远声源响度差

数据见 `artifacts/results/loudness_deltas.csv` 与 `loudness_comparison.csv`。

| 距离 | 仅距离律 Δ (dB) | 加空气吸收 Δ (dB) | A 计权全链路 Δ (dB) | 理论距离律 (dB) |
|---:|---:|---:|---:|---:|
| 2 m | −6.02 | −6.06 | −6.05 | −6.02 |
| 4 m | −12.05 | −12.15 | −12.13 | −12.04 |
| 8 m | −18.07 | −18.29 | −18.26 | −18.06 |
| 16 m | −24.09 | −24.48 | −24.47 | −24.08 |
| 32 m | −30.11 | −30.72 | −30.81 | −30.10 |

结论：距离加倍响度差严格逼近 −6 dB；32 m 相对 1 m 总差 −30.8 dB（A 计权），
其中空气吸收在距离律之外额外贡献约 0.7 dB，且随距离增大而加剧。

## 5. 高频衰减与拟合精度确认

- 滤波器对 ISO 目标的最大拟合误差：1 m 为 0.023 dB、8 m 为 0.182 dB、
  32 m 为 0.777 dB（`filter_fit_summary.csv`），全频带 < 1 dB。
- Welch 实测频谱（`frequency_response.csv` 的 `total_response_db`）与
  “距离律 + 空气 + HRTF”理论和一致：32 m、16 kHz 实测约 −44 dB
  （≈ −30.1 dB 距离律 −11.5 dB 空气 −1.3 dB HRTF）。
- 曲线图：
  - `frequency_response_curves.svg`：1–32 m 总频率响应随距离整体下移且高频更陡。
  - `response_components_8m.svg`：8 m 处各分量分解与拟合贴合度。
  - `air_absorption_curves.svg`：空气吸收随频率/距离的变化。
- 听感 WAV：`distance_01m.wav` … `distance_32m.wav`（48 kHz / 16-bit，
  含直达声 + 预延迟混响）。

## 6. 排名不一致片段（合成演示数据）

仓库不含任何真实主观评测数据，因此 `src/ranking.js` 生成 10 段、每段 2.5 s
（总时长 25 s）的**合成**劣化片段（低通截止 + 噪声底逐级恶化），并显式在
`ranking_summary.json` 标注 `dataset: "synthetic-demo"`。客观分（SNR + 带宽）
与主观 MOS 排名对比结果：

| 片段 | 起止时间 (s) | 客观排名 | 主观排名 | 排名差 |
|---|---|---:|---:|---:|
| seg-04 | 7.50 – 10.00 | 4 | 8 | +4 |
| seg-07 | 15.00 – 17.50 | 7 | 4 | −3 |
| seg-08 | 17.50 – 20.00 | 8 | 7 | −1 |

Spearman 等级相关系数 rho = 0.8424。低分一致性审计：客观最差三段
（seg-08/09/10）与主观最差三段（seg-04/09/10）重合 2 段；seg-04 的主观
低分与“窄带宽导致听感发闷”的主观判断一致，seg-09/seg-10 在两套评分中
均为最低，低分片段与主观判断一致。

## 7. 产物清单

- `artifacts/results/air_absorption_coefficients.csv`：系数随频率变化表。
- `artifacts/results/frequency_response.csv`：逐距离/逐频率响应与各分量。
- `artifacts/results/loudness_comparison.csv`、`loudness_deltas.csv`：响度与差值。
- `artifacts/results/filter_fit_summary.csv`、`summary.json`：拟合误差与预延迟。
- `artifacts/results/*.svg`：频率响应/空气吸收曲线。
- `artifacts/results/distance_*.wav`：听感渲染文件。
- `artifacts/results/ranking/`：片段 WAV、`segments.csv`、`ranking_mismatches.csv`。
- `web/index.html` + `web/audio-demo.js`：浏览器 Web Audio 实时听感验证。
