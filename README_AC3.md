# Dolby Digital (AC-3) 比特流解析器

纯 C++17 实现，无第三方依赖。解析 ATSC A/52（Dolby Digital / AC-3）
比特流的同步帧、`bsid`、`frmsizecod`，按标准表换算帧长，并对每帧做
CRC1 校验。CRC 失败或头部非法时标注具体字节偏移后继续重同步，不中断解析。

## 文件

- `ac3.hpp`：A/52 帧长标准表（38 个 `frmsizecod` × 3 个采样率）、
  MSB-first 位读取器、CRC-16（poly 0x8005，init 0）、同步头/BSI 解析
- `ac3_parser.cpp`：命令行解析器，扫描同步字、重同步、统计与 CSV 映射表
- `make_sample.cpp`：确定性样例生成器（不使用随机数，输出字节完全可复现）
- `table_check.cpp`：用标准公式逐格校验帧长表（114 个条目）

## 构建与复现

```sh
g++ -std=c++17 -O2 -Wall -Wextra -Werror ac3_parser.cpp -o ac3_parser
g++ -std=c++17 -O2 -Wall -Wextra -Werror make_sample.cpp -o make_sample
g++ -std=c++17 -O2 -Wall -Wextra -Werror table_check.cpp -o table_check

./table_check            # 校验标准帧长表
./make_sample sample.ac3 # 生成固定输入样例
./ac3_parser sample.ac3 frame_map.csv
```

## AC-3 头部与字段

- 同步字：2 字节 `0x0B77`（大端）
- CRC1：2 字节，覆盖整帧前 5/8 字节，CRC 字段自身按 0 参与计算
- 第 5 字节高 2 位 `fscod`：0=48 kHz，1=44.1 kHz，2=32 kHz
- 第 5 字节低 6 位 `frmsizecod`：0..37 为标准定义值，38..63 非法
- BSI 起始 5 位 `bsid`：标准 AC-3 为 8，0..8 均可被标准解码器接受
- `acmod`（3 位）+ `lfeon`（1 位）决定声道数，例如 `acmod=7` 且
  `lfeon=1` 即 3/2 + LFE 的 5.1（6 声道）

帧长换算（word = 2 字节）：

```
words = bitrate × 1536 / (sample_rate × 16)   # 向下取整
# 奇数 frmsizecod 为半码率编码，44.1 kHz 列额外 +1 word
bytes = words × 2
```

半码率（奇数代码）每帧时长为 64 ms（3072 个采样），其余为 32 ms（1536）。

## 固定输入样例布局

`make_sample` 生成 15408 字节，内容完全确定：

- 12 个 48 kHz、`bsid=8` 的有效帧，2.0（`frmsizecod=20`，192 kbps，
  768 B）与 5.1（`frmsizecod=30`，448 kbps，1792 B）严格交替
- 第 6 帧（序号 5，偏移 `0x1700`）CRC 覆盖区内 1 字节被异或 `0xFF`，
  造成且仅造成该帧 CRC1 失败
- 帧 7 之后偏移 `0x2800` 处插入 8 字节假同步：`0B 77 00 00 3F 00 40 00`，
  其 `frmsizecod=63` 超出标准表，计为 1 次帧长换算错误/漏帧
- 文件尾部偏移 `0x3c08` 处放置一个只写了 40/768 字节的截断帧

## 预期结果

```
完整解析帧数      : 12
CRC1 失败帧数     : 1        # 序号 5，偏移 0x1700
漏帧总数          : 2        # 帧长换算错误 1 + 截断 1
帧长换算错误帧数  : 1
平均码率          : 320000 bps   # 标称均值与按时长计算一致
峰值码率          : 448000 bps
```

## 帧序号 → 字节偏移映射表

解析器同时把映射写入 CSV（默认 `frame_map.csv`），供人工定位错误帧：

```
frame_index,byte_offset,offset_hex,size_bytes,channels,sample_rate_hz,bitrate_bps,crc1_ok,half_rate
0,0,0x0,768,2,48000,192000,1,0
1,768,0x300,1792,6,48000,448000,1,0
...
5,5888,0x1700,1792,6,48000,448000,0,0   # crc1_ok=0 即问题帧
```

人工核对可用 `xxd -s 0x1700 -l 32 sample.ac3` 直接跳转到失败帧。

## 码率统计口径

- 峰值码率：所有完整帧标称码率的最大值
- 平均码率（标称均值）：各帧标称码率的算术平均
- 平均码率（按时长）：`有效帧总字节 × 8 / 有效帧总时长`，
  用于检查帧长表换算是否自洽；标准码流下两者相等

## CRC 实现的独立验证

CRC-16 参数与 CRC-16/BUYPASS 相同（poly=0x8005、init=0、MSB-first、
无反射、无最终异或），对 ASCII `123456789` 的 check value 为 `0xFEE8`。
仓库中的解析结果已用独立 Python 实现交叉核对，帧偏移与失败帧完全一致。
