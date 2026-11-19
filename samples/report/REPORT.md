# VVC 参数集、切换与频谱连续性验证报告

本报告由 `tools/run_validation.py` 与 `tools/render_report.py` 生成，所有偏移均为 Annex B 字节流中的绝对字节偏移。

## 1. VPS / SPS / PPS 识别

样本：`/home/hj/userfeedback/uid4-0016/samples/streams/vps_sps_pps_multires.vvc`（JVET VVC FDIS conformance bitstream VPS_A_INTEL_3）。

| 参数集 | ID | 修订 | 起始码偏移 | 起始码 | NAL 偏移 | 前缀 | 出现次数 | 分辨率 | 位深 | 色度 |
|---|---:|---:|---:|---|---:|---:|---:|---|---:|---|
| VPS | 1 | 1 | 7 | 00000001 | 11 | 4B | 1 | - | - | - |
| SPS | 0 | 1 | 34 | 00000001 | 38 | 4B | 1 | 208x120 | 10 | 4:2:0 |
| PPS | 0 | 1 | 139 | 00000001 | 143 | 4B | 1 | - | - | - |
| SPS | 1 | 1 | 1336 | 00000001 | 1340 | 4B | 1 | 832x480 | 10 | 4:2:0 |
| PPS | 1 | 1 | 1454 | 00000001 | 1458 | 4B | 1 | - | - | - |

SPS 中的 `sps_pic_*_in_luma_samples` 与裁剪窗口共同给出显示分辨率；`sps_bitdepth_minus8` 给出位深；`sps_chroma_format_idc` 给出色度格式。VPS 本身不直接携带画面分辨率。

## 2. 双分辨率拼接与解码

码流：`/home/hj/userfeedback/uid4-0016/samples/streams/switch_low_high_low.vvc`，组成 `96x64/8-bit -> 192x128/10-bit -> 96x64/8-bit`。

拼接流中的参数集首次出现位置：

| 参数集 | ID | 修订 | 起始码偏移 | 起始码 | NAL 偏移 | 前缀 | 出现次数 | 分辨率 | 位深 | 色度 |
|---|---:|---:|---:|---|---:|---:|---:|---|---:|---|
| SPS | 0 | 1 | 0 | 00000001 | 4 | 4B | 2 | 96x64 | 8 | 4:2:0 |
| PPS | 0 | 1 | 242 | 00000001 | 246 | 4B | 2 | - | - | - |
| SPS | 0 | 2 | 937 | 00000001 | 941 | 4B | 1 | 192x128 | 10 | 4:2:0 |
| PPS | 0 | 2 | 1179 | 00000001 | 1183 | 4B | 1 | - | - | - |

SPS 参数集切换事件：

| 切换 | 流偏移 | 解码字节边界 | 切换后首帧 | 切换前 | 切换后 |
|---|---:|---:|---:|---|---|
| 1 | 937 | 110592 | 12 | 96x64 / 8-bit / 4:2:0 | 192x128 / 10-bit / 4:2:0 |
| 2 | 2046 | 995328 | 24 | 192x128 / 10-bit / 4:2:0 | 96x64 / 8-bit / 4:2:0 |

解码器：`VVdeC 3.2.1`。解码日志报告 `36` 帧，尺寸序列为 96x64@8b -> 192x128@10b -> 96x64@8b。

| 片段 | 帧数 | 解码输出偏移 | 字节数 | 与独立解码逐字节一致 |
|---|---:|---:|---:|---|
| low_1 | 12 | 0 | 110592 | 是 |
| high_1 | 12 | 110592 | 884736 | 是 |
| low_2 | 12 | 995328 | 110592 | 是 |
| trailing_bytes | 0 | 1105920 | 0 | 是 |

结果：期望 36 帧，实际 36 帧；输出 1105920 字节，期望 1105920 字节。`no_frame_loss=true`。两个切换点的前后片段均与各自独立解码结果完全一致，因此没有因参数集切换产生丢帧、错序或像素错乱。

## 3. 未知 NAL 保留与分布

探针码流：`/home/hj/userfeedback/uid4-0016/samples/streams/switch_with_unknown.vvc`。未知 NAL 按原始 NAL 单元（含 2 字节头）落盘。

| NAL 类型 | 名称 | 数量 | 总字节 | 首次偏移 | 原字节校验 |
|---:|---|---:|---:|---:|---|
| 26 | RSV_NVCL_26 | 1 | 27 | 0 | 一致 |
| 27 | RSV_NVCL_27 | 1 | 27 | 967 | 一致 |
| 29 | UNSPEC_29 | 1 | 23 | 2106 | 一致 |
| 31 | UNSPEC_31 | 1 | 23 | 3069 | 一致 |

`all_preserved=true`。保存目录：`samples/unknown_raw/`；含完整 SHA-256 与原始十六进制清单的机器报告见 `samples/report/validation.json`。

## 4. 窄带 / 宽带频繁切换

补充样本为 48 kHz、16-bit、单声道 PCM：`4.95` 秒内每 `0.45` 秒交替一次，共 `10` 次切换；每次切换使用 `120 ms` 升余弦交叉过渡。

| 指标 | 平滑样本 | 硬切换对照 |
|---|---:|---:|
| 高频段能量占比最大相邻帧步进 | 0.013344 | 0.090597 |
| 高频段幅度最大相邻帧步进 | 0.026903 | 0.199018 |
| 稳态窄带高频占比中位数 | 1.694e-09 | - |
| 稳态宽带高频占比中位数 | 0.150598 | - |
| 稳态低频能量波动 | 0.000389 dB | - |
| 削波样本数 | 0 | - |

平滑/硬切换的最大频谱步进比为 `0.1473`，峰值相邻样本差为 `0.126099` FS。`continuous_transition_pass=true`；高频段按交叉过渡逐步进入/退出，未观察到离散跳变或削波。

频谱曲线：`samples/audio/band_switch_spectrum.svg`；逐帧数据：`samples/audio/band_switch_spectrum.csv`。

## 5. 复现

```bash
python3 -m unittest discover -s tests -v
python3 tools/run_validation.py --vvdec /path/to/vvdecapp
python3 tools/render_report.py
```

完整机器可读结果：`samples/report/validation.json`。
