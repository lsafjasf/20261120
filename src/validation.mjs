import { codecs, loopbackReference } from "./reference.mjs";

const ALL_SAMPLES = Int16Array.from(
  { length: 65536 },
  (_, index) => index - 32768,
);

export function validateFullDomain(wasm) {
  const report = {
    pcm_samples_tested: ALL_SAMPLES.length,
    code_words_tested: 256,
    codecs: {},
  };

  for (const codec of Object.keys(codecs)) {
    report.codecs[codec] = validateCodec(wasm, codec);
  }
  return report;
}

function validateCodec(wasm, codec) {
  const rule = codecs[codec];
  const expectedCodes = new Uint8Array(ALL_SAMPLES.length);
  const expectedLoopback = new Int16Array(ALL_SAMPLES.length);
  const scalarMismatches = [];

  for (let index = 0; index < ALL_SAMPLES.length; index += 1) {
    const sample = ALL_SAMPLES[index];
    const code = rule.encode(sample);
    const decoded = rule.decode(code);
    expectedCodes[index] = code;
    expectedLoopback[index] = decoded;

    if (
      wasm.encodeSample(codec, sample) !== code ||
      wasm.decodeSample(codec, code) !== decoded ||
      wasm.loopbackSample(codec, sample) !== decoded
    ) {
      if (scalarMismatches.length < 20) {
        scalarMismatches.push({ sample, expected_code: code, expected_pcm: decoded });
      }
    }
  }

  const wasmCodes = wasm.encode(codec, ALL_SAMPLES);
  const wasmLoopback = wasm.loopback(codec, ALL_SAMPLES);
  const expectedCodebook = new Int16Array(256);
  const wasmCodebook = new Int16Array(256);
  for (let code = 0; code < 256; code += 1) {
    expectedCodebook[code] = rule.decode(code);
    wasmCodebook[code] = wasm.decodeSample(codec, code);
  }

  const encodedMismatches = diffIndexes(wasmCodes, expectedCodes);
  const decodedMismatches = diffIndexes(
    wasm.decode(codec, expectedCodes),
    expectedLoopback,
  );
  const loopbackMismatches = diffIndexes(wasmLoopback, expectedLoopback);
  const codebookMismatches = diffIndexes(wasmCodebook, expectedCodebook);

  let codeStable = 0;
  let pcmCodeStable = 0;
  let pcmLoopbackStable = 0;
  for (let code = 0; code < 256; code += 1) {
    const decoded = rule.decode(code);
    if (rule.decode(rule.encode(decoded)) === decoded) codeStable += 1;
  }
  for (const sample of ALL_SAMPLES) {
    const once = loopbackReference(sample, codec);
    if (rule.encode(once) === rule.encode(sample)) {
      pcmCodeStable += 1;
    }
    if (loopbackReference(once, codec) === once) {
      pcmLoopbackStable += 1;
    }
  }

  return {
    scalar_mismatches: scalarMismatches.length,
    scalar_mismatch_examples: scalarMismatches,
    batch_encode_mismatches: encodedMismatches.length,
    batch_encode_mismatch_examples: encodedMismatches.slice(0, 20),
    batch_decode_mismatches: decodedMismatches.length,
    batch_decode_mismatch_examples: decodedMismatches.slice(0, 20),
    batch_loopback_mismatches: loopbackMismatches.length,
    batch_loopback_mismatch_examples: loopbackMismatches.slice(0, 20),
    codebook_decode_mismatches: codebookMismatches.length,
    codebook_decode_mismatch_examples: codebookMismatches.slice(0, 20),
    codebook_decoded_values: expectedCodebook.length,
    codebook_stable_values: codeStable,
    pcm_code_rewrite_stable: pcmCodeStable === ALL_SAMPLES.length,
    pcm_code_rewrite_stable_samples: pcmCodeStable,
    pcm_loopback_idempotent: pcmLoopbackStable === ALL_SAMPLES.length,
    pcm_loopback_idempotent_samples: pcmLoopbackStable,
  };
}

function diffIndexes(actual, expected) {
  const indexes = [];
  for (let index = 0; index < expected.length; index += 1) {
    if (actual[index] !== expected[index]) indexes.push(index);
  }
  return indexes;
}

export function validationPassed(report) {
  return Object.values(report.codecs).every(
    (result) =>
      result.scalar_mismatches === 0 &&
      result.batch_encode_mismatches === 0 &&
      result.batch_decode_mismatches === 0 &&
      result.batch_loopback_mismatches === 0 &&
      result.codebook_decode_mismatches === 0 &&
      result.pcm_loopback_idempotent,
  );
}
