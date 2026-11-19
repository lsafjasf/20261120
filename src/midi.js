const NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];

export function noteName(pitch) {
  const value = Math.max(0, Math.min(127, Number(pitch)));
  return `${NOTE_NAMES[value % 12]}${Math.floor(value / 12) - 1}`;
}

function decodeText(bytes) {
  try {
    return new TextDecoder("utf-8").decode(bytes);
  } catch {
    return Array.from(bytes, (byte) => String.fromCharCode(byte)).join("");
  }
}

class ByteReader {
  constructor(buffer) {
    this.bytes = new Uint8Array(buffer);
    this.pos = 0;
  }

  get remaining() {
    return this.bytes.length - this.pos;
  }

  ensure(length) {
    if (this.pos + length > this.bytes.length) {
      throw new Error(`MIDI 数据在偏移 ${this.pos} 处意外结束`);
    }
  }

  u8() {
    this.ensure(1);
    return this.bytes[this.pos++];
  }

  u16() {
    this.ensure(2);
    const value = (this.bytes[this.pos] << 8) | this.bytes[this.pos + 1];
    this.pos += 2;
    return value;
  }

  u32() {
    this.ensure(4);
    const value =
      this.bytes[this.pos] * 0x1000000 +
      (this.bytes[this.pos + 1] << 16) +
      (this.bytes[this.pos + 2] << 8) +
      this.bytes[this.pos + 3];
    this.pos += 4;
    return value >>> 0;
  }

  ascii(length) {
    this.ensure(length);
    const value = String.fromCharCode(...this.bytes.slice(this.pos, this.pos + length));
    this.pos += length;
    return value;
  }

  slice(length) {
    this.ensure(length);
    const value = this.bytes.slice(this.pos, this.pos + length);
    this.pos += length;
    return value;
  }

  vlq() {
    let value = 0;
    for (let index = 0; index < 4; index += 1) {
      const byte = this.u8();
      value = (value << 7) | (byte & 0x7f);
      if ((byte & 0x80) === 0) return value;
    }
    throw new Error("无效的 MIDI 可变长度数值");
  }
}

function eventLabel(event) {
  if (event.type === "noteOn") return `On ${noteName(event.data.note)} · v${event.data.velocity}`;
  if (event.type === "noteOff") return `Off ${noteName(event.data.note)} · v${event.data.velocity ?? 0}`;
  if (event.type === "controlChange") {
    if (event.data.controller === 64) return `CC64 ${event.data.value >= 64 ? "踏板按下" : "踏板释放"}`;
    return `CC${event.data.controller} · ${event.data.value}`;
  }
  if (event.type === "tempo") return `${Math.round(event.data.bpm * 1000) / 1000} BPM`;
  if (event.type === "timeSignature") {
    return `${event.data.numerator}/${event.data.denominator}`;
  }
  if (event.type === "trackName") return `轨名「${event.data.name}」`;
  if (event.type === "programChange") return `音色 ${event.data.program + 1}`;
  if (event.type === "endOfTrack") return "轨道结束";
  if (event.type === "sysex") return `SysEx · ${event.data.length} bytes`;
  return event.type;
}

function parseTrack(bytes, trackIndex) {
  const reader = new ByteReader(bytes);
  const events = [];
  let tick = 0;
  let runningStatus = null;
  let eventNumber = 0;

  const push = (type, payload) => {
    eventNumber += 1;
    const event = {
      id: `t${trackIndex}e${eventNumber}`,
      trackIndex,
      eventNumber,
      tick,
      type,
      channel: payload.channel ?? null,
      data: payload.data ?? {},
      sourceOrder: eventNumber,
    };
    event.label = eventLabel(event);
    events.push(event);
  };

  while (reader.remaining > 0) {
    const delta = reader.vlq();
    tick += delta;
    let status = reader.bytes[reader.pos];

    if (status === undefined) {
      throw new Error(`轨道 ${trackIndex + 1} 在 tick ${tick} 后缺少事件`);
    }

    if (status < 0x80) {
      if (runningStatus === null) {
        throw new Error(`轨道 ${trackIndex + 1} 在 tick ${tick} 使用了无效 running status`);
      }
      status = runningStatus;
    } else {
      reader.pos += 1;
      if (status < 0xf0) runningStatus = status;
    }

    if (status === 0xff) {
      const metaType = reader.u8();
      const length = reader.vlq();
      const data = reader.slice(length);
      runningStatus = null;

      if (metaType === 0x51 && length === 3) {
        const microseconds = (data[0] << 16) | (data[1] << 8) | data[2];
        push("tempo", {
          data: { microseconds, bpm: 60000000 / microseconds },
        });
      } else if (metaType === 0x03) {
        push("trackName", { data: { name: decodeText(data) } });
      } else if (metaType === 0x58 && length >= 2) {
        push("timeSignature", {
          data: {
            numerator: data[0],
            denominator: 2 ** data[1],
            clocks: data[2] ?? null,
            thirtySeconds: data[3] ?? null,
          },
        });
      } else if (metaType === 0x2f) {
        push("endOfTrack", { data: {} });
        break;
      } else {
        push("meta", {
          data: { metaType, length, text: length < 96 ? decodeText(data) : "" },
        });
      }
      continue;
    }

    if (status === 0xf0 || status === 0xf7) {
      const length = reader.vlq();
      reader.slice(length);
      runningStatus = null;
      push("sysex", { data: { length } });
      continue;
    }

    if (status >= 0xf1 && status <= 0xf6) {
      throw new Error(`轨道 ${trackIndex + 1} 包含不支持的 SMF 系统状态 0x${status.toString(16)}`);
    }

    if (status < 0x80) {
      throw new Error(`轨道 ${trackIndex + 1} 在 tick ${tick} 出现未知状态字节`);
    }

    const command = status >> 4;
    const channel = status & 0x0f;
    const dataLength = command === 0x0c || command === 0x0d ? 1 : 2;
    const first = reader.u8();
    const second = dataLength === 2 ? reader.u8() : 0;

    if (command === 0x08) {
      push("noteOff", { channel, data: { note: first, velocity: second } });
    } else if (command === 0x09) {
      push("noteOn", { channel, data: { note: first, velocity: second } });
    } else if (command === 0x0a) {
      push("polyAftertouch", { channel, data: { note: first, value: second } });
    } else if (command === 0x0b) {
      push("controlChange", { channel, data: { controller: first, value: second } });
    } else if (command === 0x0c) {
      push("programChange", { channel, data: { program: first } });
    } else if (command === 0x0d) {
      push("channelPressure", { channel, data: { value: first } });
    } else if (command === 0x0e) {
      push("pitchBend", { channel, data: { value: (second << 7) | first } });
    }
  }

  return events;
}

export function parseMidi(arrayBuffer) {
  const reader = new ByteReader(arrayBuffer);
  const headerId = reader.ascii(4);
  if (headerId !== "MThd") throw new Error("不是 Standard MIDI File：缺少 MThd 头");
  const headerLength = reader.u32();
  if (headerLength < 6) throw new Error("MIDI 头长度无效");
  const format = reader.u16();
  const declaredTracks = reader.u16();
  const division = reader.u16();
  if (division & 0x8000) throw new Error("暂不支持 SMPTE 时间制式，请使用 PPQ MIDI");
  if (![0, 1].includes(format)) throw new Error(`不支持 MIDI Type ${format}，请使用 Type 0 或 Type 1`);
  if (division === 0) throw new Error("MIDI 的 PPQ 时间基准不能为 0");
  reader.slice(headerLength - 6);

  const tracks = [];
  while (reader.remaining >= 8) {
    const chunkId = reader.ascii(4);
    const chunkLength = reader.u32();
    const chunk = reader.slice(chunkLength);
    if (chunkId !== "MTrk") continue;

    const trackIndex = tracks.length;
    const events = parseTrack(chunk, trackIndex);
    const nameEvent = events.find((event) => event.type === "trackName");
    tracks.push({
      index: trackIndex,
      name: nameEvent?.data.name || `轨道 ${trackIndex + 1}`,
      eventCount: events.length,
      events,
    });
  }

  if (tracks.length === 0) throw new Error("MIDI 中没有找到 MTrk 轨道");
  if (format === 0 && tracks.length > 1) {
    throw new Error("文件标记为 Type 0，但包含多个轨道");
  }

  return {
    format,
    declaredTracks,
    ppq: division,
    tracks,
    trackCount: tracks.length,
  };
}
