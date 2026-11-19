import { getFFTPlan, nextPowerOfTwo } from './fft.js';

export const DEFAULT_BLOCK_SIZE = 128;
export const DEFAULT_SAMPLE_RATE = 48000;

function assertImpulseResponse(ir) {
  if (!ir || ir.length === 0) {
    throw new Error('Impulse response must contain at least one sample');
  }
  for (let i = 0; i < ir.length; i++) {
    if (!Number.isFinite(ir[i])) {
      throw new Error(`Impulse response contains a non-finite sample at ${i}`);
    }
  }
}

function makeSpectrum(ir, offset, length, fftSize) {
  const plan = getFFTPlan(fftSize);
  const real = new Float64Array(fftSize);
  const imag = new Float64Array(fftSize);
  const end = Math.min(ir.length, offset + length);
  for (let i = offset; i < end; i++) {
    real[i - offset] = ir[i];
  }
  plan.transform(real, imag, false);
  return { plan, real, imag };
}

class ConvolutionBase {
  constructor({ blockSize = DEFAULT_BLOCK_SIZE, sampleRate = DEFAULT_SAMPLE_RATE } = {}) {
    if (!Number.isInteger(blockSize) || blockSize <= 0) {
      throw new Error('blockSize must be a positive integer');
    }
    if (!(sampleRate > 0)) {
      throw new Error('sampleRate must be positive');
    }
    this.blockSize = blockSize;
    this.sampleRate = sampleRate;
    this.latencySamples = blockSize;
    this.blocksProcessed = 0;
    this.fftCalls = 0;
    this.partitionProducts = 0;
  }

  reset() {
    this.blocksProcessed = 0;
    this.fftCalls = 0;
    this.partitionProducts = 0;
  }

  getLatencySeconds() {
    return this.latencySamples / this.sampleRate;
  }
}

export class UniformPartitionedConvolver extends ConvolutionBase {
  constructor(options = {}) {
    super(options);
    this.mode = 'uniform';
    this.impulseResponse = null;
    this.partitionCount = 0;
  }

  loadImpulseResponse(impulseResponse) {
    assertImpulseResponse(impulseResponse);
    const ir = impulseResponse instanceof Float64Array
      ? impulseResponse
      : Float64Array.from(impulseResponse);
    const block = this.blockSize;
    const fftSize = block * 2;
    const count = Math.ceil(ir.length / block);

    this.impulseResponse = ir;
    this.partitionCount = count;
    this.fftSize = fftSize;
    this.binCount = fftSize / 2 + 1;
    this.spectra = new Array(count);
    for (let i = 0; i < count; i++) {
      const spectrum = makeSpectrum(ir, i * block, block, fftSize);
      this.spectra[i] = {
        real: spectrum.real.subarray(0, this.binCount),
        imag: spectrum.imag.subarray(0, this.binCount),
      };
    }

    this.inputReal = new Array(count);
    this.inputImag = new Array(count);
    for (let i = 0; i < count; i++) {
      this.inputReal[i] = new Float64Array(this.binCount);
      this.inputImag[i] = new Float64Array(this.binCount);
    }

    this.plan = getFFTPlan(fftSize);
    this.frameReal = new Float64Array(fftSize);
    this.frameImag = new Float64Array(fftSize);
    this.accumReal = new Float64Array(this.binCount);
    this.accumImag = new Float64Array(this.binCount);
    this.previousInput = new Float64Array(block);
    this.inputCursor = count - 1;
    this.blocksProcessed = 0;
    this.fftCalls = 0;
    this.partitionProducts = 0;
    return this;
  }

  reset() {
    super.reset();
    if (!this.impulseResponse) return this;
    this.previousInput.fill(0);
    this.inputCursor = this.partitionCount - 1;
    for (let i = 0; i < this.partitionCount; i++) {
      this.inputReal[i].fill(0);
      this.inputImag[i].fill(0);
    }
    return this;
  }

  processBlock(input, output = new Float32Array(this.blockSize)) {
    const block = this.blockSize;
    const fftSize = this.fftSize;
    const bins = this.binCount;
    const frameReal = this.frameReal;
    const frameImag = this.frameImag;

    for (let i = 0; i < block; i++) {
      frameReal[i] = this.previousInput[i];
      frameReal[block + i] = input[i];
      this.previousInput[i] = input[i];
    }
    frameImag.fill(0);
    this.plan.transform(frameReal, frameImag, false);
    this.fftCalls++;

    this.inputCursor = (this.inputCursor + 1) % this.partitionCount;
    const currentReal = this.inputReal[this.inputCursor];
    const currentImag = this.inputImag[this.inputCursor];
    for (let i = 0; i < bins; i++) {
      currentReal[i] = frameReal[i];
      currentImag[i] = frameImag[i];
    }

    this.accumReal.fill(0);
    this.accumImag.fill(0);
    let cursor = this.inputCursor;
    for (let partition = 0; partition < this.partitionCount; partition++) {
      const h = this.spectra[partition];
      const xr = this.inputReal[cursor];
      const xi = this.inputImag[cursor];
      const ar = this.accumReal;
      const ai = this.accumImag;
      for (let i = 0; i < bins; i++) {
        ar[i] += h.real[i] * xr[i] - h.imag[i] * xi[i];
        ai[i] += h.real[i] * xi[i] + h.imag[i] * xr[i];
      }
      cursor = cursor === 0 ? this.partitionCount - 1 : cursor - 1;
    }
    this.partitionProducts += this.partitionCount;

    frameReal.set(this.accumReal);
    frameImag.set(this.accumImag);
    for (let i = 1; i < fftSize / 2; i++) {
      frameReal[fftSize - i] = this.accumReal[i];
      frameImag[fftSize - i] = -this.accumImag[i];
    }
    this.plan.transform(frameReal, frameImag, true);
    this.fftCalls++;

    for (let i = 0; i < block; i++) {
      output[i] = frameReal[block + i];
    }
    this.blocksProcessed++;
    return output;
  }

  estimateFootprintBytes() {
    const spectra = this.partitionCount * this.fftSize * 2 * Float64Array.BYTES_PER_ELEMENT;
    const histories = this.partitionCount * this.binCount * 2 * Float64Array.BYTES_PER_ELEMENT;
    return spectra + histories + this.fftSize * 4 * Float64Array.BYTES_PER_ELEMENT;
  }

  getMetrics(blocks = this.blocksProcessed) {
    return {
      mode: this.mode,
      blockSize: this.blockSize,
      latencySamples: this.latencySamples,
      latencyMs: this.getLatencySeconds() * 1000,
      partitionCount: this.partitionCount,
      fftSize: this.fftSize,
      fftCallsPerBlock: blocks ? this.fftCalls / blocks : 0,
      partitionProductsPerBlock: blocks ? this.partitionProducts / blocks : 0,
      footprintBytes: this.estimateFootprintBytes(),
    };
  }
}

export class NonUniformPartitionedConvolver extends ConvolutionBase {
  constructor(options = {}) {
    super(options);
    this.mode = 'non-uniform';
    this.impulseResponse = null;
    this.partitionCount = 0;
    this.tailStages = [];
    this.head = new UniformPartitionedConvolver({
      blockSize: this.blockSize,
      sampleRate: this.sampleRate,
    });
  }

  loadImpulseResponse(impulseResponse) {
    assertImpulseResponse(impulseResponse);
    const ir = impulseResponse instanceof Float64Array
      ? impulseResponse
      : Float64Array.from(impulseResponse);
    const block = this.blockSize;
    const headLength = block * 2;

    this.impulseResponse = ir;
    this.head.loadImpulseResponse(ir.subarray(0, Math.min(ir.length, headLength)));
    this.head.fftSize = block * 2;

    this.tailStages = [];
    let offset = headLength;
    let partitionLength = headLength;
    while (offset < ir.length) {
      const fftSize = nextPowerOfTwo(partitionLength * 2);
      const spectrum = makeSpectrum(ir, offset, partitionLength, fftSize);
      this.tailStages.push({
        offset,
        length: partitionLength,
        fftSize,
        strideBlocks: partitionLength / block,
        plan: spectrum.plan,
        filterReal: spectrum.real.subarray(0, fftSize / 2 + 1),
        filterImag: spectrum.imag.subarray(0, fftSize / 2 + 1),
        frameReal: new Float64Array(fftSize),
        frameImag: new Float64Array(fftSize),
      });
      offset += partitionLength;
      partitionLength *= 2;
    }

    this.partitionCount = 1 + this.tailStages.length;
    this.levelCount = 1 + this.tailStages.length;
    this.maxTailLength = this.tailStages.length
      ? this.tailStages[this.tailStages.length - 1].length
      : 0;
    this.historySize = this.maxTailLength * 2 + block;
    this.futureSize = this.maxTailLength + block * 2;
    this.inputHistory = new Float64Array(this.historySize);
    this.futureOutput = new Float64Array(this.futureSize);
    this.historyCursor = 0;
    this.blocksProcessed = 0;
    this.fftCalls = 0;
    this.partitionProducts = 0;
    return this;
  }

  reset() {
    super.reset();
    if (!this.impulseResponse) return this;
    this.head.reset();
    this.inputHistory.fill(0);
    this.futureOutput.fill(0);
    this.historyCursor = 0;
    return this;
  }

  readFuture(index, target, length) {
    const size = this.futureSize;
    let cursor = index % size;
    for (let i = 0; i < length; i++) {
      target[i] = this.futureOutput[cursor];
      this.futureOutput[cursor] = 0;
      cursor++;
      if (cursor === size) cursor = 0;
    }
  }

  addFuture(index, source, length) {
    const size = this.futureSize;
    let cursor = index % size;
    for (let i = 0; i < length; i++) {
      this.futureOutput[cursor] += source[i];
      cursor++;
      if (cursor === size) cursor = 0;
    }
  }

  processTailStage(stage, endSample) {
    const length = stage.length;
    const fftSize = stage.fftSize;
    const bins = fftSize / 2 + 1;
    const frameReal = stage.frameReal;
    const frameImag = stage.frameImag;
    const historySize = this.historySize;
    let cursor = (endSample - length * 2) % historySize;
    if (cursor < 0) cursor += historySize;

    for (let i = 0; i < length * 2; i++) {
      frameReal[i] = this.inputHistory[cursor];
      frameImag[i] = 0;
      cursor++;
      if (cursor === historySize) cursor = 0;
    }
    stage.plan.transform(frameReal, frameImag, false);
    this.fftCalls++;

    for (let i = 0; i < bins; i++) {
      const xr = frameReal[i];
      const xi = frameImag[i];
      frameReal[i] = stage.filterReal[i] * xr - stage.filterImag[i] * xi;
      frameImag[i] = stage.filterReal[i] * xi + stage.filterImag[i] * xr;
    }
    for (let i = 1; i < fftSize / 2; i++) {
      frameReal[fftSize - i] = frameReal[i];
      frameImag[fftSize - i] = -frameImag[i];
    }
    stage.plan.transform(frameReal, frameImag, true);
    this.fftCalls++;
    this.partitionProducts++;
    this.addFuture(endSample, frameReal.subarray(length), length);
  }

  processBlock(input, output = new Float32Array(this.blockSize)) {
    const block = this.blockSize;
    const startSample = this.blocksProcessed * block;
    const endSample = startSample + block;

    let cursor = this.historyCursor;
    for (let i = 0; i < block; i++) {
      this.inputHistory[cursor] = input[i];
      cursor++;
      if (cursor === this.historySize) cursor = 0;
    }
    this.historyCursor = cursor;

    this.readFuture(startSample, output, block);
    const headOutput = this.head.processBlock(input);
    for (let i = 0; i < block; i++) output[i] += headOutput[i];
    this.fftCalls += 2;
    this.partitionProducts += this.head.partitionCount;

    for (const stage of this.tailStages) {
      if (endSample % stage.length === 0) {
        this.processTailStage(stage, endSample);
      }
    }

    this.blocksProcessed++;
    return output;
  }

  estimateFootprintBytes() {
    let tailSpectra = 0;
    let tailFrames = 0;
    for (const stage of this.tailStages) {
      tailSpectra += stage.fftSize * 2 * Float64Array.BYTES_PER_ELEMENT;
      tailFrames += stage.fftSize * 2 * Float64Array.BYTES_PER_ELEMENT;
    }
    return this.head.estimateFootprintBytes()
      + tailSpectra
      + tailFrames
      + this.inputHistory.byteLength
      + this.futureOutput.byteLength;
  }

  getMetrics(blocks = this.blocksProcessed) {
    const headCalls = this.head.fftCalls;
    const tailCalls = this.fftCalls - headCalls;
    const headProducts = this.head.partitionProducts;
    const tailProducts = this.partitionProducts - headProducts;
    return {
      mode: this.mode,
      blockSize: this.blockSize,
      latencySamples: this.latencySamples,
      latencyMs: this.getLatencySeconds() * 1000,
      partitionCount: this.partitionCount,
      levelCount: this.levelCount,
      headPartitions: this.head.partitionCount,
      tailPartitions: this.tailStages.length,
      fftCallsPerBlock: blocks ? this.fftCalls / blocks : 0,
      partitionProductsPerBlock: blocks ? this.partitionProducts / blocks : 0,
      tailPartitionProductsPerBlock: blocks ? tailProducts / blocks : 0,
      headPartitionProductsPerBlock: blocks ? headProducts / blocks : 0,
      footprintBytes: this.estimateFootprintBytes(),
      stages: this.tailStages.map((stage) => ({
        offset: stage.offset,
        length: stage.length,
        fftSize: stage.fftSize,
        strideBlocks: stage.strideBlocks,
      })),
      tailFftCalls: tailCalls,
    };
  }
}

export function createConvolver(mode, impulseResponse, options = {}) {
  const Convolver = mode === 'non-uniform'
    ? NonUniformPartitionedConvolver
    : UniformPartitionedConvolver;
  return new Convolver(options).loadImpulseResponse(impulseResponse);
}


