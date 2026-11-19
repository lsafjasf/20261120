import { createConvolver } from './partitioned-convolution.js';

const now = typeof performance !== 'undefined' && typeof performance.now === 'function'
  ? () => performance.now()
  : (typeof Date !== 'undefined' ? () => Date.now() : null);

class LowLatencyConvolutionProcessor extends AudioWorkletProcessor {
  constructor(options) {
    super();
    this.mode = options.processorOptions?.mode === 'non-uniform' ? 'non-uniform' : 'uniform';
    this.inputBlock = new Float32Array(128);
    this.outputBlock = new Float32Array(128);
    this.convolvers = {};
    this.blockTimes = [];
    this.reportCounter = 0;
    this.reportPeriod = 375;

    const irArray = options.processorOptions?.impulseResponse;
    if (irArray) {
      this.buildConvolvers(new Float32Array(irArray));
    }

    this.port.onmessage = (event) => {
      const message = event.data || {};
      if (message.type === 'set-mode' && this.convolvers[message.mode]) {
        this.mode = message.mode;
        this.convolver = this.convolvers[this.mode];
        this.resetConvolvers();
        this.port.postMessage({ type: 'mode', mode: this.mode });
      } else if (message.type === 'set-ir') {
        const samples = message.impulseResponse instanceof Float32Array
          ? message.impulseResponse
          : new Float32Array(message.impulseResponse);
        this.buildConvolvers(samples);
        this.resetConvolvers();
        this.port.postMessage({ type: 'mode', mode: this.mode });
      } else if (message.type === 'reset') {
        this.resetConvolvers();
      }
    };
  }

  buildConvolvers(impulseResponse) {
    this.impulseResponse = impulseResponse;
    this.convolvers.uniform = createConvolver('uniform', impulseResponse, {
      blockSize: 128,
      sampleRate,
    });
    this.convolvers['non-uniform'] = createConvolver('non-uniform', impulseResponse, {
      blockSize: 128,
      sampleRate,
    });
    this.convolver = this.convolvers[this.mode];
    this.port.postMessage({
      type: 'configured',
      sampleRate,
      impulseResponseSamples: impulseResponse.length,
      metrics: this.convolver.getMetrics(1),
    });
  }

  resetConvolvers() {
    this.convolvers.uniform?.reset();
    this.convolvers['non-uniform']?.reset();
    this.blockTimes.length = 0;
    this.reportCounter = 0;
  }

  emitMetrics() {
    if (this.blockTimes.length === 0) return;
    const sorted = Float64Array.from(this.blockTimes).sort();
    const meanUs = this.blockTimes.reduce((sum, value) => sum + value, 0) / this.blockTimes.length;
    const p99Us = sorted[Math.min(sorted.length - 1, Math.floor(sorted.length * 0.99))];
    const maxUs = sorted[sorted.length - 1];
    const budgetUs = (128 / sampleRate) * 1e6;
    this.port.postMessage({
      type: 'cpu',
      mode: this.mode,
      meanUs,
      p99Us,
      maxUs,
      cpuPercent: (meanUs / budgetUs) * 100,
      p99CpuPercent: (p99Us / budgetUs) * 100,
      blocks: this.blockTimes.length,
    });
    this.blockTimes.length = 0;
  }

  process(inputs, outputs) {
    const outputChannels = outputs[0];
    if (!outputChannels || outputChannels.length === 0) return true;
    const inputChannels = inputs[0];
    const input = inputChannels?.[0];

    if (input) {
      this.inputBlock.set(input);
    } else {
      this.inputBlock.fill(0);
    }

    const begin = now ? now() : 0;
    this.convolver.processBlock(this.inputBlock, this.outputBlock);
    const elapsedUs = now ? (now() - begin) * 1000 : 0;

    for (const channel of outputChannels) channel.set(this.outputBlock);
    if (now) {
      this.blockTimes.push(elapsedUs);
      this.reportCounter++;
      if (this.reportCounter >= this.reportPeriod) {
        this.reportCounter = 0;
        this.emitMetrics();
      }
    }
    return true;
  }
}

registerProcessor('low-latency-convolution', LowLatencyConvolutionProcessor);

