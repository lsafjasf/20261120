# LED 墙快门设置预检工具

面向虚拟拍摄棚：导入显示均匀灰阶的短测试片与每条的曝光 / 帧率 / 触发相位
记录，按行（列）统计亮度，检测扫描条带的宽度、移动方向与跨帧周期，生成
快门设置风险表，并为每项附原始帧与行亮度剖面图。

## 用法

```bash
# 合成演示数据集（不同扫描方向 + 对照样例）
python3 -m ledwall_precheck.main synth --data data

# 分析并生成报告
python3 -m ledwall_precheck.main analyze --data data --out output

# 一步完成
python3 -m ledwall_precheck.main demo --data data --out output

# 自检
python3 -m unittest discover -s tests -v
```

## 数据格式

每个测试条一个目录：`<take>/frames/frame_000.png ...` 加 `<take>/log.json`
（`fps`、`exposure_s`、`trigger_phase_s`）。`shoot_log.json` 汇总所有条。

## 检测原理

- 每帧按行/列求平均亮度，时间高通去除静态结构（渐变、固定条纹）。
- 候选暗带需覆盖大部分列宽，借此排除真实物体运动。
- 跨帧跟踪条带质心，线性拟合得漂移速度 → 方向与跨帧周期。
- 帧率混叠导致多个候选扫描频率时保留歧义（剩余类 mod fps 表示），
  并给出补拍条件（改变帧率或曝光）以消歧。
