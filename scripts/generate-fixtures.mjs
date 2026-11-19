import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const fixtureDir = join(root, "fixtures");

function vlq(value) {
  let buffer = value & 0x7f;
  while ((value >>= 7) > 0) {
    buffer <<= 8;
    buffer |= (value & 0x7f) | 0x80;
  }
  const bytes = [];
  while (true) {
    bytes.push(buffer & 0xff);
    if (buffer & 0x80) buffer >>= 8;
    else break;
  }
  return bytes;
}

function u16(value) {
  return [(value >> 8) & 0xff, value & 0xff];
}

function u32(value) {
  return [
    (value >>> 24) & 0xff,
    (value >>> 16) & 0xff,
    (value >>> 8) & 0xff,
    value & 0xff,
  ];
}

function ascii(value) {
  return Array.from(value, (character) => character.charCodeAt(0));
}

function meta(type, data = []) {
  return { status: 0xff, bytes: [0xff, type, ...vlq(data.length), ...data] };
}

function textMeta(type, text) {
  return meta(type, Array.from(text, (character) => character.charCodeAt(0)));
}

function chunk(id, data) {
  return new Uint8Array([...ascii(id), ...u32(data.length), ...data]);
}

function track(events) {
  const sorted = events
    .map((event, index) => ({ ...event, index }))
    .sort((left, right) => left.tick - right.tick || left.index - right.index);
  const data = [];
  let previousTick = 0;
  for (const event of sorted) {
    data.push(...vlq(event.tick - previousTick), ...event.bytes);
    previousTick = event.tick;
  }
  data.push(...vlq(0), ...meta(0x2f).bytes);
  return chunk("MTrk", data);
}

function midi(format, ppq, tracks) {
  return new Uint8Array([
    ...ascii("MThd"),
    ...u32(6),
    ...u16(format),
    ...u16(tracks.length),
    ...u16(ppq),
    ...tracks.flatMap((events) => Array.from(events)),
  ]);
}

const tempo = (tick, bpm) => {
  const microseconds = Math.round(60000000 / bpm);
  return {
    tick,
    bytes: [
      0xff,
      0x51,
      0x03,
      (microseconds >> 16) & 0xff,
      (microseconds >> 8) & 0xff,
      microseconds & 0xff,
    ],
  };
};

const noteOn = (tick, channel, note, velocity) => ({
  tick,
  bytes: [0x90 | channel, note, velocity],
});

const noteOff = (tick, channel, note, velocity = 0) => ({
  tick,
  bytes: [0x80 | channel, note, velocity],
});

const pedal = (tick, channel, value) => ({
  tick,
  bytes: [0xb0 | channel, 64, value],
});

const program = (tick, channel, value) => ({
  tick,
  bytes: [0xc0 | channel, value],
});

function writeFixture(name, bytes) {
  writeFileSync(join(fixtureDir, name), bytes);
}

mkdirSync(fixtureDir, { recursive: true });

const conductor = track([
  { tick: 0, bytes: textMeta(0x03, "Tempo & Pedal Test").bytes },
  tempo(0, 120),
  { tick: 0, bytes: meta(0x58, [4, 2, 24, 8]).bytes },
  tempo(1920, 60),
  { tick: 2880, bytes: textMeta(0x06, "END / 4.000s").bytes },
]);

const piano = track([
  { tick: 0, bytes: textMeta(0x03, "Piano Phrase").bytes },
  program(0, 0, 0),
  noteOn(0, 0, 60, 92),
  noteOn(0, 0, 64, 78),
  noteOff(960, 0, 60, 36),
  noteOn(960, 0, 60, 88),
  noteOn(960, 0, 67, 84),
  noteOn(960, 0, 65, 72),
  noteOff(1440, 0, 65, 32),
  noteOff(1920, 0, 60, 40),
  noteOff(1920, 0, 64, 40),
  noteOff(1920, 0, 67, 40),
  noteOn(2400, 0, 72, 96),
  noteOff(2880, 0, 72, 48),
]);

const pedalTrack = track([
  { tick: 0, bytes: textMeta(0x03, "Sustain Pedal").bytes },
  pedal(0, 0, 127),
  pedal(1920, 0, 0),
]);

writeFixture(
  "tempo-pedal-type1.mid",
  midi(1, 480, [conductor, piano, pedalTrack]),
);

const missingOff = track([
  { tick: 0, bytes: textMeta(0x03, "Missing Note Off").bytes },
  tempo(0, 120),
  pedal(0, 0, 127),
  program(0, 0, 4),
  noteOn(0, 0, 60, 80),
  noteOn(480, 0, 64, 76),
  noteOff(960, 0, 64, 32),
  pedal(1440, 0, 0),
]);

writeFixture("missing-off-type0.mid", midi(0, 480, [missingOff]));

const manifest = {
  generatedBy: "scripts/generate-fixtures.mjs",
  fixtures: [
    {
      file: "tempo-pedal-type1.mid",
      format: 1,
      ppq: 480,
      title: "速度变化处松踏板",
      checks: [
        { tick: 1920, seconds: 2, bpm: 60 },
        { tick: 2400, seconds: 3 },
        { tick: 2880, seconds: 4 },
      ],
    },
    {
      file: "missing-off-type0.mid",
      format: 0,
      ppq: 480,
      title: "缺少 Note Off",
      expectedAnomalies: ["missing-note-off"],
    },
  ],
};
writeFileSync(join(fixtureDir, "manifest.json"), `${JSON.stringify(manifest, null, 2)}\n`);

console.log("Generated fixtures in", fixtureDir);
