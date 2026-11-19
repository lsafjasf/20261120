'use strict';

function parseAttrs(tagText) {
  const attrs = {};
  const re = /([\w:]+)\s*=\s*"([^"]*)"/g;
  let m;
  while ((m = re.exec(tagText)) !== null) attrs[m[1]] = m[2];
  return attrs;
}

function parseXML(xml) {
  // Minimal non-namespace-aware element parser sufficient for TTML samples.
  // Each node keeps `mixed` (ordered text/element items) so inter-element
  // whitespace (e.g. between ruby containers) survives serialization.
  const stack = [];
  const root = { tag: null, attrs: {}, children: [], text: '', mixed: [] };
  stack.push(root);
  const tokenRe = /<!--[\s\S]*?-->|<\?[\s\S]*?>|<\/?[^>]+>|[^<]+/g;
  let t;
  while ((t = tokenRe.exec(xml)) !== null) {
    const tok = t[0];
    if (tok.startsWith('<!--') || tok.startsWith('<?')) continue;
    if (tok.startsWith('<')) {
      const closing = tok.startsWith('</');
      const selfClose = tok.endsWith('/>');
      const name = tok.replace(/[</>]/g, '').trim().split(/\s/)[0];
      if (closing) { stack.pop(); continue; }
      const node = { tag: name, attrs: parseAttrs(tok),
        children: [], text: '', mixed: [] };
      stack[stack.length - 1].children.push(node);
      stack[stack.length - 1].mixed.push({ type: 'element', node });
      if (!selfClose) stack.push(node);
    } else if (tok.length) {
      const node = stack[stack.length - 1];
      const wsOnly = /^\s+$/.test(tok);
      const piece = wsOnly ? ' ' : tok;
      const last = node.mixed[node.mixed.length - 1];
      if (last && last.type === 'text' && wsOnly &&
          last.value.trim() === '') continue;
      if (last && last.type === 'text' && !wsOnly) last.value += piece;
      else node.mixed.push({ type: 'text', value: piece });
      if (node.children.length === 0) node.text += piece;
    }
  }
  return root.children[0];
}

function localName(tag) {
  return tag.includes(':') ? tag.split(':')[1] : tag;
}

function findChildren(node, name) {
  return node.children.filter(c => localName(c.tag) === name);
}

function findChild(node, name) {
  return findChildren(node, name)[0] || null;
}

function cellMetrics(root) {
  const tt = root;
  const cell = (tt.attrs['ttp:cellResolution'] || '32 15').split(/\s+/).map(Number);
  const extent = (tt.attrs['tts:extent'] || tt.attrs['ttp:extent'] ||
    '1280px 720px').match(/(\d+)px\s+(\d+)px/);
  const width = extent ? +extent[1] : 1280;
  const height = extent ? +extent[2] : 720;
  return { cols: cell[0], rows: cell[1], width, height,
    cellW: width / cell[0], cellH: height / cell[1] };
}

function toPx(value, cm) {
  if (value == null) return null;
  let m;
  if ((m = /^([\d.]+)px$/.exec(value))) return +m[1];
  if ((m = /^([\d.]+)c$/.exec(value))) return +m[1] * cm.cellH;
  if ((m = /^([\d.]+)%$/.exec(value))) return +m[1] / 100 * cm.height;
  return null;
}

function toPx2(value, cm) {
  // Two-component length (origin/extent): horizontal component resolves
  // against cell width / root width, vertical against cell height / root
  // height — matching imscJS cellLength {rw, rh}.
  if (value == null) return null;
  const parts = value.split(/\s+/);
  const bases = [cm.cellW, cm.cellH];
  const roots = [cm.width, cm.height];
  return parts.map((p, idx) => {
    let m;
    if ((m = /^([\d.]+)px$/.exec(p))) return +m[1];
    if ((m = /^([\d.]+)c$/.exec(p))) return +m[1] * bases[idx];
    if ((m = /^([\d.]+)%$/.exec(p))) return +m[1] / 100 * roots[idx];
    return NaN;
  });
}

module.exports = { parseXML, localName, findChildren, findChild,
  cellMetrics, toPx, toPx2 };
