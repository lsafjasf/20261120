'use strict';

const fs = require('fs');
const path = require('path');

// Browser-side code (serialized into the page). Renders both the imscJS
// reference ISD and the CSS-mapped equivalent, then measures geometry.
const PAGE_FN = String(async function pageMain({ imscUrl, ttmlXml, mappedHtml, mappedCss }) {
  const imsc = await import(imscUrl);
  const doc = imsc.fromXML(ttmlXml, e => { throw new Error('TTML parse: ' + e); });
  const isd = imsc.generateISD(doc, 3.0);

  const stage = document.getElementById('stage');
  const ref = document.createElement('div');
  ref.id = 'ref-root';
  ref.style.cssText = 'position:absolute;left:0;top:0;width:1280px;height:720px';
  stage.appendChild(ref);
  const errs = [];
  imsc.renderHTML(isd, ref, null, 720, 1280, false,
    msg => errs.push(String(msg && msg.message || msg)), null, false);
  if (errs.length) throw new Error('renderHTML error: ' + errs.join(' | '));
  // imscJS structure: an absolutely-positioned region element (border box ==
  // TTML origin+extent, content box == extent minus padding) containing a
  // static body/div wrapper. Tag the region element itself so geometry is
  // normalized against the same border box on both sides.
  ref.querySelectorAll('div[style*="position: absolute"]').forEach(wrapper => {
    wrapper.classList.add('ttml-region-ref');
  });

  const cssHost = document.createElement('div');
  cssHost.id = 'css-root';
  cssHost.style.cssText = 'position:absolute;left:0;top:0;width:1280px;height:720px';
  const style = document.createElement('style');
  style.textContent = mappedCss;
  cssHost.appendChild(style);
  const wrap = document.createElement('div');
  wrap.innerHTML = mappedHtml;
  cssHost.appendChild(wrap);
  stage.appendChild(cssHost);

  await new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));

  function box(el) {
    const r = el.getBoundingClientRect();
    return { x: r.x, y: r.y, width: r.width, height: r.height,
      top: r.top, left: r.left, right: r.right, bottom: r.bottom };
  }

  function probeBaseline(host, sampleEl) {
    // inline-block probe: its bottom sits on the text baseline;
    // subtract ~0.21em descent estimate like the legacy methodology.
    const probe = document.createElement('span');
    probe.className = 'baseline-probe';
    probe.style.cssText = 'display:inline-block;width:1px;height:1px;overflow:hidden';
    sampleEl.appendChild(probe);
    const fs = parseFloat(getComputedStyle(sampleEl).fontSize) || 0;
    const rect = probe.getBoundingClientRect();
    const pb = rect.bottom;
    const wm = getComputedStyle(sampleEl).writingMode ||
               getComputedStyle(host).writingMode || 'horizontal-tb';
    probe.remove();
    const vertical = wm.indexOf('vertical') === 0;
    return {
      baseline: vertical ? rect.left : pb - 0.21 * fs,
      baselineX: rect.left + rect.width / 2,
      probeBottom: pb, fontSize: fs, writingMode: wm,
    };
  }

  function textOf(el) { return (el.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 40); }

  function rel(rect, origin) {
    return { x: rect.x - origin.x, y: rect.y - origin.y,
      width: rect.width, height: rect.height,
      top: rect.top - origin.y, left: rect.left - origin.x,
      right: rect.right - origin.x, bottom: rect.bottom - origin.y };
  }

  function regionOf(el, host) {
    const byId = el.closest('.ttml-region, .ttml-region-ref');
    if (byId) return byId;
    let cur = el;
    while (cur && cur !== host) {
      if (cur.tagName === 'DIV' &&
          getComputedStyle(cur).position === 'absolute') return cur;
      cur = cur.parentElement;
    }
    return host;
  }

  function collect(host) {
    const paras = [...host.querySelectorAll('p')].map((p, i) => {
      const region = regionOf(p, host);
      const rb = region.getBoundingClientRect();
      return {
      index: i, text: textOf(p),
      lineHeight: getComputedStyle(p).lineHeight,
      writingMode: getComputedStyle(p).writingMode || getComputedStyle(host).writingMode,
      rect: box(p),
      regionBox: box(region),
      rectRel: rel(p.getBoundingClientRect(), rb),
      regionId: region.id || null,
      };
    });
    const firstText = host.querySelector('p, .ttml-p');
    const firstLine = firstText ? (() => {
      const range = document.createRange();
      range.selectNodeContents(firstText);
      const rects = [...range.getClientRects()];
      const r = rects[0] || firstText.getBoundingClientRect();
      const rb = regionOf(firstText, host).getBoundingClientRect();
      return { x: r.x, y: r.y, width: r.width, height: r.height,
        top: r.top, left: r.left, bottom: r.bottom, right: r.right,
        rel: rel(r, rb) };
    })() : null;
    const baselineEl = firstText || host;
    const baseline = probeBaseline(host, baselineEl);
    const rubies = [...host.querySelectorAll('ruby')].map((rb, i) => {
      const rt = rb.querySelector('rt');
      const base = rb.querySelector('span, rb');
      const rtb = rt ? rt.getBoundingClientRect() : null;
      const bbb = base ? base.getBoundingClientRect() : rb.getBoundingClientRect();
      const origin = regionOf(rb, host).getBoundingClientRect();
      const cs = getComputedStyle(rb);
      return { index: i, rubyText: rt ? textOf(rt) : null,
        baseText: base ? textOf(base) : textOf(rb), writingMode: cs.writingMode,
        rtBox: rtb ? rel(rtb, origin) : null,
        baseBox: rel(bbb, origin),
        rtGap: rtb ? (cs.writingMode.indexOf('vertical') === 0
          ? rtb.x - bbb.x : rtb.y - bbb.y) : null };
    });
    const regionRects = [...host.querySelectorAll('.ttml-region, .ttml-region-ref')]
      .filter(el => el.getBoundingClientRect().width > 0)
      .map(box);
    return { paragraphs: paras, firstLine, baseline, rubies, regionRects };
  }

  window.__domDump = {
    ref: ref.innerHTML.slice(0, 4000),
    css: cssHost.innerHTML.slice(0, 4000),
  };
  return { ref: collect(ref), css: collect(cssHost) };
});

function buildPage({ imscUrl, ttmlXml, mappedHtml, mappedCss }) {
  const base = imscUrl.replace(/node_modules\/imsc\/.*$/, 'node_modules/');
  return `<!doctype html><html><head><meta charset="utf-8">
<script type="importmap">{"imports":{"tslib":"${base}tslib/tslib.es6.js"}}</script>
<style>html,body{margin:0;padding:0;background:#000}
#stage{position:relative;width:1280px;height:720px;overflow:hidden}</style>
</head><body><div id="stage"></div>
<script type="module">
const __main = ${PAGE_FN};
__main(${JSON.stringify({ imscUrl, ttmlXml, mappedHtml, mappedCss })}).then(
  r => window.__result = r,
  e => { window.__error = String(e && e.stack || e); }
);
</script></body></html>`;
}

module.exports = { buildPage, PAGE_FN };
