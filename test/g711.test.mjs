import test from "node:test";
import assert from "node:assert/strict";
import { loadG711 } from "../src/wasm.mjs";
import { codecs } from "../src/reference.mjs";
import {
  validateFullDomain,
  validationPassed,
} from "../src/validation.mjs";
import {
  analyzeQuantization,
  checkSymmetry,
  signalMetrics,
} from "../src/metrics.mjs";
import { silence, sine } from "../src/signals.mjs";

const wasm = await loadG711();

test("known G.711 code words decode canonically", () => {
  const muVectors = [
    [0x00, -32124],
    [0x7e, -8],
    [0x7f, 0],
    [0x80, 32124],
    [0xfe, 8],
    [0xff, 0],
  ];
  const aVectors = [
    [0x2a, -32256],
    [0x55, -8],
    [0x54, -24],
    [0xd5, 8],
    [0xd4, 24],
    [0xaa, 32256],
  ];

  for (const [code, pcm] of muVectors) {
    assert.equal(wasm.decodeSample("mulaw", code), pcm);
  }
  for (const [code, pcm] of aVectors) {
    assert.equal(wasm.decodeSample("alaw", code), pcm);
  }
});

test("zero-near encoder behavior exposes the A-law dead zone", () => {
  assert.equal(wasm.encodeSample("mulaw", 0), 0xff);
  assert.equal(wasm.encodeSample("mulaw", -1), 0x7f);
  assert.equal(wasm.encodeSample("alaw", 0), 0xd5);
  assert.equal(wasm.encodeSample("alaw", -1), 0x55);
  assert.equal(wasm.loopbackSample("mulaw", 0), 0);
  assert.equal(wasm.loopbackSample("alaw", 0), 8);
});

test("WASM matches the reference over every PCM value and code word", () => {
  const report = validateFullDomain(wasm);
  assert.equal(report.pcm_samples_tested, 65536);
  assert.equal(report.code_words_tested, 256);
  assert.equal(validationPassed(report), true);
  assert.equal(report.codecs.mulaw.batch_loopback_mismatches, 0);
  assert.equal(report.codecs.alaw.batch_loopback_mismatches, 0);
});

test("both companding rules are symmetric for positive and negative input", () => {
  for (const codec of Object.keys(codecs)) {
    const result = checkSymmetry(
      wasm.encodeSample.bind(wasm, codec),
      wasm.decodeSample.bind(wasm, codec),
    );
    assert.equal(result.passed, true, JSON.stringify(result));
    assert.equal(result.max_decoded_magnitude_difference_lsb, 0);
    assert.equal(result.codebook_sign_pair_mismatches, 0);
  }
});

test("zero-near quantization steps differ as expected", () => {
  const mu = analyzeQuantization((code) => wasm.decodeSample("mulaw", code));
  const a = analyzeQuantization((code) => wasm.decodeSample("alaw", code));

  assert.equal(mu.zero_level, true);
  assert.equal(mu.zero_code_count, 2);
  assert.equal(mu.step_across_zero_lsb, 16);
  assert.equal(mu.first_segment_positive_step_lsb, 8);

  assert.equal(a.zero_level, false);
  assert.equal(a.zero_code_count, 0);
  assert.equal(a.step_across_zero_lsb, 16);
  assert.equal(a.first_segment_positive_step_lsb, 16);
});

test("silence and sine metrics have bounded error and defined DC behavior", () => {
  const silenceInput = silence();
  for (const codec of Object.keys(codecs)) {
    const output = wasm.loopback(codec, silenceInput);
    const metrics = signalMetrics(silenceInput, output);
    assert.equal(metrics.snr_db, null);
    assert.ok(metrics.max_abs_error_lsb <= 8);
    assert.ok(Math.abs(metrics.output_dc_offset_lsb) <= 8);
  }

  const sineInput = sine({ amplitude: 16384 });
  for (const codec of Object.keys(codecs)) {
    const output = wasm.loopback(codec, sineInput);
    const metrics = signalMetrics(sineInput, output);
    assert.ok(metrics.snr_db > 30);
    assert.ok(metrics.max_abs_error_lsb > 0);
  }
});
