# G.711 μ-law / A-law WASM 回环校验报告

生成时间（UTC）：2026-11-19T19:05:26.045Z

## 方法与口径

- 输入为 signed 16-bit PCM，量纲为 LSB；μ-law 与 A-law 均按 16-bit G.711 常用口径裁剪到 ±32635。
- 回环定义：PCM → 8-bit G.711 → PCM，全部编解码由 `dist/g711.wasm` 执行。
- 误差 `e[n] = loopback_pcm[n] - input_pcm[n]`；SNR = `10 log10(sum(x^2) / sum(e^2))`。
- 全零静音的信号功率为 0，因此 SNR 未定义；表中报告最大误差与输出直流。

## 全码域校验

- 已遍历全部 65536 个 16-bit PCM 值和全部 256 个 G.711 码字。
- μ-law：标量/批量/回环差异 0；PCM 回环幂等 65536/65536。
- A-law：标量/批量/回环差异 0；PCM 回环幂等 65536/65536。
- μ-law 的 3 个码字重写差异来自 `0x7F/0xFF` 双零码；解码后的 PCM 回环结果仍完全幂等。

## 信号量化误差与 SNR

| 信号 | 样本数 | μ-law 最大误差 | μ-law SNR | μ-law 输出直流 | A-law 最大误差 | A-law SNR | A-law 输出直流 |
|---|---:|---:|---:|---:|---:|---:|---:|
| 静音（400 个零） | 400 | 0 LSB | N/A | 0.000 LSB | 8 LSB | N/A | 8.000 LSB |
| 1 kHz 正弦（峰值 16384） | 8000 | 380 LSB | 33.835 dB | 0.000 LSB | 512 LSB | 32.974 dB | 2.000 LSB |
| 真实语音（FSDD） | 5148 | 502 LSB | 37.272 dB | -1.658 LSB | 505 LSB | 37.487 dB | 0.193 LSB |
| 小幅正弦（峰值 32） | 8000 | 1 LSB | 30.175 dB | 0.000 LSB | 8 LSB | 12.045 dB | 2.000 LSB |

说明：静音为 400 个 0；正弦为 1 kHz、峰值 16384 LSB、1 秒；真实语音为 8 kHz 单声道 FSDD `0_jackson_0.wav`，5148 个样本。

## 零附近量化台阶

| 规则 | 零电平 | 零码 | 最近负/正电平 | 首段正台阶 | 最近的负→正电平间距 |
|---|---:|---|---:|---:|---:|
| μ-law | 有 | 0x7f, 0xff | -8 / 8 | 8 LSB | 16 LSB |
| A-law | 无 | 无 | -8 / 8 | 16 LSB | 16 LSB |

- μ-law 有 `0x7F` 与 `0xFF` 两个零码，零两侧各有 8 LSB 台阶。
- A-law 没有精确零电平，零附近为 `-8 → +8`，首段台阶为 16 LSB；静音/零输入会回到 `+8 LSB`。

## 小信号直流与失真

- μ-law：峰值 ≤ 32 LSB 时，最大绝对误差 4 LSB，最大输出直流 0 LSB；半首段台阶上限 4 LSB，检查通过。
- A-law：峰值 ≤ 32 LSB 时，最大绝对误差 8 LSB，最大输出直流 2 LSB；半首段台阶上限 8 LSB，检查通过。
- 结论：未见由正负编码规则引入的额外直流偏置或非对称失真。A-law 在幅度小于 8 LSB 时相对 SNR 明显下降，这是其 16 LSB 零区死台阶造成的固有量化，不是实现偏置。

## 正负样本对称性

| 规则 | 测试幅值对 | 码字 XOR 异常 | 解码幅值差 | 回环幅值差 | 码本符号对异常 | 结果 |
|---|---:|---:|---:|---:|---:|---|
| μ-law | 32767 | 0 | 0 LSB | 0 LSB | 0 | 通过 |
| A-law | 32767 | 0 | 0 LSB | 0 LSB | 0 | 通过 |

- 对每个 `m ∈ [1, 32767]`，正负码字满足 `code(+m) XOR code(-m) = 0x80`，解码后的绝对值完全相等。
- A-law 的零输入使用正侧 `0xD5 → +8`；负的最小样本使用 `0x55 → -8`，两侧对称但没有零电平。

## 输出文件

- `reports/loopback-sample-differences.csv`：所有测试信号按样本序号列出输入、码字、回环 PCM 与差值。
- `reports/loopback-metrics.json`：最大误差、RMSE、SNR、直流偏移等完整指标。
- `reports/full-domain-validation.json`：65,536 PCM 值与 256 码字的全量校验明细。
- `reports/quantization-near-zero.csv`：零附近电平、码字与台阶。
- `reports/small-signal-metrics.csv`：小幅正弦幅度扫描。
- `reports/symmetry.json`、`reports/symmetry-edges.csv`：正负样本对称性结果。
