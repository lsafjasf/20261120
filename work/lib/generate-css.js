'use strict';

const { parseXML, localName, findChildren, findChild, cellMetrics, toPx, toPx2 } = require('./ttml');
const { ATTR_MAP } = require('./mapper');

const WRITING_MODE = {
  lrtb: 'horizontal-tb', rltb: 'horizontal-tb',
  tbrl: 'vertical-rl', tblr: 'vertical-lr',
  'lr-tb': 'horizontal-tb', 'rl-tb': 'horizontal-tb',
  'tb-rl': 'vertical-rl', 'tb-lr': 'vertical-lr',
};
const DISPLAY_ALIGN = { before: 'flex-start', center: 'center', after: 'flex-end' };
const BIDI = { normal: 'normal', embed: 'embed', bdo: 'bidi-override',
  isolated: 'isolate', isolate: 'isolate',
  isolateOverride: 'isolate-override', plaintext: 'plaintext' };

function ttsAttrs(attrs) {
  return Object.keys(attrs).filter(k => k.startsWith('tts:') || k.startsWith('xml:'));
}

function lenVal(v, cm) {
  const px = toPx(v, cm);
  if (px != null) return `${px}px`;
  return String(v);
}

function padPx(token, horizontal, cm) {
  const m = /^([\d.]+)(px|c|%)$/.exec(token);
  if (!m) return NaN;
  const n = +m[1];
  if (m[2] === 'px') return n;
  if (m[2] === 'c') return n * (horizontal ? cm.cellW : cm.cellH);
  return n / 100 * (horizontal ? cm.width : cm.height);
}

function parsePadding(v, cm) {
  // TTML padding: 1-4 lengths, vertical insets resolve against cell H and
  // horizontal insets against cell W (imscJS behavior).
  const parts = v.split(/\s+/);
  let top, right, bottom, left;
  if (parts.length === 1) {
    top = bottom = padPx(parts[0], false, cm);
    right = left = padPx(parts[0], true, cm);
  } else if (parts.length === 2) {
    top = bottom = padPx(parts[0], false, cm);
    right = left = padPx(parts[1], true, cm);
  } else if (parts.length === 3) {
    top = padPx(parts[0], false, cm);
    right = left = padPx(parts[1], true, cm);
    bottom = padPx(parts[2], false, cm);
  } else {
    top = padPx(parts[0], false, cm);
    right = padPx(parts[3], true, cm);
    bottom = padPx(parts[2], false, cm);
    left = padPx(parts[1], true, cm);
  }
  return { top, right, bottom, left };
}

function buildRegionCss(region, cm, diffs) {
  const id = region.attrs['xml:id'];
  const decls = ['position: absolute', 'box-sizing: content-box'];
  const origin = toPx2(region.attrs['tts:origin'], cm);
  const extent = toPx2(region.attrs['tts:extent'], cm);
  let pad = region.attrs['tts:padding'] ? parsePadding(region.attrs['tts:padding'], cm) : null;
  const wm = region.attrs['tts:writingMode'];
  if (pad && (wm === 'tbrl' || wm === 'tb')) {
    // imscJS rotates [before,end,after,start] to physical edges in tbrl.
    pad = { top: pad.left, right: pad.bottom, bottom: pad.right, left: pad.top };
  } else if (pad && wm === 'tblr') {
    pad = { top: pad.right, right: pad.top, bottom: pad.left, left: pad.bottom };
  }
  if (origin) decls.push(`left: ${origin[0]}px`, `top: ${origin[1]}px`);
  if (extent) {
    // TTML extent includes padding; imscJS sets the content-box size to
    // extent minus insets, so mirror that to obtain identical region boxes.
    const w = pad ? extent[0] - pad.left - pad.right : extent[0];
    const h = pad ? extent[1] - pad.top - pad.bottom : extent[1];
    decls.push(`width: ${w}px`, `height: ${h}px`);
  }
  const writingMode = region.attrs['tts:writingMode'];
  const vertical = writingMode && (writingMode.startsWith('tb'));
  if (writingMode) decls.push(`writing-mode: ${WRITING_MODE[writingMode] || 'horizontal-tb'}`);
  const da = region.attrs['tts:displayAlign'];
  if (da) {
    decls.push('display: flex', `flex-direction: column`,
      `justify-content: ${DISPLAY_ALIGN[da] || 'flex-start'}`);
  }
  if (pad) decls.push(`padding: ${pad.top}px ${pad.right}px ${pad.bottom}px ${pad.left}px`);
  if (region.attrs['tts:backgroundColor']) decls.push(`background-color: ${region.attrs['tts:backgroundColor']}`);
  if (region.attrs['tts:opacity']) decls.push(`opacity: ${region.attrs['tts:opacity']}`);
  if (region.attrs['tts:overflow'] === 'visible') decls.push('overflow: visible');
  else decls.push('overflow: hidden');
  decls.push('z-index: 1');
  collectDiffs(region.attrs, `region:${id}`, diffs);
  return { selector: `#${id}`, decls, vertical: !!vertical, id };
}

function buildStyleCss(style, cm, diffs) {
  const id = style.attrs['xml:id'];
  const a = style.attrs;
  const decls = [];
  if (a['tts:fontFamily']) decls.push(`font-family: ${a['tts:fontFamily']}`);
  if (a['tts:fontSize']) decls.push(`font-size: ${lenVal(a['tts:fontSize'], cm)}`);
  if (a['tts:lineHeight']) decls.push(`line-height: ${lenVal(a['tts:lineHeight'], cm)}`);
  if (a['tts:color']) decls.push(`color: ${a['tts:color']}`);
  if (a['tts:backgroundColor']) decls.push(`background-color: ${a['tts:backgroundColor']}`);
  if (a['tts:fontStyle']) decls.push(`font-style: ${a['tts:fontStyle']}`);
  if (a['tts:fontWeight']) decls.push(`font-weight: ${a['tts:fontWeight']}`);
  if (a['tts:textDecoration']) decls.push(`text-decoration-line: ${a['tts:textDecoration']}`);
  if (a['tts:textAlign']) decls.push(`text-align: ${a['tts:textAlign']}`);
  if (a['tts:textShadow']) decls.push(`text-shadow: ${a['tts:textShadow']}`);
  if (a['tts:letterSpacing']) decls.push(`letter-spacing: ${a['tts:letterSpacing']}`);
  if (a['tts:direction']) decls.push(`direction: ${a['tts:direction']}`);
  if (a['tts:unicodeBidi']) decls.push(`unicode-bidi: ${BIDI[a['tts:unicodeBidi']] || a['tts:unicodeBidi']}`);
  if (a['tts:rubyPosition']) decls.push(`ruby-position: ${a['tts:rubyPosition']}`);
  if (a['tts:rubyAlign']) {
    // imscJS maps every non-spaceBetween value to CSS center.
    const ra = a['tts:rubyAlign'] === 'spaceBetween' ? 'space-between' : 'center';
    decls.push(`ruby-align: ${ra}`);
  }
  if (a['tts:rubyReserve']) {
    if (!a['tts:fontSize']) decls.push(`font-size: ${cm.cellH}px`);
    decls.push(`line-height: 1`);
    // Emulation only: an empty <ruby class=ruby-reserve> reserves the
    // annotation block, mirroring imscJS. Size its <rt> from the reserve
    // length (0.6c here).
    const m = /(\d+(?:\.\d+)?)(c|px|em)/.exec(a['tts:rubyReserve']);
    const reserve = m ? toPx(m[1] + m[2], cm) : 0.2 * cm.cellH;
    decls.push(`/* rubyReserve emulation: .ruby-reserve rt height */`);
    decls.push(`--ruby-reserve: ${reserve}px`);
  }
  if (a['tts:fontShear']) decls.push(`transform: skewX(${-parseFloat(a['tts:fontShear'])}deg)`);
  if (a['tts:wrapOption'] === 'noWrap') decls.push('white-space: nowrap');
  collectDiffs(a, `style:${id}`, diffs);
  return { selector: `.${id}`, decls, id };
}

function collectDiffs(attrs, scope, diffs) {
  for (const key of ttsAttrs(attrs)) {
    const mapped = ATTR_MAP[key];
    if (!mapped) {
      diffs.push({ attribute: key, value: attrs[key], status: 'unknown',
        reason: '映射表未收录的属性', scope: scope.split(':')[0], id: scope.split(':')[1] });
    } else if (mapped.status) {
      diffs.push({ attribute: key, value: attrs[key], status: mapped.status,
        reason: mapped.note || '', scope: scope.split(':')[0], id: scope.split(':')[1] });
    }
  }
}

function generate(xml) {
  const root = parseXML(xml);
  const cm = cellMetrics(root);
  const head = findChild(root, 'head');
  const styling = findChild(head, 'styling');
  const layout = findChild(head, 'layout');
  const diffs = [];
  const regions = findChildren(layout, 'region').map(r => buildRegionCss(r, cm, diffs));
  const styles = findChildren(styling, 'style').map(s => buildStyleCss(s, cm, diffs));
  const reset = `.ttml-region, .ttml-region p, .ttml-region span,
.ttml-region ruby, .ttml-region rt { margin: 0; }
.ruby-reserve rt {
  font-size: var(--ruby-reserve, 28.8px);
  line-height: var(--ruby-reserve, 28.8px);
  min-height: var(--ruby-reserve, 28.8px);
}
.ruby-reserve { ruby-position: before; }`;
  const blocks = [reset, ...regions, ...styles].map(b =>
    typeof b === 'string' ? b :
    `${b.selector} {\n${b.decls.map(d => '  ' + d + ';').join('\n')}\n}`);
  return { css: blocks.join('\n\n'), diffs, cm, regions, styles };
}

module.exports = { generate };
