'use strict';

const fs = require('fs');
const path = require('path');
const { generate } = require('./lib/generate-css');
const { ATTR_MAP } = require('./lib/mapper');

const reports = path.join(__dirname, 'reports');
const samples = path.join(__dirname, 'samples');
const files = ['multilang.itt', 'vertical-ruby.itt'];

function statusLabel(s) {
  return { mappable: '可直接映射', partial: '部分映射',
    emulated: '仅可模拟', unmappable: '无法映射',
    unknown: '未收录（xml:id 等非样式属性）' }[s] || s;
}

const mappingLines = ['# TTML/IMSC region / style / tts → CSS 映射报告',
  '', '- 视口: 1280×720px；cellResolution: 32×15；参考实现: imscJS 2.0 + Chrome 157',
  '- 状态: mappable 可直接映射 / partial 部分映射 / emulated 仅可模拟 / unmappable 无法映射', ''];
const summary = [];

for (const file of files) {
  const xml = fs.readFileSync(path.join(samples, file), 'utf8');
  const r = generate(xml);
  fs.writeFileSync(path.join(reports, file.replace('.itt', '.css')), r.css);
  fs.writeFileSync(path.join(reports, file.replace('.itt', '.differences.json')),
    JSON.stringify(r.diffs, null, 2));
  const byStatus = {};
  for (const d of r.diffs) byStatus[d.status] = (byStatus[d.status] || 0) + 1;
  summary.push({ file, observedDifferences: r.diffs.length, byStatus,
    unmappableHit: [...new Set(r.diffs
      .filter(d => d.status === 'unmappable' || d.status === 'emulated')
      .map(d => d.attribute))] });
  mappingLines.push(`## 样例：${file}`, '',
    `- 观察到的差异项: ${r.diffs.length}；状态分布: ${JSON.stringify(byStatus)}`, '',
    '### 生成的 CSS', '', '```css', r.css, '```', '',
    '### 本文档触发的差异项', '',
    '| 属性 | 值 | 状态 | 位置 | 原因 |', '| --- | --- | --- | --- | --- |');
  for (const d of r.diffs) {
    mappingLines.push(`| \`${d.attribute}\` | ${d.value} | ${statusLabel(d.status)} | ${d.scope}:${d.id} | ${d.reason || '—'} |`);
  }
  mappingLines.push('');
}

// Complete unmappable/partial catalogue from the master mapping table.
const entries = Object.entries(ATTR_MAP).filter(([, v]) => v.status);
const groups = { unmappable: [], emulated: [], partial: [] };
for (const [attr, v] of entries) (groups[v.status] || groups.partial).push([attr, v]);
mappingLines.push('## CSS 无法直接表达的属性完整清单', '',
  '### 完全无法映射（unmappable）', '',
  '| 属性 | CSS 替代 | 原因 |', '| --- | --- | --- |');
for (const [attr, v] of groups.unmappable)
  mappingLines.push(`| \`${attr}\` | ${v.css || '无'} | ${v.note} |`);
mappingLines.push('', '### 只能部分映射或模拟（partial / emulated）', '',
  '| 属性 | 状态 | CSS 替代 | 差异 |', '| --- | --- | --- | --- |');
for (const [attr, v] of [...groups.emulated, ...groups.partial])
  mappingLines.push(`| \`${attr}\` | ${statusLabel(v.status)} | ${v.css || '无'} | ${v.note} |`);

fs.writeFileSync(path.join(reports, 'mapping-report.md'), mappingLines.join('\n'));
fs.writeFileSync(path.join(reports, 'mapping-summary.json'), JSON.stringify(summary, null, 2));
console.log('mapping report written:', summary);
