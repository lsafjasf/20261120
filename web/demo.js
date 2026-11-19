import { H264RtpPacker } from '../src/packer.js';
import { H264RtpDepacketizer } from '../src/depacketizer.js';
import { splitAnnexB } from '../src/nal.js';
import { routeOpusPacket, makeOpusPacket } from '../src/opus.js';
import {
  createH264Decoder,
  createH264Encoder,
  chunkToBytes,
  PipelineTimer,
} from './webcodecs-adapter.js';

const RTP_CLOCK = 90000;
const FRAME_RATE = 30;
const TIMESTAMP_STEP = RTP_CLOCK / FRAME_RATE; // 3000

export async function runDemo({ frameCount = 12, maxPayload = 300, log } = {}) {
  log('status', '1/4  使用 WebCodecs VideoEncoder 生成 H.264 (Annex-B) 帧…');
  const factory = createH264Encoder({ framerate: FRAME_RATE });
  for (let i = 0; i < frameCount; i += 1) {
    const frame = factory.makeFrame(i);
    factory.encoder.encode(frame, { keyFrame: i === 0 || i === 6 });
    frame.close();
  }
  await factory.encoder.flush();
  log('status', `编码完成：${factory.encodedChunks.length} 个 EncodedVideoChunk`);

  // Build RTP stream: NALs from each chunk -> packer.
  const packer = new H264RtpPacker({ maxPayload, payloadType: 96 });
  const stream = [];
  factory.encodedChunks.forEach((chunk, frameIndex) => {
    const nals = splitAnnexB(chunkToBytes(chunk));
    const timestamp = frameIndex * TIMESTAMP_STEP;
    for (const packet of packer.packFrame(nals, timestamp)) {
      stream.push({ packet, frameIndex, timestamp, nals: nals.length });
    }
  });
  log('status', `2/4  RTP 打包完成：${stream.length} 个包 (single=${packer.stats.singleNal}, STAP-A=${packer.stats.stapA}, FU-A=${packer.stats.fuA})`);

  // Simulate loss of one middle FU-A fragment belonging to frame 3.
  const lossTarget = chooseFuFragment(stream, 3);
  const lostSeqs = new Set();
  if (lossTarget >= 0) {
    lostSeqs.add(seqOf(stream[lossTarget].packet));
    log('loss', `模拟 FU-A 丢包：丢弃第 ${lossTarget} 个 RTP 包 (seq=${[...lostSeqs][0]}, frame=${stream[lossTarget].frameIndex})`);
  } else {
    log('loss', '未找到可丢弃的 FU-A 分片（增大帧数或减小 maxPayload 后重试）');
  }

  // Also drop every packet of one whole later frame to force a timestamp jump warning.
  const wholeFrameIndex = 9;
  const wholeFrameSeqs = stream
    .filter((item) => item.frameIndex === wholeFrameIndex)
    .map((item) => seqOf(item.packet));
  if (wholeFrameSeqs.length > 0) {
    wholeFrameSeqs.forEach((seq) => lostSeqs.add(seq));
    log('loss', `模拟整帧丢失：frame=${wholeFrameIndex} 的 ${wholeFrameSeqs.length} 个包全部丢弃（触发时戳跳变告警）`);
  }

  const surviving = stream.filter((item) => !lostSeqs.has(seqOf(item.packet)));
  return decodeStream(surviving, { log });
}

function chooseFuFragment(stream, frameIndex) {
  // FU header is at byte 13 (12 RTP header bytes + FU indicator).
  const isFu = (packet) => (packet[12] & 0x1f) === 28;
  const isIdr = (packet) => (packet[13] & 0x1f) === 5;
  const isMiddle = (packet) => (packet[13] & 0xc0) === 0; // neither START nor END

  const middleFragments = stream
    .map((item, index) => ({ item, index }))
    .filter(({ item }) => isFu(item.packet) && !isIdr(item.packet) && isMiddle(item.packet));
  const onFrame = middleFragments.filter(({ item }) => item.frameIndex === frameIndex);
  const pool = onFrame.length > 0 ? onFrame : middleFragments;
  if (pool.length === 0) return -1;
  return pool[Math.floor(pool.length / 2)].index;
}

function seqOf(rtpPacket) {
  return (rtpPacket[2] << 8) | rtpPacket[3];
}

async function decodeStream(stream, { log }) {
  const decoder = createH264Decoder({
    onFrame: (frame) => log('decoded', `VideoDecoder 输出帧 ${frame.timestamp} µs (${frame.codedWidth}x${frame.codedHeight})`),
    onError: (err) => log('decoder-error', `解码器错误（丢帧预期内）: ${err.message}`),
  });
  const depack = new H264RtpDepacketizer({ timestampStep: TIMESTAMP_STEP });
  const timer = new PipelineTimer();
  depack.onWarning = (event) => log('warn', `[${event.kind}] ${event.message}`);

  log('status', '3/4  重组并送入 WebCodecs VideoDecoder…');
  depack.onFrame = (frame) => {
    const handedAt = performance.now();
    const hasVcl = frame.nals.some((nal) => nal.vcl);
    const key = frame.isKeyframe ? 'key' : 'delta';
    const nalSummary = frame.nals
      .map((nal) => `#${nal.nalIndex}[${nal.source} ${nal.nalTypeName}${nal.vcl ? ` slice=${nal.sliceIndex}(${nal.sliceType})` : ''} ${nal.size}B ${nal.reassemblyMs.toFixed(3)}ms]`)
      .join(' ');
    log('frame', `ts=${frame.timestamp} ${key} NAL×${frame.nals.length} ${nalSummary}`);

    if (hasVcl) {
      const chunk = new EncodedVideoChunk({
        type: frame.isKeyframe ? 'key' : 'delta',
        timestamp: (frame.timestamp / RTP_CLOCK) * 1e6,
        data: frame.annexb,
      });
      decoder.decode(chunk);
    } else {
      log('frame', `ts=${frame.timestamp} 参数集帧（无 VCL NAL），仅记录不送解码器`);
    }
    timer.record(frame, performance.now() - handedAt);
  };

  for (const item of stream) {
    depack.push(item.packet);
  }
  try {
    await decoder.flush();
  } catch (err) {
    log('decoder-error', `flush 返回错误（参考帧丢失导致的预期行为）: ${err.message}；重置解码器并继续`);
    decoder.reset();
    decoder.configure({ codec: 'avc1.42001e', optimizeForLatency: true });
  }

  log('status', '4/4  解码完成');
  const result = {
    packetsSent: stream.length,
    depackStats: { ...depack.stats, reassemblyNsTotal: depack.stats.reassemblyNsTotal.toString() },
    warnings: depack.warnings,
    framesHanded: timer.framesHanded,
    handoverTotalMs: timer.totalMs,
    reassemblyMs: timer.reassemblyMs,
    decoderEnqueueMs: timer.handoverMs,
    handoverWallMs: timer.wallMs,
  };
  log('timing', `重组后交给解码器的总耗时：${timer.totalMs.toFixed(3)} ms（重组 ${timer.reassemblyMs.toFixed(3)} ms + 入队 ${timer.handoverMs.toFixed(3)} ms），覆盖 ${timer.framesHanded} 帧；挂钟跨度 ${timer.wallMs.toFixed(3)} ms`);
  runOpusMixedStream(log);
  return result;
}

// Mixed stream containing SILK-only, hybrid (SILK+CELT) and CELT-only Opus frames.
export function runOpusMixedStream(log) {
  const sequence = [
    { label: 'SILK NB 20ms', config: 1, expect: 'SILK' },
    { label: 'SILK WB 40ms', config: 10, expect: 'SILK' },
    { label: 'Hybrid SWB 10ms', config: 12, expect: 'HYBRID' },
    { label: 'Hybrid FB 20ms', config: 15, expect: 'HYBRID' },
    { label: 'CELT FB 20ms', config: 31, expect: 'CELT' },
    { label: 'CELT WB 10ms', config: 22, expect: 'CELT' },
  ];
  let misClassified = 0;
  log('opus', 'Opus 混合流解码路径判断（同一条流中含 CELT 与 SILK 帧）:');
  for (const item of sequence) {
    const routed = routeOpusPacket(makeOpusPacket(item.config, { stereo: true }));
    const ok = routed.decodePath === item.expect;
    if (!ok) misClassified += 1;
    log('opus', `  config=${String(item.config).padStart(2)} ${item.label.padEnd(16)} -> ${routed.decodePath.padEnd(6)} (${routed.bandwidth} ${routed.frameMs}ms ${routed.stereo ? 'stereo' : 'mono'}) -> ${routed.decoder} ${ok ? '✓' : '✗ 错判'}`);
  }
  log('opus', `混合流判断结束：${sequence.length} 帧，错判 ${misClassified} 个`);
  return misClassified;
}
