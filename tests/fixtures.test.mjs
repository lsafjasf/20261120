import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { decodeEdges, parseEdgeCsv } from "../src/aes3.js";

const root = new URL("../data/scenarios/", import.meta.url);

test("generated fixture manifest contains passing validation suites", async () => {
  const manifest = JSON.parse(await readFile(new URL("manifest.json", root), "utf8"));
  assert.equal(manifest.sampleRate, 8000);
  assert.equal(manifest.frames, 256);
  assert.deepEqual(manifest.tones, { left: 1000, right: 1500 });
  assert.equal(Object.keys(manifest.scenarios).length, 5);
  for (const scenario of Object.values(manifest.scenarios)) {
    assert.equal(scenario.validation.passed, true, scenario.title);
    assert.ok(scenario.validation.checks.length >= 4);
  }
});

test("checked-in edge CSVs decode to their manifest summaries", async () => {
  const manifest = JSON.parse(await readFile(new URL("manifest.json", root), "utf8"));
  for (const [name, scenario] of Object.entries(manifest.scenarios)) {
    const csv = await readFile(new URL(scenario.file, root), "utf8");
    const decoded = decodeEdges(parseEdgeCsv(csv).edges);
    assert.equal(decoded.summary.edgeCount, scenario.edgeCount, `${name} edge count`);
    assert.equal(decoded.summary.frameCount, scenario.summary.frameCount, `${name} frames`);
    assert.equal(decoded.summary.parityErrorCount, scenario.summary.parityErrorCount, `${name} parity`);
    assert.equal(decoded.summary.dropoutCount, scenario.summary.dropoutCount, `${name} dropout`);
    assert.deepEqual(
      decoded.channelStatus ? { A: decoded.channelStatus.A.label, B: decoded.channelStatus.B.label } : null,
      { A: "L", B: "R" }
    );
  }
});

test("checked-in decoded WAV files are stereo 24-bit fixtures", async () => {
  const manifest = JSON.parse(await readFile(new URL("manifest.json", root), "utf8"));
  for (const [name, scenario] of Object.entries(manifest.scenarios)) {
    const wav = await readFile(new URL(scenario.file.replace(".csv", "-decoded.wav"), root));
    assert.equal(wav.toString("ascii", 0, 4), "RIFF");
    assert.equal(wav.readUInt16LE(22), 2, `${name} channels`);
    assert.equal(wav.readUInt32LE(24), 8000, `${name} sample rate`);
    assert.equal(wav.readUInt16LE(34), 24, `${name} bit depth`);
    assert.equal(wav.readUInt32LE(40), manifest.frames * 6, `${name} frame count`);
  }
});
