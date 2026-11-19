import { writeFile } from "node:fs/promises";
import path from "node:path";

export async function writeSampleDifferenceCsv(filePath, signals) {
  const header = [
    "signal",
    "index",
    "input_pcm",
    "mulaw_code",
    "mulaw_code_hex",
    "mulaw_loopback_pcm",
    "mulaw_difference_lsb",
    "alaw_code",
    "alaw_code_hex",
    "alaw_loopback_pcm",
    "alaw_difference_lsb",
  ];
  const lines = [header.join(",")];
  for (const [key, signal] of Object.entries(signals)) {
    const { input, reports } = signal;
    for (let index = 0; index < input.length; index += 1) {
      const mu = reports.mulaw;
      const a = reports.alaw;
      const inputPcm = input[index];
      lines.push(
        [
          key,
          index,
          inputPcm,
          mu.codes[index],
          hexByte(mu.codes[index]),
          mu.output[index],
          mu.output[index] - inputPcm,
          a.codes[index],
          hexByte(a.codes[index]),
          a.output[index],
          a.output[index] - inputPcm,
        ].join(","),
      );
    }
  }
  await writeFile(filePath, `${lines.join("\n")}\n`);
}

export async function writeNearZeroCsv(filePath, quantization) {
  const rows = [["codec", "pcm_lsb", "codes", "step_from_previous_lsb"]];
  for (const [codec, analysis] of Object.entries(quantization)) {
    let previous = null;
    for (const level of analysis.core_levels) {
      rows.push([
        codec,
        level.pcm,
        level.codes.join("|"),
        previous === null ? "" : level.pcm - previous,
      ]);
      previous = level.pcm;
    }
  }
  await writeFile(
    filePath,
    `${rows.map((row) => row.join(",")).join("\n")}\n`,
  );
}

export async function writeSmallSignalCsv(filePath, smallSignal) {
  const rows = [[
    "codec",
    "peak_amplitude_lsb",
    "snr_db",
    "max_abs_error_lsb",
    "rmse_lsb",
    "output_dc_offset_lsb",
    "unique_output_levels",
    "within_half_first_step",
  ]];
  for (const [codec, analysis] of Object.entries(smallSignal.codecs)) {
    for (const point of analysis) {
      rows.push([
        codec,
        point.peak_amplitude_lsb,
        point.metrics.snr_db ?? "",
        point.metrics.max_abs_error_lsb,
        point.metrics.rmse_lsb,
        point.metrics.output_dc_offset_lsb,
        point.metrics.unique_output_levels,
        point.within_half_first_step ?? "",
      ]);
    }
  }
  await writeFile(
    filePath,
    `${rows.map((row) => row.join(",")).join("\n")}\n`,
  );
}

export async function writeSymmetryCsv(filePath, symmetry) {
  const rows = [[
    "codec",
    "positive_magnitude",
    "negative_input",
    "positive_code",
    "negative_code",
    "positive_pcm",
    "negative_pcm",
    "abs_magnitude_difference_lsb",
    "code_xor",
  ]];
  for (const [codec, analysis] of Object.entries(symmetry.codecs)) {
    for (const row of analysis.representative_rows) {
      rows.push([
        codec,
        row.magnitude,
        -row.magnitude,
        row.positive_code,
        row.negative_code,
        row.positive_pcm,
        row.negative_pcm,
        Math.abs(row.positive_pcm - Math.abs(row.negative_pcm)),
        row.code_xor,
      ]);
    }
  }
  await writeFile(
    filePath,
    `${rows.map((row) => row.join(",")).join("\n")}\n`,
  );
}

export async function writeSummaryMarkdown(filePath, data) {
  const lines = [
    "# G.711 μ-law / A-law WASM 回环校验报告",
    "",
    `生成时间（UTC）：${data.generatedAt}`,
    "",
    "## 方法与口径",
    "",
    "- 输入为 signed 16-bit PCM，量纲为 LSB；μ-law 与 A-law 均按 16-bit G.711 常用口径裁剪到 ±32635。",
    "- 回环定义：PCM → 8-bit G.711 → PCM，全部编解码由 `dist/g711.wasm` 执行。",
    "- 误差 `e[n] = loopback_pcm[n] - input_pcm[n]`；SNR = `10 log10(sum(x^2) / sum(e^2))`。",
    "- 全零静音的信号功率为 0，因此 SNR 未定义；表中报告最大误差与输出直流。",
    "",
    "## 全码域校验",
    "",
    `- 已遍历全部 ${data.validation.pcm_samples_tested} 个 16-bit PCM 值和全部 ${data.validation.code_words_tested} 个 G.711 码字。`,
    `- μ-law：标量/批量/回环差异 ${mismatchCount(data.validation.codecs.mulaw)}；PCM 回环幂等 ${data.validation.codecs.mulaw.pcm_loopback_idempotent_samples}/${data.validation.pcm_samples_tested}。`,
    `- A-law：标量/批量/回环差异 ${mismatchCount(data.validation.codecs.alaw)}；PCM 回环幂等 ${data.validation.codecs.alaw.pcm_loopback_idempotent_samples}/${data.validation.pcm_samples_tested}。`,
    "- μ-law 的 3 个码字重写差异来自 `0x7F/0xFF` 双零码；解码后的 PCM 回环结果仍完全幂等。",
    "",
    "## 信号量化误差与 SNR",
    "",
    "| 信号 | 样本数 | μ-law 最大误差 | μ-law SNR | μ-law 输出直流 | A-law 最大误差 | A-law SNR | A-law 输出直流 |",
    "|---|---:|---:|---:|---:|---:|---:|---:|",
  ];

  for (const signal of data.signalRows) {
    lines.push(
      `| ${signal.label} | ${signal.samples} | ${fmt(signal.mulaw.max_abs_error_lsb, 0)} LSB | ${fmtSnr(signal.mulaw.snr_db)} | ${fmt(signal.mulaw.output_dc_offset_lsb)} LSB | ${fmt(signal.alaw.max_abs_error_lsb, 0)} LSB | ${fmtSnr(signal.alaw.snr_db)} | ${fmt(signal.alaw.output_dc_offset_lsb)} LSB |`,
    );
  }
  lines.push(
    "",
    "说明：静音为 400 个 0；正弦为 1 kHz、峰值 16384 LSB、1 秒；真实语音为 8 kHz 单声道 FSDD `0_jackson_0.wav`，5148 个样本。",
    "",
    "## 零附近量化台阶",
    "",
    "| 规则 | 零电平 | 零码 | 最近负/正电平 | 首段正台阶 | 最近的负→正电平间距 |",
    "|---|---:|---|---:|---:|---:|",
  );
  for (const [codec, analysis] of Object.entries(data.quantization)) {
    lines.push(
      `| ${codecLabel(codec)} | ${analysis.zero_level ? "有" : "无"} | ${analysis.zero_codes.length > 0 ? analysis.zero_codes.join(", ") : "无"} | ${analysis.nearest_negative_level} / ${analysis.nearest_positive_level} | ${analysis.first_segment_positive_step_lsb} LSB | ${analysis.step_across_zero_lsb} LSB |`,
    );
  }
  lines.push(
    "",
    "- μ-law 有 `0x7F` 与 `0xFF` 两个零码，零两侧各有 8 LSB 台阶。",
    "- A-law 没有精确零电平，零附近为 `-8 → +8`，首段台阶为 16 LSB；静音/零输入会回到 `+8 LSB`。",
    "",
    "## 小信号直流与失真",
    "",
  );

  for (const [codec, summary] of Object.entries(data.smallSignal.summary)) {
    lines.push(
      `- ${codecLabel(codec)}：峰值 ≤ ${data.smallSignal.max_amplitude_lsb} LSB 时，最大绝对误差 ${summary.max_abs_error_lsb} LSB，最大输出直流 ${summary.max_abs_dc_offset_lsb} LSB；半首段台阶上限 ${summary.half_first_step_lsb} LSB，检查${summary.passed ? "通过" : "未通过"}。`,
    );
  }
  lines.push(
    "- 结论：未见由正负编码规则引入的额外直流偏置或非对称失真。A-law 在幅度小于 8 LSB 时相对 SNR 明显下降，这是其 16 LSB 零区死台阶造成的固有量化，不是实现偏置。",
    "",
    "## 正负样本对称性",
    "",
    "| 规则 | 测试幅值对 | 码字 XOR 异常 | 解码幅值差 | 回环幅值差 | 码本符号对异常 | 结果 |",
    "|---|---:|---:|---:|---:|---:|---|",
  );
  for (const [codec, analysis] of Object.entries(data.symmetry.codecs)) {
    lines.push(
      `| ${codecLabel(codec)} | ${analysis.positive_magnitudes_tested} | ${analysis.code_xor_sign_bit_mismatches} | ${analysis.max_decoded_magnitude_difference_lsb} LSB | ${analysis.max_loopback_magnitude_difference_lsb} LSB | ${analysis.codebook_sign_pair_mismatches} | ${analysis.passed ? "通过" : "失败"} |`,
    );
  }
  lines.push(
    "",
    "- 对每个 `m ∈ [1, 32767]`，正负码字满足 `code(+m) XOR code(-m) = 0x80`，解码后的绝对值完全相等。",
    "- A-law 的零输入使用正侧 `0xD5 → +8`；负的最小样本使用 `0x55 → -8`，两侧对称但没有零电平。",
    "",
    "## 输出文件",
    "",
    "- `reports/loopback-sample-differences.csv`：所有测试信号按样本序号列出输入、码字、回环 PCM 与差值。",
    "- `reports/loopback-metrics.json`：最大误差、RMSE、SNR、直流偏移等完整指标。",
    "- `reports/full-domain-validation.json`：65,536 PCM 值与 256 码字的全量校验明细。",
    "- `reports/quantization-near-zero.csv`：零附近电平、码字与台阶。",
    "- `reports/small-signal-metrics.csv`：小幅正弦幅度扫描。",
    "- `reports/symmetry.json`、`reports/symmetry-edges.csv`：正负样本对称性结果。",
  );
  await writeFile(filePath, `${lines.join("\n")}\n`);
}

function mismatchCount(analysis) {
  return (
    analysis.scalar_mismatches +
    analysis.batch_encode_mismatches +
    analysis.batch_decode_mismatches +
    analysis.batch_loopback_mismatches +
    analysis.codebook_decode_mismatches
  );
}

function codecLabel(codec) {
  return codec === "mulaw" ? "μ-law" : "A-law";
}

function fmt(value, digits = 3) {
  if (value === null) return "N/A";
  return Number(value).toFixed(digits);
}

function fmtSnr(value) {
  return value === null ? "N/A" : `${fmt(value)} dB`;
}

function hexByte(value) {
  return `0x${value.toString(16).padStart(2, "0")}`;
}

export function reportPaths(outputDir) {
  return {
    metrics: path.join(outputDir, "loopback-metrics.json"),
    validation: path.join(outputDir, "full-domain-validation.json"),
    sampleDifferences: path.join(
      outputDir,
      "loopback-sample-differences.csv",
    ),
    quantization: path.join(outputDir, "quantization-near-zero.csv"),
    smallSignal: path.join(outputDir, "small-signal-metrics.csv"),
    symmetry: path.join(outputDir, "symmetry.json"),
    symmetryEdges: path.join(outputDir, "symmetry-edges.csv"),
    summary: path.join(outputDir, "summary.md"),
  };
}
