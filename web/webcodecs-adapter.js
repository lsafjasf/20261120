// WebCodecs bridge: feeds reassembled Annex-B access units into VideoDecoder and
// measures the total time spent between depacketizer output and decoder hand-off.

export function createH264Decoder({ onFrame, onError } = {}) {
  if (typeof VideoDecoder === 'undefined') {
    throw new Error('WebCodecs VideoDecoder is not available in this browser');
  }
  const decoder = new VideoDecoder({
    output: (videoFrame) => {
      if (onFrame) onFrame(videoFrame);
      videoFrame.close();
    },
    error: (err) => {
      if (onError) onError(err);
    },
  });
  decoder.configure({ codec: 'avc1.42001e', optimizeForLatency: true });
  return decoder;
}

export function createH264Encoder({ width = 320, height = 180, framerate = 30 } = {}) {
  if (typeof VideoEncoder === 'undefined') {
    throw new Error('WebCodecs VideoEncoder is not available in this browser');
  }
  const encodedChunks = [];
  const encoder = new VideoEncoder({
    output: (chunk) => encodedChunks.push(chunk),
    error: (err) => {
      throw err;
    },
  });
  encoder.configure({
    codec: 'avc1.42001e', // Constrained Baseline, level 3.0
    width,
    height,
    framerate,
    bitrate: 250_000,
    avc: { format: 'annexb' },
  });
  return {
    encoder,
    encodedChunks,
    width,
    height,
    makeFrame(seed) {
      const canvas = new OffscreenCanvas(width, height);
      const ctx = canvas.getContext('2d');
      ctx.fillStyle = `hsl(${(seed * 37) % 360} 70% 45%)`;
      ctx.fillRect(0, 0, width, height);
      ctx.fillStyle = '#fff';
      ctx.font = '28px sans-serif';
      ctx.fillText(`frame ${seed}`, 24, height / 2);
      return new VideoFrame(canvas, { timestamp: Math.round((seed * 1e6) / framerate) });
    },
  };
}

// Extract Annex-B bytes out of an EncodedVideoChunk.
export function chunkToBytes(chunk) {
  const buffer = new ArrayBuffer(chunk.byteLength);
  chunk.copyTo(buffer);
  return new Uint8Array(buffer);
}

// Pipeline timing tracker for "reassembly start -> frame handed to decoder".
export class PipelineTimer {
  constructor() {
    this.framesHanded = 0;
    this.reassemblyNs = 0n;
    this.handoverNs = 0n;
    this.firstAt = null;
    this.lastAt = null;
  }

  record(frame, handoverMs) {
    this.framesHanded += 1;
    for (const nal of frame.nals) {
      this.reassemblyNs += BigInt(Math.round(nal.reassemblyMs * 1e6));
    }
    this.handoverNs += BigInt(Math.round(handoverMs * 1e6));
    if (this.firstAt === null) this.firstAt = performance.now();
    this.lastAt = performance.now();
  }

  get totalMs() {
    return (Number(this.reassemblyNs) + Number(this.handoverNs)) / 1e6;
  }

  get reassemblyMs() {
    return Number(this.reassemblyNs) / 1e6;
  }

  get handoverMs() {
    return Number(this.handoverNs) / 1e6;
  }

  get wallMs() {
    return this.firstAt === null ? 0 : this.lastAt - this.firstAt;
  }
}
