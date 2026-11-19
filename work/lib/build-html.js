'use strict';

const { parseXML, findChildren, findChild } = require('./ttml');

function esc(s) {
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function normText(s) {
  return esc((s || '').replace(/\s+/g, ' '));
}

// Resolve TTML style references (including chained styles) into a flat table
// of effective attributes. Inline attributes win over referenced styles.
function buildStyleTable(root) {
  const styling = findChild(findChild(root, 'head'), 'styling');
  const table = new Map();
  if (!styling) return table;
  for (const s of findChildren(styling, 'style')) table.set(s.attrs['xml:id'], s);
  return table;
}

function effectiveAttrs(node, table, seen = new Set()) {
  const out = {};
  const refs = (node.attrs.style || '').split(/\s+/).filter(Boolean);
  for (const refId of refs) {
    if (seen.has(refId)) continue;
    seen.add(refId);
    const st = table.get(refId);
    if (st) Object.assign(out, effectiveAttrs(st, table, seen), st.attrs);
  }
  Object.assign(out, node.attrs);
  return out;
}

let STYLE_TABLE;

function serializeChildren(node) {
  let out = '';
  for (const item of (node.mixed || [])) {
    if (item.type === 'text') {
      out += normText(item.value);
      continue;
    }
    const child = item.node;
    const role = effectiveAttrs(child, STYLE_TABLE)['tts:ruby'];
    if (role === 'text' || role === 'base' || role === 'delimiter') {
      const txt = child.children.length
        ? child.children.map(spanHtml).join('') : normText(child.text || '');
      out += rubyChildHtml(child, role, txt);
    } else {
      out += spanHtml(child);
    }
  }
  return out.replace(/^\s+|\s+$/g, '');
}

function rubyChildHtml(child, role, txt) {
  const extra = (child.attrs.style || '').split(/\s+/).filter(Boolean);
  if (role === 'text') return `<rt class="ruby-text ${extra.join(' ')}">${txt}</rt>`;
  if (role === 'base') return `<span class="ruby-base ${extra.join(' ')}">${txt}</span>`;
  return '';
}

function classAttr(node) {
  return node.attrs.style ? ` class="ttml-span ${node.attrs.style.split(/\s+/).join(' ')}"`
    : ' class="ttml-span"';
}

// tts:ruby role -> HTML ruby element, matching imscJS output structure.
function spanHtml(node) {
  const a = node.attrs;
  const eff = effectiveAttrs(node, STYLE_TABLE);
  const ruby = eff['tts:ruby'];
  const inner = serializeChildren(node);
  if (ruby === 'container') {
    const cls = 'ruby-container' + (a.style ? ' ' + a.style.split(/\s+/).join(' ') : '');
    const flat = node.children.map(child => {
      const role = effectiveAttrs(child, STYLE_TABLE)['tts:ruby'];
      const txt = child.children.length
        ? child.children.map(spanHtml).join('') : normText(child.text || '');
      const extra = (child.attrs.style || '').split(/\s+/).filter(Boolean);
      if (role === 'text') return `<rt class="ruby-text ${extra.join(' ')}">${txt}</rt>`;
      if (role === 'base') return `<span class="ruby-base ${extra.join(' ')}">${txt}</span>`;
      return spanHtml(child);
    }).join('');
    return `<ruby class="${cls}">${flat}</ruby>`;
  }
  if (ruby === 'base') {
    return `<span class="ruby-base"${classAttr(node)}>${inner}</span>`;
  }
  if (ruby === 'text') {
    return `<rt class="ruby-text"${classAttr(node)}>${inner}</rt>`;
  }
  if (ruby === 'delimiter') return `<rp class="ruby-delimiter">${inner}</rp>`;
  const lang = a['xml:lang'] ? ` lang="${a['xml:lang']}"` : '';
  if (!inner && node.text) return `<span${classAttr(node)}${lang}>${normText(node.text)}</span>`;
  return `<span${classAttr(node)}${lang}>${inner}</span>`;
}

function buildMappedHtml(xml) {
  const root = parseXML(xml);
  STYLE_TABLE = buildStyleTable(root);
  const body = findChildren(root, 'body')[0];
  const byRegion = new Map();
  for (const div of findChildren(body, 'div')) {
    const region = div.attrs.region ||
      (findChildren(div, 'p')[0] || { attrs: {} }).attrs.region;
    for (const p of findChildren(div, 'p')) {
      const cls = ['ttml-p'];
      if (p.attrs.style) cls.push(...p.attrs.style.split(/\s+/));
      const regionId = p.attrs.region || region;
      const inner = serializeChildren(p);
      const effP = effectiveAttrs(p, STYLE_TABLE);
      let reserveHtml = '';
      if (effP['tts:rubyReserve']) {
        // Mirror imscJS: an empty <ruby> reserves the annotation block.
        reserveHtml = '<ruby class="ruby-reserve"><span>​</span><rt>​</rt></ruby>';
      }
      const html = `<p class="${cls.join(' ')}" data-region="${regionId || ''}">${reserveHtml}${inner}</p>`;
      if (!byRegion.has(regionId)) byRegion.set(regionId, []);
      byRegion.get(regionId).push(html);
    }
  }
  return [...byRegion.entries()].map(([regionId, paras]) =>
    `<div id="${regionId || 'default-region'}" class="ttml-region">${paras.join('\n')}</div>`
  ).join('\n');
}

module.exports = { buildMappedHtml };
