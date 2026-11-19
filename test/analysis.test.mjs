import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { parseMidi } from "../src/midi.js";
import { analyzeMidi } from "../src/analysis.js";
import { encodeWav, synthesizePcm } from "../src/synth.js";

const fixture = (name) =>
  readFileSync(new URL(`../fixtures/${name}`, import.meta.url));

function runningStatusMidi() {
  return new Uint8Array([
    0x4d, 0x54, 0x68, 0x64, 0x00, 0x00, 0x00, 0x06,
    0x00, 0x00, 0x00, 0x01, 0x01, 0xe0,
    0x4d, 0x54, 0x72, 0x6b, 0x00, 0x00, 0x00, 0x0b,
    0x00, 0x90, 0x3c, 0x40,
    0x00, 0x3c, 0x00,
    0x00, 0xff, 0x2f, 0x00,
  ]);
}

test("parses Type 1 and converts segmented tempo to playback seconds", () => {
  const analysis = analyzeMidi(parseMidi(fixture("tempo-pedal-type1.mid")));
  assert.equal(analysis.format, 1);
  assert.equal(analysis.ppq, 480);
  assert.equal(analysis.tracks.length, 3);
  assert.equal(analysis.durationSeconds, 4);
  assert.ok(Math.abs(analysis.tempoMap.tickToSeconds(1920) - 2) < 1e-9);
  assert.ok(Math.abs(analysis.tempoMap.tickToSeconds(2400) - 3) < 1e-9);
  assert.ok(Math.abs(analysis.tempoMap.tickToSeconds(2880) - 4) < 1e-9);
  assert.equal(analysis.tempoMap.tempoAtTick(1919).bpm, 120);
  assert.equal(analysis.tempoMap.tempoAtTick(1920).bpm, 60);
});

test("merges same-tick events as pedal up, note off, then note on", () => {
  const analysis = analyzeMidi(parseMidi(fixture("tempo-pedal-type1.mid")));
  const atChange = analysis.events.filter((event) => event.tick === 1920);
  assert.equal(atChange[0].type, "tempo");
  assert.equal(atChange[1].type, "controlChange");
  assert.equal(atChange[1].data.value, 0);
  const noteEvents = atChange.filter(
    (event) => event.type === "noteOff" || event.type === "noteOn",
  );
  assert.ok(
    noteEvents.length > 0 && noteEvents.every((event) => event.type === "noteOff"),
    "all note-offs at the tempo point must precede any note-on",
  );

  const retriggerPoint = analysis.events.filter((event) => event.tick === 960);
  const noteOffIndex = retriggerPoint.findIndex((event) => event.type === "noteOff");
  const noteOnIndex = retriggerPoint.findIndex((event) => event.type === "noteOn");
  assert.ok(noteOffIndex >= 0 && noteOnIndex > noteOffIndex);
});

test("keeps same-pitch retriggers independent and does not leak sustain", () => {
  const analysis = analyzeMidi(parseMidi(fixture("tempo-pedal-type1.mid")));
  const c4 = analysis.notes.filter((note) => note.name === "C4");
  assert.equal(c4.length, 2);
  assert.deepEqual(
    c4.map((note) => [note.startTick, note.endTick]),
    [
      [0, 960],
      [960, 1920],
    ],
  );
  assert.ok(c4.every((note) => note.sustainEndTick === null));
  assert.equal(analysis.stats.sustainCount, 1);
  const f4 = analysis.notes.find((note) => note.name === "F4");
  assert.deepEqual([f4.startTick, f4.endTick, f4.sustainEndTick], [960, 1440, 1920]);
  assert.equal(analysis.anomalies.length, 0);
});

test("reports a missing Note Off in Type 0 with source event trace", () => {
  const analysis = analyzeMidi(parseMidi(fixture("missing-off-type0.mid")));
  assert.equal(analysis.format, 0);
  assert.equal(analysis.tracks.length, 1);
  const missing = analysis.anomalies.filter(
    (anomaly) => anomaly.code === "missing-note-off",
  );
  assert.equal(missing.length, 1);
  assert.equal(missing[0].trackIndex, 0);
  assert.equal(missing[0].eventNumber, 5);
  const note = analysis.notes.find((item) => item.missingNoteOff);
  assert.equal(note.name, "C4");
  assert.equal(note.endTick, analysis.maxTick);
});

test("supports running status in channel events", () => {
  const parsed = parseMidi(runningStatusMidi());
  assert.equal(parsed.format, 0);
  assert.equal(parsed.tracks[0].events[0].type, "noteOn");
  assert.equal(parsed.tracks[0].events[1].type, "noteOn");
  assert.equal(parsed.tracks[0].events[1].data.velocity, 0);
});

test("preserves same-track order for a same-tick zero-length note", () => {
  const analysis = analyzeMidi(parseMidi(runningStatusMidi()));
  assert.equal(analysis.notes.length, 1);
  assert.equal(analysis.notes[0].startTick, 0);
  assert.equal(analysis.notes[0].endTick, 0);
  assert.ok(analysis.notes[0].anomalyCodes.includes("zero-length"));
  assert.equal(
    analysis.anomalies.filter((item) => item.code === "orphan-note-off").length,
    0,
  );
});

test("renders a valid PCM WAV header and payload", () => {
  const analysis = analyzeMidi(parseMidi(fixture("missing-off-type0.mid")));
  const pcm = synthesizePcm(analysis, { sampleRate: 4000, maxSeconds: 1 });
  const wav = encodeWav(pcm);
  assert.equal(Buffer.from(wav.slice(0, 4)).toString(), "RIFF");
  assert.equal(Buffer.from(wav.slice(8, 12)).toString(), "WAVE");
  assert.equal(wav.length, 44 + 4000 * 2 * 2);
});
