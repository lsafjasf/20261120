'use strict';

const fs = require('fs');
const path = require('path');
const reports = path.join(__dirname, 'reports');
const data = JSON.parse(fs.readFileSync(path.join(reports, 'compare-results.json')));
const tol = 4;

const lines = ['# 浏览器渲染（CSS 映射）vs 参考实现（imscJS）对比报告', '',
  `- 视口: 1280×720px；容差: ${tol}px；浏览器: Chrome 157 headless；参考: imscJS 2.0`, ''];

for (const f of data) {
  if (!f.analysis) { lines.push(`## ${f.file} — 错误`, '', f.errors.join('; '), ''); continue; }
  const a = f.analysis;
  lines.push(`## ${f.file} — ${a.overallPass ? '✅ 全部在容差内' : '❌ 存在超差项'}`, '');
  lines.push('### 行高 / 段落盒高度', '',
    '| # | 参考高度 px | CSS 高度 px | 差 px | 判定 | 位置(top/left 差) |',
    '| - | --- | --- | --- | --- | --- |');
  for (const row of a.lineHeights) {
    lines.push(`| ${row.index} | ${row.refParagraphHeightPx ?? 'n/a'} | ${row.cssParagraphHeightPx ?? 'n/a'} | ${row.absDeltaPx ?? 'n/a'} | ${row.verdict} | ${row.topDeltaPx != null ? row.topDeltaPx.toFixed(2) + '/' + row.leftDeltaPx.toFixed(2) : 'n/a'} |`);
  }
  lines.push('', '### 首行行盒', '',
    `- 行高差: ${a.firstLine.heightDeltaPx.toFixed(2)}px (${a.firstLine.heightVerdict})；位置差 top/left: ${a.firstLine.topDeltaPx.toFixed(2)}/${a.firstLine.leftDeltaPx.toFixed(2)}px (${a.firstLine.positionVerdict})`, '');
  lines.push('### 基线偏移', '',
    `- 轴向: ${a.baseline.axis}；参考=${a.baseline.refBaselineY?.toFixed(2)}；CSS=${a.baseline.cssBaselineY?.toFixed(2)}；偏移=${a.baseline.baselineOffsetPx.toFixed(2)}px；判定=${a.baseline.verdict}`,
    `- 方法: ${a.baseline.method}`, '');
  lines.push('### Ruby 注音位置（相对各自 region 归一化）', '',
    '| # | 注音 | rt 位置误差 px | gap 差 px | 判定 |',
    '| - | --- | --- | --- | --- |');
  if (!a.ruby.length) lines.push('| — | 无真实注音 | — | — | — |');
  for (const rb of a.ruby) {
    lines.push(`| ${rb.index} | ${rb.rubyText} | ${rb.positionErrorPx != null ? rb.positionErrorPx.toFixed(2) : 'n/a'} | ${rb.gapDeltaPx != null ? rb.gapDeltaPx.toFixed(2) : 'n/a'} | ${rb.verdict} |`);
  }
  lines.push('');
}

fs.writeFileSync(path.join(reports, 'compare-report.md'), lines.join('\n'));
console.log('compare report written');
