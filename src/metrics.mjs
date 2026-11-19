export function signalMetrics(input, output, codes = null) {
  let inputPower = 0;
  let noisePower = 0;
  let signedError = 0;
  let inputSum = 0;
  let outputSum = 0;
  let maxAbsError = 0;
  let maxAbsInput = 0;
  let maxAbsOutput = 0;
  const outputLevels = new Set();

  for (let index = 0; index < input.length; index += 1) {
    const sample = input[index];
    const reconstructed = output[index];
    const error = reconstructed - sample;
    inputPower += sample * sample;
    noisePower += error * error;
    signedError += error;
    inputSum += sample;
    outputSum += reconstructed;
    maxAbsError = Math.max(maxAbsError, Math.abs(error));
    maxAbsInput = Math.max(maxAbsInput, Math.abs(sample));
    maxAbsOutput = Math.max(maxAbsOutput, Math.abs(reconstructed));
    outputLevels.add(reconstructed);
  }

  const count = input.length;
  const signalPower = count === 0 ? 0 : inputPower / count;
  const errorPower = count === 0 ? 0 : noisePower / count;
  const snrDefined = signalPower > 0 && errorPower > 0;

  return {
    samples: count,
    input_peak_lsb: maxAbsInput,
    output_peak_lsb: maxAbsOutput,
    signal_power_lsb2: round(signalPower),
    noise_power_lsb2: round(errorPower),
    snr_db: snrDefined ? round(10 * Math.log10(signalPower / errorPower)) : null,
    snr_note: snrNote(signalPower, errorPower),
    max_abs_error_lsb: maxAbsError,
    rmse_lsb: round(Math.sqrt(errorPower)),
    mean_error_lsb: round(signedError / count),
    input_dc_offset_lsb: round(inputSum / count),
    output_dc_offset_lsb: round(outputSum / count),
    unique_output_levels: outputLevels.size,
    encoded_codes: codes === null ? null : Array.from(new Set(codes)).sort(
      (left, right) => left - right,
    ),
  };
}

function snrNote(signalPower, errorPower) {
  if (signalPower === 0) return "undefined: all-zero input";
  if (errorPower === 0) return "infinite: exact reconstruction";
  return null;
}

export function analyzeQuantization(decode) {
  const rows = Array.from({ length: 256 }, (_, code) => ({
    code,
    pcm: decode(code),
  }));
  const levels = [...new Set(rows.map((row) => row.pcm))].sort(
    (left, right) => left - right,
  );
  const zeroRows = rows.filter((row) => row.pcm === 0);
  const negativeLevel = [...levels].reverse().find((level) => level < 0);
  const positiveLevel = levels.find((level) => level > 0);
  const coreLevels = levels.filter((level) => Math.abs(level) <= 256);
  const positiveFirstSegment = levels.filter(
    (level) => level >= 0 && level < 256,
  );
  const negativeFirstSegment = levels.filter(
    (level) => level < 0 && level > -256,
  );

  const levelRows = levels.map((pcm) => ({
    pcm,
    codes: rows
      .filter((row) => row.pcm === pcm)
      .map((row) => `0x${row.code.toString(16).padStart(2, "0")}`),
  }));

  return {
    distinct_levels: levels.length,
    zero_level: levels.includes(0),
    zero_code_count: zeroRows.length,
    zero_codes: zeroRows.map(
      (row) => `0x${row.code.toString(16).padStart(2, "0")}`,
    ),
    nearest_negative_level: negativeLevel,
    nearest_positive_level: positiveLevel,
    step_across_zero_lsb: positiveLevel - negativeLevel,
    smallest_positive_step_lsb: minimumStep(
      positiveFirstSegment,
    ),
    smallest_negative_step_lsb: minimumStep(negativeFirstSegment),
    first_segment_positive_step_lsb: stepAtStart(positiveFirstSegment),
    core_levels: coreLevels.map((pcm) => ({
      pcm,
      codes: levelRows.find((row) => row.pcm === pcm).codes,
    })),
    all_levels: levelRows,
  };
}

function minimumStep(levels) {
  const steps = [];
  for (let index = 1; index < levels.length; index += 1) {
    steps.push(levels[index] - levels[index - 1]);
  }
  return steps.length > 0 ? Math.min(...steps) : null;
}

function stepAtStart(levels) {
  if (levels.length < 2) return null;
  return levels[1] - levels[0];
}

export function checkSymmetry(encode, decode) {
  let codeXorMismatches = 0;
  let pcmMagnitudeMismatches = 0;
  let loopbackMagnitudeMismatches = 0;
  let maxPcmMagnitudeDifference = 0;
  let maxLoopbackMagnitudeDifference = 0;
  const examples = [];

  for (let magnitude = 1; magnitude <= 32767; magnitude += 1) {
    const positiveCode = encode(magnitude);
    const negativeCode = encode(-magnitude);
    const positivePcm = decode(positiveCode);
    const negativePcm = decode(negativeCode);
    const positiveLoopback = decode(positiveCode);
    const negativeLoopback = decode(negativeCode);
    const pcmDifference = Math.abs(positivePcm - Math.abs(negativePcm));
    const loopbackDifference = Math.abs(
      positiveLoopback - Math.abs(negativeLoopback),
    );
    const codeXor = positiveCode ^ negativeCode;

    if (codeXor !== 0x80) codeXorMismatches += 1;
    if (pcmDifference !== 0) pcmMagnitudeMismatches += 1;
    if (loopbackDifference !== 0) loopbackMagnitudeMismatches += 1;
    maxPcmMagnitudeDifference = Math.max(
      maxPcmMagnitudeDifference,
      pcmDifference,
    );
    maxLoopbackMagnitudeDifference = Math.max(
      maxLoopbackMagnitudeDifference,
      loopbackDifference,
    );

    if (
      examples.length < 20 &&
      (codeXor !== 0x80 || pcmDifference !== 0)
    ) {
      examples.push({
        magnitude,
        positive_code: positiveCode,
        negative_code: negativeCode,
        positive_pcm: positivePcm,
        negative_pcm: negativePcm,
      });
    }
  }

  let codebookPairMismatches = 0;
  for (let code = 0; code < 128; code += 1) {
    if (Math.abs(decode(code)) !== Math.abs(decode(code ^ 0x80))) {
      codebookPairMismatches += 1;
    }
  }

  return {
    positive_magnitudes_tested: 32767,
    code_xor_sign_bit_mismatches: codeXorMismatches,
    decoded_magnitude_mismatches: pcmMagnitudeMismatches,
    loopback_magnitude_mismatches: loopbackMagnitudeMismatches,
    max_decoded_magnitude_difference_lsb: maxPcmMagnitudeDifference,
    max_loopback_magnitude_difference_lsb: maxLoopbackMagnitudeDifference,
    codebook_sign_pairs_tested: 128,
    codebook_sign_pair_mismatches: codebookPairMismatches,
    mismatch_examples: examples,
    passed:
      codeXorMismatches === 0 &&
      pcmMagnitudeMismatches === 0 &&
      loopbackMagnitudeMismatches === 0 &&
      codebookPairMismatches === 0,
  };
}

export function round(value, digits = 6) {
  if (!Number.isFinite(value)) return value;
  const scale = 10 ** digits;
  return Math.round(value * scale) / scale;
}
