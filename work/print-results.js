'use strict';

const r = require('./reports/compare-results.json');
for (const f of r) {
  if (!f.analysis) { console.log(f.file, f.errors); continue; }
  const a = f.analysis;
  console.log('=====', f.file, 'overallPass =', a.overallPass);
  console.log('lineHeights:', a.lineHeights.map(x => ({
    i: x.index, delta: x.absDeltaPx != null && +x.absDeltaPx.toFixed(2),
    verdict: x.verdict, position: x.positionVerdict,
    top: x.topDeltaPx != null && +x.topDeltaPx.toFixed(2),
    left: x.leftDeltaPx != null && +x.leftDeltaPx.toFixed(2) })));
  console.log('firstLine:', {
    height: +a.firstLine.heightDeltaPx.toFixed(2),
    top: +a.firstLine.topDeltaPx.toFixed(2),
    left: +a.firstLine.leftDeltaPx.toFixed(2),
    heightVerdict: a.firstLine.heightVerdict,
    positionVerdict: a.firstLine.positionVerdict });
  console.log('baseline:', {
    axis: a.baseline.axis, offsetPx: +a.baseline.baselineOffsetPx.toFixed(2),
    verdict: a.baseline.verdict });
  console.log('ruby:', a.ruby.map(x => ({
    text: x.rubyText,
    positionErrorPx: x.positionErrorPx != null && +x.positionErrorPx.toFixed(2),
    gapDeltaPx: x.gapDeltaPx != null && +x.gapDeltaPx.toFixed(2),
    verdict: x.verdict, gapVerdict: x.gapVerdict })));
  console.log('regions:', {
    ref: a.regions.refCount, css: a.regions.cssCount });
}
