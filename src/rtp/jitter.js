'use strict';

class RtpJitterEstimator {
  constructor(clockRate) {
    this.clockRate = clockRate;
    this.previousTransit = null;
    this.previousRawTimestamp = null;
    this.extendedTimestamp = null;
    this.smoothedJitter = 0;
  }

  unwrapTimestamp(rawTimestamp) {
    if (this.extendedTimestamp === null) {
      this.extendedTimestamp = rawTimestamp >>> 0;
    } else {
      let delta = (rawTimestamp - this.previousRawTimestamp) >>> 0;
      if (delta > 0x7fffffff) delta -= 0x100000000;
      this.extendedTimestamp += delta;
    }
    this.previousRawTimestamp = rawTimestamp;
    return this.extendedTimestamp;
  }

  observe(arrivalMs, rawTimestamp) {
    const timestampMs = (this.unwrapTimestamp(rawTimestamp) / this.clockRate) * 1000;
    const transit = arrivalMs - timestampMs;
    let delta = null;

    if (this.previousTransit !== null) {
      delta = transit - this.previousTransit;
      this.smoothedJitter += (Math.abs(delta) - this.smoothedJitter) / 16;
    }
    this.previousTransit = transit;

    return {
      transitMs: transit,
      deltaMs: delta,
      jitterMs: this.smoothedJitter,
    };
  }
}

module.exports = { RtpJitterEstimator };
