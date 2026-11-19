# TTML/IMSC → CSS 映射与 SRT 重传验证工作区

## 目录

- `samples/` — IMSC 样例（多语言横排、竖排 + ruby）
- `lib/` — TTML 解析、region/style/tts → CSS 映射、HTML 映射与浏览器测量
- `srt/` — SRT HS/KM/ARQ 离散事件模拟器（移植自同批 SRT 任务）
- `reports/` — 生成的 CSS、差异清单、对比报告、重传报告
- `debug/` — 排查过程中的临时脚本（非交付主路径）

## 复现步骤

```sh
npm install                 # 安装 imsc（puppeteer 复用宿主 Chrome 157）
node make-reports.js        # 生成 CSS、差异 JSON、映射报告
node run-compare.js         # Chrome 实测 imscJS vs CSS 映射几何
node make-compare-report.js # 生成对比 Markdown
python3 run_retrans.py      # SRT 丢包 + KMRSP 丢弃重传场景
```

## 结论摘要

- 两个样例在 4px 容差内全部通过：行高、首行位置、竖排基线、ruby 注音位置。
- 竖排基线（块向 x 轴）实测偏移 2px；ruby rt 综合位置误差 2.83px、gap 差 0。
- CSS 完全无法表达：`tts:showBackground`、`tts:multiRowAlign`、`tts:linePadding`；
  `tts:rubyReserve`、`tts:fontShear`、`tts:textOutline`、`tts:glyphOrientation`
  只能模拟；ruby/bidi/whitespace 等为部分映射。
- SRT 场景：强制丢弃数据包 3/7/12 与一个 KMRSP，重传后两侧 `km_ok=true`，
  投递序号 0..23 连续、无重复、无已 ACK 包重投。
