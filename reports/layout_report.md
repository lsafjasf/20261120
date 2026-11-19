# WebVTT 排版与帧率核对报告

- 素材：`fixtures/sample.vtt` / `fixtures/player.html`
- cue 总数：13；REGION：newsroll, stationary
- `::cue` 规则：5 条

## 1. script tag 帧率核对

- `<script type="text/vtt" data-fps>` 声明帧率：**25.0 fps**
- 由 10 个时间戳反推的实际帧率：**24.0 fps**
- 最大网格拟合误差：0.0 s；容差 ±0.05 fps
- 结论：**不一致**（声明 25 fps，但时间戳全部落在 24/30 fps 网格上）

## 2. 两种播放器宽度的排版结果

### 1280x720（字号 36.0 px，行高 38.376 px）

- cue 视口越界：0 处
- 几何重叠事件：0 组
- REGION 全部在视口内：True
- REGION 占视口面积：10.23%

### 640x360（字号 18.0 px，行高 19.188 px）

- cue 视口越界：0 处
- 几何重叠事件：0 组
- REGION 全部在视口内：True
- REGION 占视口面积：10.23%

## 3. 每行断点与显示宽度（1280×720）

### cue `cue1`
| # | 断点类型 | 字符偏移 | 显示宽度 px | 时间戳 tag s | 文本 |
|---|----------|----------|-------------|--------------|------|
| 1 | 硬换行 | 0 | 560.16 | - | Lower third title that must wrap |
| 2 | 软换行 | 33 | 573.48 | - | inside its explicit size box before |
| 3 | 软换行 | 69 | 320.04 | - | the next cue enters |

### cue `cue7`
| # | 断点类型 | 字符偏移 | 显示宽度 px | 时间戳 tag s | 文本 |
|---|----------|----------|-------------|--------------|------|
| 1 | 硬换行 | 0 | 376.56 | - | Ticker line one enters |

### cue `cue5`
| # | 断点类型 | 字符偏移 | 显示宽度 px | 时间戳 tag s | 文本 |
|---|----------|----------|-------------|--------------|------|
| 1 | 硬换行 | 0 | 316.08 | - | Stationary cue one |

### cue `cue6`
| # | 断点类型 | 字符偏移 | 显示宽度 px | 时间戳 tag s | 文本 |
|---|----------|----------|-------------|--------------|------|
| 1 | 硬换行 | 0 | 320.4 | - | Stationary cue two |

### cue `cue2`
| # | 断点类型 | 字符偏移 | 显示宽度 px | 时间戳 tag s | 文本 |
|---|----------|----------|-------------|--------------|------|
| 1 | 硬换行 | 0 | 498.96 | - | Second lower third overlaps |
| 2 | 软换行 | 28 | 577.08 | - | cue1 in time and shares the same |
| 3 | 软换行 | 61 | 123.12 | - | column |

### cue `cue8`
| # | 断点类型 | 字符偏移 | 显示宽度 px | 时间戳 tag s | 文本 |
|---|----------|----------|-------------|--------------|------|
| 1 | 硬换行 | 0 | 439.56 | - | Ticker line two pushes up |

### cue `cue3`
| # | 断点类型 | 字符偏移 | 显示宽度 px | 时间戳 tag s | 文本 |
|---|----------|----------|-------------|--------------|------|
| 1 | 硬换行 | 0 | 317.16 | - | Right top breaking |
| 2 | 软换行 | 19 | 365.76 | - | news banner with an |
| 3 | 软换行 | 39 | 393.48 | - | emphasized inline span |

### cue `cue4`
| # | 断点类型 | 字符偏移 | 显示宽度 px | 时间戳 tag s | 文本 |
|---|----------|----------|-------------|--------------|------|
| 1 | 硬换行 | 0 | 420.48 | - | Auto cue moved upward |
| 2 | 软换行 | 22 | 457.56 | - | while lower thirds occupy |
| 3 | 软换行 | 48 | 366.48 | - | the bottom-right slots |

### cue `cue9`
| # | 断点类型 | 字符偏移 | 显示宽度 px | 时间戳 tag s | 文本 |
|---|----------|----------|-------------|--------------|------|
| 1 | 硬换行 | 0 | 408.24 | - | Ticker line three starts |
| 2 | 软换行 | 25 | 131.4 | - | clipping |

### cue `cue10`
| # | 断点类型 | 字符偏移 | 显示宽度 px | 时间戳 tag s | 文本 |
|---|----------|----------|-------------|--------------|------|
| 1 | 硬换行 | 0 | 395.64 | - | Ticker line four keeps |
| 2 | 软换行 | 23 | 131.4 | - | clipping |

### cue `cue11`
| # | 断点类型 | 字符偏移 | 显示宽度 px | 时间戳 tag s | 文本 |
|---|----------|----------|-------------|--------------|------|
| 1 | 硬换行 | 0 | 511.2 | - | CJK boundary wrapping test: |
| 2 | 软换行 | 28 | 504.0 | - | 字幕排版引擎需要在全角字符 |
| 3 | 软换行 | 41 | 504.0 | - | 之间正确断行并且不遮挡主画 |
| 4 | 软换行 | 54 | 108.0 | - | 面区域 |

### cue `cue12`
| # | 断点类型 | 字符偏移 | 显示宽度 px | 时间戳 tag s | 文本 |
|---|----------|----------|-------------|--------------|------|
| 1 | 硬换行 | 0 | 508.68 | - | Timestamp tag test line word |
| 2 | 软换行 | 29 | 500.76 | 7.0 | one continues word two with |
| 3 | 软换行 | 57 | 482.76 | 8.0 | extra wrapping content here |

### cue `cue13`
| # | 断点类型 | 字符偏移 | 显示宽度 px | 时间戳 tag s | 文本 |
|---|----------|----------|-------------|--------------|------|
| 1 | 硬换行 | 0 | 644.28 | - | Fixed pixel width box gains wraps when narrow |

## 4. 跨宽度稳定性

- 窄宽度新增换行的 cue：cue13
- 层叠顺序两宽度一致：True
- 滚动方向两宽度一致：True
- 两宽度 REGION 均在视口内：True

## 5. 滚动 REGION 与层叠

### 1280x720

- 越界裁剪事件（滚动方向 up，旧行从 region 顶边裁出）：
  - t=4.000s region=newsroll cue=cue7 1 line(s) / 38.4px clipped above top
  - t=5.000s region=newsroll cue=cue9 1 line(s) / 38.4px clipped above top
  - t=5.000s region=newsroll cue=cue8 1 line(s) / 38.4px clipped above top
  - t=5.000s region=newsroll cue=cue7 1 line(s) / 38.4px clipped above top
  - t=6.000s region=newsroll cue=cue9 1 line(s) / 38.4px clipped above top
  - t=6.000s region=newsroll cue=cue8 1 line(s) / 38.4px clipped above top
  - …共 13 条，详见 JSON

### 640x360

- 越界裁剪事件（滚动方向 up，旧行从 region 顶边裁出）：
  - t=4.000s region=newsroll cue=cue7 1 line(s) / 19.2px clipped above top
  - t=5.000s region=newsroll cue=cue9 1 line(s) / 19.2px clipped above top
  - t=5.000s region=newsroll cue=cue8 1 line(s) / 19.2px clipped above top
  - t=5.000s region=newsroll cue=cue7 1 line(s) / 19.2px clipped above top
  - t=6.000s region=newsroll cue=cue9 1 line(s) / 19.2px clipped above top
  - t=6.000s region=newsroll cue=cue8 1 line(s) / 19.2px clipped above top
  - …共 13 条，详见 JSON

## 6. 时间重叠 cue 的层叠顺序

- `cue1` [0.0, 4.0] 与 cue7, cue5, cue6, cue2, cue8, cue3, cue4 时间重叠
- `cue7` [0.0, 8.0] 与 cue5, cue6, cue2, cue8, cue3, cue4, cue9, cue10, cue11, cue12, cue13 时间重叠
- `cue5` [0.5, 6.5] 与 cue6, cue2, cue8, cue3, cue4, cue9, cue10, cue11 时间重叠
- `cue6` [1.0, 5.0] 与 cue2, cue8, cue3, cue4, cue9 时间重叠
- `cue2` [2.0, 6.0] 与 cue8, cue3, cue4, cue9, cue10 时间重叠
- `cue8` [2.0, 8.0] 与 cue3, cue4, cue9, cue10, cue11, cue12, cue13 时间重叠

层叠规则：自由 cue 按激活顺序编号（小者先绘、大者覆盖）；REGION cue 按进入顺序固定编号，重排时不改变身份，保证层叠稳定。
