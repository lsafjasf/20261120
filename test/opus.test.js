import test from 'node:test';
import assert from 'node:assert/strict';
import { makeOpusPacket, parseOpusToc, routeOpusPacket } from '../src/opus.js';

test('mixed CELT/SILK/hybrid stream is routed without misclassification', () => {
  // Stream interleaves one SILK-only, one hybrid and one CELT-only frame.
  const stream = [
    { name: 'SILK NB 20ms', config: 1, expectPath: 'SILK', expectDecoder: 'opus-silk-decoder' },
    { name: 'SILK WB 40ms', config: 10, expectPath: 'SILK', expectDecoder: 'opus-silk-decoder' },
    { name: 'Hybrid SWB 10ms', config: 12, expectPath: 'HYBRID', expectDecoder: 'opus-hybrid-decoder' },
    { name: 'Hybrid FB 20ms', config: 15, expectPath: 'HYBRID', expectDecoder: 'opus-hybrid-decoder' },
    { name: 'CELT FB 20ms', config: 31, expectPath: 'CELT', expectDecoder: 'opus-celt-decoder' },
    { name: 'CELT WB 10ms', config: 22, expectPath: 'CELT', expectDecoder: 'opus-celt-decoder' },
  ];

  const counts = { SILK: 0, HYBRID: 0, CELT: 0 };
  for (const item of stream) {
    const packet = makeOpusPacket(item.config);
    const toc = parseOpusToc(packet);
    const routed = routeOpusPacket(packet);
    assert.equal(routed.decodePath, item.expectPath, `${item.name} decode path`);
    assert.equal(routed.decoder, item.expectDecoder, `${item.name} decoder`);
    assert.equal(toc.config, item.config);
    counts[routed.decodePath] += 1;
  }
  assert.deepEqual(counts, { SILK: 2, HYBRID: 2, CELT: 2 });
});

test('TOC stereo and frame-count code bits are decoded', () => {
  const mono = parseOpusToc(makeOpusPacket(31, { stereo: false, code: 0 }));
  assert.equal(mono.stereo, false);
  assert.equal(mono.frameCount, 1);

  const stereo = parseOpusToc(makeOpusPacket(14, { stereo: true, code: 2 }));
  assert.equal(stereo.stereo, true);
  assert.equal(stereo.frameCount, 2);
  assert.equal(stereo.mode, 'HYBRID');
});

test('all 32 configs map to exactly one valid decode path', () => {
  for (let config = 0; config < 32; config += 1) {
    const routed = routeOpusPacket(makeOpusPacket(config));
    assert.ok(['SILK', 'HYBRID', 'CELT'].includes(routed.decodePath));
    assert.equal(routed.config, config);
    if (config <= 11) assert.equal(routed.decodePath, 'SILK');
    else if (config <= 15) assert.equal(routed.decodePath, 'HYBRID');
    else assert.equal(routed.decodePath, 'CELT');
  }
});
