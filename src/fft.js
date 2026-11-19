const planCache = new Map();

export class FFTPlan {
  constructor(size) {
    if (!Number.isInteger(size) || size < 2 || (size & (size - 1)) !== 0) {
      throw new Error(`FFT size must be a power of two, received ${size}`);
    }

    this.size = size;
    this.bitReverse = new Uint32Array(size);
    this.cos = new Float64Array(size / 2);
    this.sin = new Float64Array(size / 2);

    const bits = Math.log2(size);
    for (let i = 0; i < size; i++) {
      let value = i;
      let reversed = 0;
      for (let bit = 0; bit < bits; bit++) {
        reversed = (reversed << 1) | (value & 1);
        value >>>= 1;
      }
      this.bitReverse[i] = reversed;
    }

    for (let i = 0; i < size / 2; i++) {
      const angle = (2 * Math.PI * i) / size;
      this.cos[i] = Math.cos(angle);
      this.sin[i] = Math.sin(angle);
    }
  }

  transform(real, imag, inverse = false) {
    const n = this.size;
    if (real.length < n || imag.length < n) {
      throw new Error(`FFT buffers must contain at least ${n} samples`);
    }

    const reverse = this.bitReverse;
    for (let i = 0; i < n; i++) {
      const j = reverse[i];
      if (j > i) {
        const tr = real[i];
        const ti = imag[i];
        real[i] = real[j];
        imag[i] = imag[j];
        real[j] = tr;
        imag[j] = ti;
      }
    }

    for (let width = 2; width <= n; width <<= 1) {
      const half = width >> 1;
      const tableStep = n / width;
      for (let start = 0; start < n; start += width) {
        for (let j = 0; j < half; j++) {
          const twiddle = j * tableStep;
          const wr = this.cos[twiddle];
          const wi = inverse ? this.sin[twiddle] : -this.sin[twiddle];
          const even = start + j;
          const odd = even + half;
          const xr = real[odd] * wr - imag[odd] * wi;
          const xi = real[odd] * wi + imag[odd] * wr;
          real[odd] = real[even] - xr;
          imag[odd] = imag[even] - xi;
          real[even] += xr;
          imag[even] += xi;
        }
      }
    }

    if (inverse) {
      const scale = 1 / n;
      for (let i = 0; i < n; i++) {
        real[i] *= scale;
        imag[i] *= scale;
      }
    }
  }
}

export function getFFTPlan(size) {
  let plan = planCache.get(size);
  if (!plan) {
    plan = new FFTPlan(size);
    planCache.set(size, plan);
  }
  return plan;
}

export function nextPowerOfTwo(value) {
  let size = 1;
  while (size < value) size <<= 1;
  return size;
}

