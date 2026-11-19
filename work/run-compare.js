'use strict';

const fs = require('fs');
const path = require('path');
const puppeteer = require('/home/hj/userfeedback/uid4-0025/node_modules/puppeteer');
const { generate } = require('./lib/generate-css');
const { buildMappedHtml } = require('./lib/build-html');
const { buildPage } = require('./lib/browser-page');

const CHROME = '/home/hj/.cache/puppeteer/chrome/linux-157.0.8103.50/chrome-linux64/chrome';
const IMSC_URL = 'file://' + path.resolve(__dirname, 'node_modules/imsc/dist/main/main.js');
const TOL = 4;

function analyze(metrics) {
  const { ref, css } = metrics;
  const lineHeights = [];
  const n = Math.max(ref.paragraphs.length, css.paragraphs.length);
  for (let i = 0; i < n; i++) {
    const rp = ref.paragraphs[i];
    let cp = css.paragraphs[i];
    if (rp && (!cp || (cp.regionId && rp.regionId && cp.regionId !== rp.regionId))) {
      cp = css.paragraphs.find(c => c.regionId === rp.regionId) || cp;
    }
    const row = { index: i,
      refText: rp ? rp.text : null, cssText: cp ? cp.text : null,
      refLineHeightCss: rp ? rp.lineHeight : null,
      cssLineHeightCss: cp ? cp.lineHeight : null,
      refParagraphHeightPx: rp ? rp.rect.height : null,
      cssParagraphHeightPx: cp ? cp.rect.height : null };
    if (rp && cp) {
      row.absDeltaPx = Math.abs(rp.rect.height - cp.rect.height);
      row.verdict = row.absDeltaPx <= TOL ? 'pass' : 'fail';
      row.topDeltaPx = Math.abs(rp.rectRel.y - cp.rectRel.y);
      row.leftDeltaPx = Math.abs(rp.rectRel.x - cp.rectRel.x);
      row.positionVerdict =
        Math.max(row.topDeltaPx, row.leftDeltaPx) <= TOL ? 'pass' : 'fail';
    } else row.verdict = 'missing';
    lineHeights.push(row);
  }
  const firstLine = { available: !!(ref.firstLine && css.firstLine) };
  if (firstLine.available) {
    firstLine.ref = ref.firstLine;
    firstLine.css = css.firstLine;
    firstLine.heightDeltaPx = Math.abs(ref.firstLine.height - css.firstLine.height);
    firstLine.topDeltaPx = Math.abs(ref.firstLine.rel.y - css.firstLine.rel.y);
    firstLine.leftDeltaPx = Math.abs(ref.firstLine.rel.x - css.firstLine.rel.x);
    firstLine.heightVerdict = firstLine.heightDeltaPx <= TOL ? 'pass' : 'fail';
    firstLine.positionVerdict =
      Math.max(firstLine.topDeltaPx, firstLine.leftDeltaPx) <= TOL ? 'pass' : 'fail';
  }
  const baseline = { available: !!(ref.baseline && css.baseline),
    method: 'inline-block 探针底 - 0.21*fontSize 估计 descent' };
  if (baseline.available) {
    const vertical = (ref.paragraphs[0] || {}).writingMode &&
      ref.paragraphs[0].writingMode.indexOf('vertical') === 0;
    // probeBaseline returns a bottom coordinate; for vertical text the block
    // axis is horizontal, so also capture the probe's x axis.
    baseline.axis = vertical ? 'x (竖排块向/字符中轴)' : 'y';
    baseline.refBaselineY = vertical ? ref.baseline.baselineX : ref.baseline.baseline;
    baseline.cssBaselineY = vertical ? css.baseline.baselineX : css.baseline.baseline;
    baseline.fontSize = ref.baseline.fontSize;
    baseline.baselineOffsetPx = Math.abs(baseline.refBaselineY - baseline.cssBaselineY);
    baseline.verdict = baseline.baselineOffsetPx <= TOL ? 'pass' : 'fail';
    baseline.note = '竖排文档中该值为块向（水平方向）坐标差，反映竖排基线（字符中轴）偏移';
  }
  const ruby = [];
  const realRef = ref.rubies.filter(r => r.rtBox && r.rtBox.width > 0);
  const realCss = css.rubies.filter(r => r.rtBox && r.rtBox.width > 0);
  for (let i = 0; i < Math.max(realRef.length, realCss.length); i++) {
    const rr = realRef[i];
    const cr = realCss[i];
    if (!rr || !cr || !rr.rtBox || !cr.rtBox) {
      ruby.push({ index: i, verdict: 'fail: undefined',
        rubyText: cr ? cr.rubyText : rr ? rr.rubyText : null });
      continue;
    }
    const rtTopDelta = Math.abs(rr.rtBox.y - cr.rtBox.y);
    const rtLeftDelta = Math.abs(rr.rtBox.x - cr.rtBox.x);
    const refGap = rr.rtGap;
    const cssGap = cr.rtGap;
    const gapDelta = Math.abs(refGap - cssGap);
    const positionError = Math.hypot(rtTopDelta, rtLeftDelta);
    ruby.push({ index: i, rubyText: cr.rubyText, baseText: cr.baseText,
      writingMode: cr.writingMode,
      refRtBox: rr.rtBox, cssRtBox: cr.rtBox,
      refBaseBox: rr.baseBox, cssBaseBox: cr.baseBox,
      rtTopDeltaPx: rtTopDelta, rtLeftDeltaPx: rtLeftDelta,
      gapDeltaPx: gapDelta, positionErrorPx: positionError,
      gapVerdict: gapDelta <= TOL ? 'pass' : 'fail',
      verdict: positionError <= TOL ? 'pass' : 'fail' });
  }
  const regions = { refCount: ref.regionRects.length, cssCount: css.regionRects.length,
    ref: ref.regionRects, css: css.regionRects };
  const fails = lineHeights.some(r => r.verdict === 'fail') ||
    (firstLine.available && firstLine.positionVerdict === 'fail') ||
    (baseline.available && baseline.verdict === 'fail') ||
    ruby.some(r => r.verdict !== 'pass');
  return { tolerancePx: TOL, overallPass: !fails, lineHeights, firstLine,
    baseline, ruby, regions };
}

async function runFile(browser, file) {
  const ttmlXml = fs.readFileSync(path.join(__dirname, 'samples', file), 'utf8');
  const mapped = generate(ttmlXml);
  const mappedHtml = buildMappedHtml(ttmlXml);
  const html = buildPage({ imscUrl: IMSC_URL, ttmlXml, mappedHtml, mappedCss: mapped.css });
  const pagePath = path.resolve(__dirname, 'reports', file.replace('.itt', '.html'));
  fs.writeFileSync(pagePath, html);
  const page = await browser.newPage();
  await page.setViewport({ width: 1280, height: 720 });
  const errors = [];
  page.on('pageerror', e => errors.push(String(e)));
  await page.goto('file://' + pagePath, { waitUntil: 'load' });
  await page.waitForFunction('window.__result || window.__error', { timeout: 15000 });
  const result = await page.evaluate(() => ({ r: window.__result, e: window.__error }));
  if (result.e) errors.push(result.e);
  await page.close();
  if (errors.length || !result.r) return { file, errors, analysis: null };
  return { file, errors: [], analysis: analyze(result.r), metrics: result.r };
}

(async () => {
  const browser = await puppeteer.launch({
    executablePath: CHROME, headless: 'new',
    args: ['--no-sandbox', '--allow-file-access-from-files', '--font-render-hinting=none'],
  });
  const results = [];
  for (const f of ['multilang.itt', 'vertical-ruby.itt']) {
    results.push(await runFile(browser, f));
  }
  await browser.close();
  fs.writeFileSync(path.join(__dirname, 'reports/compare-results.json'),
    JSON.stringify(results, null, 2));
  for (const r of results) {
    console.log(r.file, r.errors.length ? 'ERROR ' + r.errors.join('; ')
      : 'pass=' + r.analysis.overallPass);
  }
})().catch(e => { console.error(e); process.exit(1); });
