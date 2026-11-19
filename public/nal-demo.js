// Drives a lossy packet stream through NalReassembler + SimDecoder and
// returns packet-level reassembly counts. A P-frame loses one fragment and
// must be dropped without disturbing the following IDR keyframe.
function runNalReassemblyDemo() {
  const reassembler = new NalReassembler();
  const decoder = new SimDecoder();

  // frame plan: [frameId, nalType, isKey, fragCount, lostFragIndexes]
  const plan = [
    [1, 'IDR', true, 3, []],
    [2, 'P', false, 3, []],
    [3, 'P', false, 4, [1]], // fragment #1 lost -> incomplete -> dropped
    [4, 'P', false, 3, []],  // arrives while decoder waits for keyframe
    [5, 'IDR', true, 3, []], // next keyframe: must reassemble + decode cleanly
    [6, 'P', false, 3, []],
  ];

  const packets = [];
  for (const [frameId, nalType, isKey, fragCount, lost] of plan) {
    for (let i = 0; i < fragCount; i++) {
      if (lost.includes(i)) continue; // simulate packet loss
      packets.push({
        frameId, nalType, isKey,
        fragIndex: i, fragCount,
        payload: new Uint8Array([frameId, i, 0xAB, 0xCD]),
      });
    }
  }

  const log = [];
  for (const packet of packets) {
    const before = reassembler.stats.dropped;
    const nal = reassembler.pushPacket(packet);
    if (reassembler.stats.dropped > before && reassembler.lastDrop) {
      decoder.notifyDrop(reassembler.lastDrop);
      log.push(`drop: frame ${reassembler.lastDrop.frameId} (${reassembler.lastDrop.reason})`);
    }
    if (nal) decoder.accept(nal);
  }
  reassembler.flush('end-of-stream');

  const keyframe5Ok = decoder.decodedFrames.includes(5);
  const afterKeyResume = decoder.decodedFrames.join(',');

  return {
    packetsSent: packets.length,
    reassembled: reassembler.stats.reassembled,
    dropped: reassembler.stats.dropped,
    packetsDiscarded: reassembler.stats.packetsDiscarded,
    decodedFrames: decoder.decodedFrames.slice(),
    decoderEvents: decoder.events,
    drops: log,
    nextKeyframeRecovered: keyframe5Ok && decoder.decodedFrames.includes(6),
    summary: `重组成功 ${reassembler.stats.reassembled} 个 NAL，重组失败丢弃 ${reassembler.stats.dropped} 个；` +
      `解码帧序列 [${afterKeyResume}]，关键帧 5 之后解码${keyframe5Ok ? '已恢复' : '未恢复'}`,
  };
}

if (typeof module !== 'undefined') {
  module.exports = { runNalReassemblyDemo };
}
