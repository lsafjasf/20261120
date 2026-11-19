import { mkdir, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { loadG711 } from "../src/wasm.mjs";
import {
  analyzeQuantization,
  checkSymmetry,
  signalMetrics,
} from "../src/metrics.mjs";
import { loadSignalSets, sine } from "../src/signals.mjs";
import { validateFullDomain, validationPassed } from "../src/validation.mjs";
import {
  reportPaths,
  writeNearZeroCsv,
  writeSampleDifferenceCsv,
  writeSmallSignalCsv,
  writeSummaryMarkdown,
  writeSymmetryCsv,
} from "./reporting.mjs";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const outputDir = path.join(root, "reports");
const paths = reportPaths(outputDir);
await mkdir(outputDir, { recursive: true });

const wasm = await loadG711();
const validation = validateFullDomain(wasm);
if (!validationPassed(validation)) {
  throw new Error("full-domain WASM validation failed");
}

const signalSets = await loadSignalSets();
const labels = {
  silence: "静音（400 个零）",
  sine: "1 kHz 正弦（峰值 16384）",
  speech: "真实语音（FSDD）",
  sineSmall: "小幅正弦（峰值 32）",
};
const processed = {};
const signalJson = {};
const signalRows = [];

for (const [key, signal] of Object.entries(signalSets)) {
  const reports = {};
  const codecs = {};
  for (const codec of ["mulaw", "alaw"]) {
    const codes = wasm.encode(codec, signal.samples);
    const output = wasm.loopback(codec, signal.samples);
    const decoded = wasm.decode(codec, codes);
    const metrics = signalMetrics(signal.samples, output, codes);
    let same = decoded.length === output.length;
    for (let index = 0; same && index < output.length; index += 1) {
      same = decoded[index] === output[index];
    }
    reports[codec] = { codes, output, metrics };
    codecs[codec] = {
      ...metrics,
      encoded_code_count: metrics.encoded_codes.length,
      loopback_batch_equals_decode_of_encoded: same,
    };
  }
  processed[key] = { input: signal.samples, reports };
  signalJson[key] = {
    description: signal.description,
    sample_rate: signal.sampleRate ?? 8000,
    samples: signal.samples.length,
    codecs,
  };
  signalRows.push({
    key,
    label: labels[key],
    samples: signal.samples.length,
    mulaw: reports.mulaw.metrics,
    alaw: reports.alaw.metrics,
  });
}

const quantization = {
  mulaw: analyzeQuantization((code) => wasm.decodeSample("mulaw", code)),
  alaw: analyzeQuantization((code) => wasm.decodeSample("alaw", code)),
};

const amplitudes = [1, 2, 3, 4, 8, 16, 24, 32, 64, 128, 256, 512];
const smallCodecs = {};
const smallSummary = {};
for (const codec of ["mulaw", "alaw"]) {
  const points = amplitudes.map((amplitude) => {
    const input = sine({ amplitude });
    const output = wasm.loopback(codec, input);
    const metrics = signalMetrics(input, output);
    const limit = quantization[codec].first_segment_positive_step_lsb / 2;
    return {
      peak_amplitude_lsb: amplitude,
      metrics,
      within_half_first_step:
        amplitude <= 32 ? metrics.max_abs_error_lsb <= limit : null,
    };
  });
  smallCodecs[codec] = points;
  const smallPoints = points.filter(
    (point) => point.peak_amplitude_lsb <= 32,
  );
  const halfFirstStep =
    quantization[codec].first_segment_positive_step_lsb / 2;
  const maxAbsError = Math.max(
    ...smallPoints.map((point) => point.metrics.max_abs_error_lsb),
  );
  const maxAbsDc = Math.max(
    ...smallPoints.map((point) =>
      Math.abs(point.metrics.output_dc_offset_lsb),
    ),
  );
  smallSummary[codec] = {
    amplitudes_tested: smallPoints.map((point) => point.peak_amplitude_lsb),
    half_first_step_lsb: halfFirstStep,
    max_abs_error_lsb: maxAbsError,
    max_abs_dc_offset_lsb: maxAbsDc,
    dc_offset_within_half_step: maxAbsDc <= halfFirstStep,
    absolute_distortion_within_half_step: maxAbsError <= halfFirstStep,
    passed: maxAbsError <= halfFirstStep && maxAbsDc <= halfFirstStep,
  };
}

const symmetryCodecs = {};
const representativeMagnitudes = [
  1, 2, 3, 4, 5, 8, 16, 24, 32, 64, 128, 256, 512, 1024, 16384, 32635,
  32767,
];
for (const codec of ["mulaw", "alaw"]) {
  const checked = checkSymmetry(
    wasm.encodeSample.bind(wasm, codec),
    wasm.decodeSample.bind(wasm, codec),
  );
  const zeroCode = wasm.encodeSample(codec, 0);
  const negativeOneCode = wasm.encodeSample(codec, -1);
  symmetryCodecs[codec] = {
    ...checked,
    zero_behavior: {
      zero_input_code: zeroCode,
      zero_input_pcm: wasm.decodeSample(codec, zeroCode),
      negative_one_input_code: negativeOneCode,
      negative_one_input_pcm: wasm.decodeSample(codec, negativeOneCode),
      zero_level_in_codebook: quantization[codec].zero_level,
      zero_codes_in_codebook: quantization[codec].zero_codes,
    },
    representative_rows: representativeMagnitudes.map((magnitude) => {
      const positiveCode = wasm.encodeSample(codec, magnitude);
      const negativeCode = wasm.encodeSample(codec, -magnitude);
      return {
        magnitude,
        positive_code: positiveCode,
        negative_code: negativeCode,
        positive_pcm: wasm.decodeSample(codec, positiveCode),
        negative_pcm: wasm.decodeSample(codec, negativeCode),
        code_xor: positiveCode ^ negativeCode,
      };
    }),
  };
}
const symmetry = {
  method:
    "For every nonzero magnitude compare code(+m), code(-m), decoded magnitudes and PCM loopback magnitudes; also compare all 128 codebook sign pairs.",
  codecs: symmetryCodecs,
  passed: Object.values(symmetryCodecs).every((result) => result.passed),
};

const smallSignal = {
  method:
    "1 kHz sine sweep at 8 kHz; absolute error and output DC are checked against half of the first positive A-law/mu-law step.",
  max_amplitude_lsb: 32,
  codecs: smallCodecs,
  summary: smallSummary,
};

const generatedAt = new Date().toISOString();
const metricsReport = {
  generated_at_utc: generatedAt,
  sample_rate_hz: 8000,
  methodology: {
    pcm_format: "signed 16-bit little-endian",
    loopback: "PCM -> G.711 -> PCM, both directions executed in WASM",
    error: "loopback_pcm - input_pcm",
    snr: "10 * log10(sum(input^2) / sum(error^2))",
    silent_snr: "undefined because input signal power is zero",
  },
  full_domain_validation_passed: validationPassed(validation),
  signals: signalJson,
  quantization_near_zero: quantization,
  small_signal: smallSignal,
  symmetry,
};

await writeFile(paths.validation, `${JSON.stringify(validation, null, 2)}\n`);
await writeFile(
  paths.metrics,
  `${JSON.stringify(metricsReport, null, 2)}\n`,
);
await writeFile(paths.symmetry, `${JSON.stringify(symmetry, null, 2)}\n`);
await writeSampleDifferenceCsv(paths.sampleDifferences, processed);
await writeNearZeroCsv(paths.quantization, quantization);
await writeSmallSignalCsv(paths.smallSignal, smallSignal);
await writeSymmetryCsv(paths.symmetryEdges, symmetry);
await writeSummaryMarkdown(paths.summary, {
  generatedAt,
  validation,
  signalRows,
  quantization,
  smallSignal,
  symmetry,
});

printConsoleSummary({
  validation,
  signalRows,
  quantization,
  smallSignal,
  symmetry,
  paths,
});

function printConsoleSummary(data) {
  console.log("Full-domain validation: PASS");
  console.log(
    "signal                 mu max/SNR/DC          A max/SNR/DC",
  );
  for (const row of data.signalRows) {
    console.log(
      `${row.key.padEnd(22)} ${metricText(row.mulaw).padEnd(23)} ${metricText(row.alaw)}`,
    );
  }
  for (const [codec, analysis] of Object.entries(data.quantization)) {
    console.log(
      `${codec.padEnd(7)} zero=${analysis.zero_level} zero_codes=${analysis.zero_codes.length} first_step=${analysis.first_segment_positive_step_lsb} across_zero=${analysis.step_across_zero_lsb}`,
    );
  }
  for (const [codec, summary] of Object.entries(data.smallSignal.summary)) {
    console.log(
      `${codec.padEnd(7)} small-signal max_error=${summary.max_abs_error_lsb} max_DC=${summary.max_abs_dc_offset_lsb} pass=${summary.passed}`,
    );
  }
  for (const [codec, result] of Object.entries(data.symmetry.codecs)) {
    console.log(
      `${codec.padEnd(7)} symmetry pass=${result.passed} code_mismatches=${result.code_xor_sign_bit_mismatches} magnitude_mismatches=${result.decoded_magnitude_mismatches}`,
    );
  }
  console.log(`Reports: ${path.relative(root, outputDir)}/`);
}

function metricText(metrics) {
  const snr = metrics.snr_db === null ? "N/A" : `${metrics.snr_db}dB`;
  return `${metrics.max_abs_error_lsb}/${snr}/${metrics.output_dc_offset_lsb}`;
}
