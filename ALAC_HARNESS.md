# ALAC magic cookie / packet 边界与无损校验工具

纯 Rust 实现的 ALAC（Apple Lossless）样例生成、cookie 解析、逐包压缩头
校验与解码核对工具。解码使用 Symphonia（纯 Rust），并以 ffmpeg 作为独立
第二解码器做跨实现交叉验证。

## 功能与任务对应

1. **解码前解析 magic cookie**（`src/cookie.rs`）
   - 读取 24/48 字节 cookie 中的 `frameLength`、`bitDepth`、声道数、
     采样率、`maxFrameBytes` 等字段。
   - 48 字节时解析 `chan` 扩展里的 CoreAudio 声道布局标签（mono/stereo/
     3.0/4.0/5.0/5.1/6.1/7.1），并校验布局声道数与 cookie 声明一致。
   - 校验版本、保留位、位深范围、frameLength 为合理的 2 的幂等约束。

2. **独立 MP4 box 遍历**（`src/mp4.rs`）
   - 不依赖解码框架，自行遍历 `moov/trak/mdia/minf/stbl`，提取 `stsd`
     内嵌套 `alac` 原子中的 cookie，以及 `stsz`/`stco`/`co64`/`stsc`/
     `stts` 和 `mdat` 区间。
   - 将 chunk/采样表展开为每个 packet 的 `(offset, size)`，并确认每个
     packet 完整落在某个 `mdat` 原子内。

3. **逐包压缩头校验**（`src/packet.rs` + `src/bitreader.rs`）
   - 按 MSB 优先解析元素头：`element_tag(3)`、`instance_tag(4)`、
     `partial_frame(1)`、`shift_code(2)`、`uncompressed(1)`，部分帧再读
     32 位实际样本数，压缩元素再读 mid/side 参数。
   - 未压缩元素按 `bitDepth` 跳过原始样本后继续遍历后续元素；压缩元素
     的熵编码载荷由 Symphonia 完整解码负责，结构侧校验第一个元素头与
     终止 `END(0b111)` 标记，并做越界检查。
   - 校验 shift 不超过位深、部分帧样本数小于 frameLength、元素覆盖声道数
     与 cookie 一致等。

4. **逐 packet 输出实际解码样本数并做累计校验**（`src/decoder.rs`、
   `src/checks.rs`）
   - Symphonia 逐 packet 解码，记录每个 packet 实际产出的帧数。
   - 三方对照：自解析压缩头样本数 == Symphonia 解码帧数 == demuxer 时长；
     累计样本数再与 `stts` 总帧数核对，确认 packet 边界与 cookie 的
     `frameLength` 完全一致（仅末包允许部分帧）。

5. **位精确 vs 声学无损两种检查**（`src/checks.rs`）
   - 位精确：每个解码 i32 与生成源逐样本严格相等。
   - 声学无损：按满幅归一化，误差低于约 -90 dB 阈值即视为声学无损。
   - `perturbed` 用例通过注入单个 16-bit LSB 偏差演示二者差异：位精确
     失败、声学无损仍通过。

6. **零样本与满幅样本防溢出**（`src/generator.rs`、`src/checks.rs`）
   - 生成全零与正负满幅（rail）信号，解码后确认值落在位深有符号范围内，
     无环绕/饱和越界；满幅样本精确到 ±(2^(n-1)) 轨。

## 样例矩阵

`src/main.rs` 生成 19 个 `.m4a`：

- 交错/声道：1、2、4、6 声道（mono、stereo、4.0、5.1 布局）
- 位深：16、24、32 bit PCM 输入（ALAC 载荷对 32bit 输入按 24bit 存储）
- 信号：多音调正弦+斜坡、全零、满幅、单 LSB 扰动
- 每个文件都包含整帧 + 一个部分末帧，覆盖 partial-frame 边界路径

## 运行

需要 `ffmpeg`（编码/交叉验证）与 Rust 工具链：

```bash
export PATH="$HOME/.local/bin:$PATH"
cargo run --release            # 生成样例、校验并写报告
cargo test                     # cookie/包头解析单元测试
cargo run --bin corrupt -- artifacts/samples/tones_2ch_16bit.m4a  # 负向测试
```

输出：

- `artifacts/samples/*.m4a`：生成的 ALAC 样例（及中间 `.pcm`）
- `artifacts/reports/report.txt`：人类可读的逐 packet 报告
- `artifacts/reports/report.json`：机器可读结果

## 关键结论

- 19/19 文件：packet 边界 0 异常、溢出 0 违规、值核对 0 失败。
- 每个完整 packet 解码 4096 帧（与 cookie `frameLength` 一致），末包为
  部分帧；累计样本数与 `stts` 总帧数完全相等。
- ffmpeg 独立解码与源 PCM 逐字节相同，验证跨实现位精确。
- 负向测试确认 cookie 篡改、非法位深、声道不一致、截断/越界/损坏包头
  都会被拒绝。
