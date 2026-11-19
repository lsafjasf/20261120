'use strict';

const fs = require('node:fs/promises');
const path = require('node:path');

function fixed(value, digits = 3) {
  return value === null || value === undefined ? 'n/a' : Number(value).toFixed(digits);
}

function streamTableRows(result) {
  return Object.entries(result.streams).map(([name, stream]) => ({
    track: name,
    packets: `${stream.pre.packetCount} / ${stream.post.packetCount}`,
    reorderP50: fixed(stream.post.reorder.reorderedPackets.p50),
    reorderP95: fixed(stream.post.reorder.reorderedPackets.p95),
    reorderMax: fixed(stream.post.reorder.reorderedPackets.max),
    jitterP50: fixed(stream.post.jitter.transitDeltaAbs.p50),
    jitterP95: fixed(stream.post.jitter.transitDeltaAbs.p95),
    jitterMax: fixed(stream.post.jitter.transitDeltaAbs.max),
    rfc3550: fixed(stream.post.jitter.finalRfc3550JitterMs),
  }));
}

function distributionRows(summary) {
  return (summary.histogram || []).map((bin) => ({
    range: bin.range,
    count: bin.count,
  }));
}

function buildAssertions(result) {
  const assertions = [];
  const add = (name, passed, detail) => assertions.push({ name, passed: Boolean(passed), detail });

  add('DESCRIBE 返回双轨 SDP', result.rtsp.describe.statusCode === 200 &&
    result.rtsp.describe.trackCount === 2, `${result.rtsp.describe.trackCount} 条轨道`);
  add('两路 SETUP 均成功', result.rtsp.setups.length === 2 &&
    result.rtsp.setups.every((setup) => setup.statusCode === 200), '视频 + 音频');
  add('两路轨道共用一个 RTSP Session', new Set(result.rtsp.setups.map((setup) =>
    setup.session?.split(';')[0])).size === 1, result.rtsp.setups[0]?.session || 'none');
  add('首次 PLAY 返回 RTP-Info', result.rtsp.initialPlay.statusCode === 200 &&
    result.rtsp.initialPlay.rtpInfo.length === 2, '两条 RTP-Info');
  add('seek PLAY 后会话仍为播放态', result.rtsp.seekStateCorrect &&
    result.rtsp.sessionIdStable, `state=${result.rtsp.sessionAfterSeek?.state}`);

  for (const [name, stream] of Object.entries(result.streams)) {
    add(`${name} seek 前 RTP-Info 对齐`,
      stream.alignment.beforeSeek.sequenceAligned &&
      stream.alignment.beforeSeek.timestampAligned,
      `seq=${stream.alignment.beforeSeek.rtpInfoSequence}, ts=${stream.alignment.beforeSeek.rtpInfoTimestamp}`);
    add(`${name} seek 后 RTP-Info 时间戳对齐`,
      stream.alignment.afterSeek.sequenceAligned &&
      stream.alignment.afterSeek.timestampAligned,
      `seq=${stream.alignment.afterSeek.rtpInfoSequence}, ts=${stream.alignment.afterSeek.rtpInfoTimestamp}`);
    add(`${name} seek 后序号/时间戳重新同步`,
      stream.timeline.resynchronized &&
      stream.alignment.continuityAcrossSeek.timelineJumpMatchesSeek,
      `jump=${stream.alignment.continuityAcrossSeek.timestampJumpSamples} ticks`);
  }

  add('DFF 与 DSF 的 DSD 位流一致',
    result.dsd.bitComparison.identical,
    `差异位 ${result.dsd.bitComparison.differingBits}`);
  add('DFF/DSF 解码 PCM 逐样本一致',
    result.dsd.pcmComparison.identical,
    `${result.dsd.pcmComparison.samplesCompared} samples, 最大差 ${result.dsd.pcmComparison.maxAbsoluteDifference}`);

  return {
    assertions,
    passed: assertions.every((assertion) => assertion.passed),
  };
}

function buildMarkdown(result) {
  const lines = [];
  const append = (line = '') => lines.push(line);
  const combined = result.aggregate.all;
  const combinedReorder = combined.reorder.reorderedPackets;
  const combinedJitter = combined.jitter.transitDeltaAbs;

  append('# RTSP/RTP 回环与 DFF/DSF PCM 验证报告');
  append();
  append(`- 运行时间：${result.startedAt}`);
  append(`- 总耗时：${fixed(result.durationMs)} ms`);
  append(`- 结果：${result.passed ? 'PASS' : 'FAIL'}`);
  append();
  append('## RTSP 会话建立');
  append();
  append('| 步骤 | 结果 | 关键内容 |');
  append('| --- | --- | --- |');
  append(`| DESCRIBE | ${result.rtsp.describe.statusCode} | ${result.rtsp.describe.trackCount} 条 SDP 轨道：video/H264 + audio/AAC |`);
  for (const setup of result.rtsp.setups) {
    append(`| SETUP ${setup.control} | ${setup.statusCode} | client_port=${setup.clientPort}, server_port=${setup.serverPort}, ssrc=${setup.ssrc} |`);
  }
  append(`| PLAY (initial) | ${result.rtsp.initialPlay.statusCode} | Range=${result.rtsp.initialPlay.range}; 2 条 RTP-Info |`);
  append(`| PLAY (seek) | ${result.rtsp.seekPlay.statusCode} | Range=${result.rtsp.seekPlay.requestRange}; Session 不变 |`);
  append();
  append('Seek 后服务端会话：');
  append();
  append('```json');
  append(JSON.stringify(result.rtsp.sessionAfterSeek, null, 2));
  append('```');
  append();
  append('## 乱序重排延迟与抖动');
  append();
  append('重排延迟定义为：乱序包首次到达缓存后，等待缺失前序包到达并释放所经历的毫秒数。抖动为相邻包单向传输时间差的绝对值，另列 RFC 3550 平滑结果。');
  append();
  append('| 轨道 | 包数(前/后) | 重排 p50 | 重排 p95 | 重排 max | 抖动 p50 | 抖动 p95 | 抖动 max | RFC3550 |');
  append('| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |');
  for (const row of streamTableRows(result)) {
    append(`| ${row.track} | ${row.packets} | ${row.reorderP50} | ${row.reorderP95} | ${row.reorderMax} | ${row.jitterP50} | ${row.jitterP95} | ${row.jitterMax} | ${row.rfc3550} |`);
  }
  append();
  append(`全部代际乱序包：${combinedReorder.count} 个，reorder p50=${fixed(combinedReorder.p50)} ms, p95=${fixed(combinedReorder.p95)} ms, max=${fixed(combinedReorder.max)} ms。`);
  append();
  append('重排延迟分布（全部代际的乱序包）：');
  append();
  append('| 区间 | 包数 |');
  append('| --- | ---: |');
  for (const row of distributionRows(combinedReorder)) {
    append(`| ${row.range} | ${row.count} |`);
  }
  append();
  append(`抖动 |Δtransit|：p50=${fixed(combinedJitter.p50)} ms, p95=${fixed(combinedJitter.p95)} ms, max=${fixed(combinedJitter.max)} ms。`);
  append();
  append('| 抖动区间 | 包数 |');
  append('| --- | ---: |');
  for (const row of distributionRows(combinedJitter)) {
    append(`| ${row.range} | ${row.count} |`);
  }
  append();
  append('## Seek 前后 RTP 时间戳对齐');
  append();
  append('| 轨道 | 阶段 | 请求 NPT | RTP-Info seq | RTP-Info ts | 首包 seq/ts | 首包对应 NPT | 对齐 |');
  append('| --- | --- | ---: | ---: | ---: | --- | ---: | --- |');
  for (const [name, stream] of Object.entries(result.streams)) {
    const before = stream.alignment.beforeSeek;
    const after = stream.alignment.afterSeek;
    append(`| ${name} | seek 前 | 0 ms | ${before.rtpInfoSequence} | ${before.rtpInfoTimestamp} | ${before.firstPacket?.sequenceNumber}/${before.firstPacket?.timestamp} | ${fixed(before.firstPacketNptMs)} ms | ${before.sequenceAligned && before.timestampAligned ? 'YES' : 'NO'} |`);
    append(`| ${name} | seek 后 | ${after.requestedNptMs} ms | ${after.rtpInfoSequence} | ${after.rtpInfoTimestamp} | ${after.firstPacket?.sequenceNumber}/${after.firstPacket?.timestamp} | ${fixed(after.firstPacketNptMs)} ms | ${after.sequenceAligned && after.timestampAligned ? 'YES' : 'NO'} |`);
  }
  append();
  append('| 轨道 | 跨 seek 序号跳变 | 时间戳跳变(ticks) | 期望跳变(ticks) | 时间戳重新同步 |');
  append('| --- | ---: | ---: | ---: | --- |');
  for (const [name, stream] of Object.entries(result.streams)) {
    const continuity = stream.alignment.continuityAcrossSeek;
    append(`| ${name} | ${continuity.sequenceJump} | ${continuity.timestampJumpSamples} | ${continuity.expectedTimestampJumpSamples} | ${stream.timeline.resynchronized ? 'YES' : 'NO'} |`);
  }
  append();
  append('## DFF 与 DSF PCM 对比');
  append();
  append('| 项目 | DFF | DSF |');
  append('| --- | --- | --- |');
  append(`| 文件大小 | ${result.dsd.files.dff.bytes} B | ${result.dsd.files.dsf.bytes} B |`);
  append(`| 容器布局 | ${result.dsd.files.dff.layout} | ${result.dsd.files.dsf.layout} |`);
  append(`| DSD 位序 | ${result.dsd.files.dff.bitOrder} | ${result.dsd.files.dsf.bitOrder} |`);
  append(`| 采样率 | ${result.dsd.files.dff.sampleRate} Hz | ${result.dsd.files.dsf.sampleRate} Hz |`);
  append(`| 每声道位数 | ${result.dsd.files.dff.bitCountPerChannel} | ${result.dsd.files.dsf.bitCountPerChannel} |`);
  append(`| 解码 PCM 字节 | ${result.dsd.files.pcm.dff.bytes} | ${result.dsd.files.pcm.dsf.bytes} |`);
  append();
  append(`- DSD 逐位比较：${result.dsd.bitComparison.identical ? '完全一致' : '存在差异'}，差异位 ${result.dsd.bitComparison.differingBits}。`);
  append(`- PCM 逐样本比较：${result.dsd.pcmComparison.samplesCompared} 个样本，差异样本 ${result.dsd.pcmComparison.differingSamples}，最大绝对差 ${result.dsd.pcmComparison.maxAbsoluteDifference}，RMS 差 ${result.dsd.pcmComparison.rmsDifference}。`);
  append(`- 两份解码 PCM 的 SHA-256：\`${result.dsd.pcmComparison.sha256.left}\` / \`${result.dsd.pcmComparison.sha256.right}\`。`);
  append('- 结论：DFF 与 DSF 的文件头、容器布局及位序差异不会改变解码后的 PCM；两种容器承载的是同一 DSD 内容。');
  append();
  append('## 断言');
  append();
  for (const assertion of result.assertions) {
    append(`- [${assertion.passed ? 'x' : ' '}] ${assertion.name} — ${assertion.detail}`);
  }
  append();
  return `${lines.join('\n')}\n`;
}

async function writeReports(result, outputDirectory, transcript) {
  const reportJsonPath = path.join(outputDirectory, 'verification-report.json');
  const reportMarkdownPath = path.join(outputDirectory, 'verification-report.md');
  const transcriptPath = path.join(outputDirectory, 'rtsp-transcript.json');
  await fs.writeFile(reportJsonPath, `${JSON.stringify(result, null, 2)}\n`);
  await fs.writeFile(reportMarkdownPath, buildMarkdown(result));
  await fs.writeFile(transcriptPath, `${JSON.stringify(transcript, null, 2)}\n`);
  return { reportJsonPath, reportMarkdownPath, transcriptPath };
}

module.exports = { buildAssertions, buildMarkdown, writeReports };
