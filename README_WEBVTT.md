# WebVTT 排版 / REGION / 帧率核对

零第三方依赖的确定性 WebVTT 布局引擎，使用 Python 标准库实现，无需浏览器
或字体文件即可复现换行、层叠与 REGION 滚动。

## 运行

```bash
python3 -m vtt_layout fixtures/sample.vtt fixtures/player.html reports
python3 -m unittest tests.test_layout -v
```

产物：

- `reports/layout_report.json`：逐 cue、逐采样时刻的完整几何与断点数据
- `reports/layout_report.md`：人类可读的核对报告

## 模块

- `vtt_layout/parser.py`：WEBVTT/REGION/STYLE/NOTE、cue settings、内联 tag
- `vtt_layout/metrics.py`：字宽表、空格与 CJK 边界换行、时间戳 tag 映射
- `vtt_layout/engine.py`：cue 几何、自动让位、REGION 向上滚动与顶边裁剪
- `vtt_layout/framerate.py`：script tag 声明帧率 vs 时间戳反推帧率
- `vtt_layout/report.py` / `markdown.py`：JSON 与 Markdown 报告

## 判定口径

- 播放器宽度：1280×720 与 640×360，默认字号 5vh、行高 5.33vh
- 字宽：西文按字形类别给固定 em 进宽；CJK 与全角标点按 1.0 em
  （容器无 CJK 字体文件，此为排版惯例口径，报告数值可完全复现）
- 层叠：自由 cue 按激活顺序编号；REGION cue 按进入顺序固定编号
- REGION 滚动：仅 `scroll:up`，新 cue 从底部进入、旧 cue 从顶边裁出
- 帧率：对常见帧率网格做整数帧拟合，误差超过 1/4 帧即判定不匹配

## 关键结论

- 两种宽度下所有 cue 与 REGION 均在视口内，零几何重叠
- `newsroll`（scroll:up）滚动方向在两宽度一致，越界行从 region 顶边裁剪，
  REGION 合计仅占视口 10.23%，不遮挡主画面
- 固定像素盒子 `cue13` 在 1280 宽为 1 行、在 640 宽被钳制为 2 行
- 层叠顺序与滚动方向跨宽度稳定
- `<script data-fps="25">` 声明 25 fps，时间戳反推为 24 fps，结论为不一致
  （测试中同时提供声明与反推一致的对照用例）
