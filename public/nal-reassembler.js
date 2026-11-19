// NalReassembler: reassembles fragmented NAL units (RTP FU-A style) from a
// lossy packet stream. Incomplete NALs are dropped when the next NAL starts
// (or on flush), so a damaged frame never poisons the following keyframe.
class NalReassembler {
  constructor() {
    this.pending = null; // { frameId, nalType, isKey, frags: Map, fragCount }
    this.stats = { reassembled: 0, dropped: 0, packetsReceived: 0, packetsDiscarded: 0 };
    this.completedNals = [];
  }

  // packet: { frameId, nalType, isKey, fragIndex, fragCount, payload: Uint8Array }
  pushPacket(packet) {
    this.stats.packetsReceived += 1;
    const p = this.pending;

    if (p && p.frameId !== packet.frameId) {
      // A new NAL started before the previous one completed: drop it.
      this._dropPending('interrupted-by-frame-' + packet.frameId);
    }
    if (!this.pending) {
      if (packet.fragIndex !== 0) {
        // Mid-NAL fragment without a start: stray packet, discard.
        this.stats.packetsDiscarded += 1;
        return null;
      }
      this.pending = {
        frameId: packet.frameId,
        nalType: packet.nalType,
        isKey: packet.isKey,
        fragCount: packet.fragCount,
        frags: new Map(),
      };
    }
    const cur = this.pending;
    if (cur.fragCount !== packet.fragCount || cur.frags.has(packet.fragIndex)) {
      this.stats.packetsDiscarded += 1;
      return null;
    }
    cur.frags.set(packet.fragIndex, packet.payload);

    if (cur.frags.size === cur.fragCount) {
      const nal = this._complete();
      return nal;
    }
    return null;
  }

  flush(reason) {
    if (this.pending) this._dropPending(reason || 'flush');
  }

  _complete() {
    const p = this.pending;
    let size = 0;
    for (let i = 0; i < p.fragCount; i++) size += p.frags.get(i).length;
    const data = new Uint8Array(size);
    let off = 0;
    for (let i = 0; i < p.fragCount; i++) {
      data.set(p.frags.get(i), off);
      off += p.frags.get(i).length;
    }
    const nal = { frameId: p.frameId, nalType: p.nalType, isKey: p.isKey, data };
    this.pending = null;
    this.stats.reassembled += 1;
    this.completedNals.push(nal);
    return nal;
  }

  _dropPending(reason) {
    this.stats.dropped += 1;
    this.lastDrop = {
      frameId: this.pending.frameId,
      nalType: this.pending.nalType,
      isKey: this.pending.isKey,
      reason,
    };
    this.pending = null;
  }
}

// Simulated decoder: needs an IDR keyframe to (re)start decoding; a dropped
// non-IDR NAL freezes decoding until the next keyframe arrives.
class SimDecoder {
  constructor() {
    this.state = 'waiting-for-keyframe';
    this.decodedFrames = [];
    this.events = [];
  }

  accept(nal) {
    if (nal.isKey) {
      if (this.state !== 'decoding') {
        this.events.push(`关键帧到来，解码恢复 (frame ${nal.frameId})`);
      }
      this.state = 'decoding';
      this.decodedFrames.push(nal.frameId);
      return true;
    }
    if (this.state === 'decoding') {
      this.decodedFrames.push(nal.frameId);
      return true;
    }
    this.events.push(`非关键帧被跳过，等待关键帧 (frame ${nal.frameId})`);
    return false;
  }

  notifyDrop(drop) {
    if (this.state === 'decoding' && !drop.isKey) {
      this.state = 'waiting-for-keyframe';
      this.events.push(`帧 ${drop.frameId} 重组失败被丢弃，等待下一关键帧`);
    }
  }
}

if (typeof module !== 'undefined') {
  module.exports = { NalReassembler, SimDecoder };
}
