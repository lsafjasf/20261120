'use strict';

const { performance } = require('node:perf_hooks');

function mod(value, modulus) {
  return ((value % modulus) + modulus) % modulus;
}

function percentile(sortedValues, fraction) {
  if (sortedValues.length === 0) return null;
  const index = Math.min(
    sortedValues.length - 1,
    Math.max(0, Math.ceil(fraction * sortedValues.length) - 1),
  );
  return sortedValues[index];
}

function round(value, digits = 3) {
  if (value === null || value === undefined || Number.isNaN(value)) return null;
  const factor = 10 ** digits;
  return Math.round(value * factor) / factor;
}

function summarize(values, { bins = null } = {}) {
  const clean = values
    .filter((value) => Number.isFinite(value))
    .map((value) => Number(value))
    .sort((left, right) => left - right);

  if (clean.length === 0) {
    return { count: 0, min: null, mean: null, p50: null, p90: null, p95: null, p99: null, max: null };
  }

  const total = clean.reduce((sum, value) => sum + value, 0);
  const result = {
    count: clean.length,
    min: round(clean[0]),
    mean: round(total / clean.length),
    p50: round(percentile(clean, 0.5)),
    p90: round(percentile(clean, 0.9)),
    p95: round(percentile(clean, 0.95)),
    p99: round(percentile(clean, 0.99)),
    max: round(clean[clean.length - 1]),
  };

  if (bins) {
    result.histogram = bins.map((bin) => ({
      range: bin.label,
      count: clean.filter((value) => value >= bin.min && value < bin.max).length,
    }));
  }

  return result;
}

function elapsedMs(startedAt) {
  return performance.now() - startedAt;
}

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function waitFor(predicate, { timeoutMs = 5000, intervalMs = 5, label = 'condition' } = {}) {
  const deadline = performance.now() + timeoutMs;
  while (performance.now() < deadline) {
    const value = predicate();
    if (value) return value;
    await delay(intervalMs);
  }
  throw new Error(`Timed out waiting for ${label}`);
}

module.exports = {
  delay,
  elapsedMs,
  mod,
  percentile,
  round,
  summarize,
  waitFor,
};
