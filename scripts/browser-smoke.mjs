// Real-browser smoke test (run: node scripts/browser-smoke.mjs after `npx playwright install chromium`).
import { chromium } from 'playwright';
import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import { extname, join } from 'node:path';

const root = join(import.meta.dirname, '..');
const mime = { '.html': 'text/html', '.js': 'text/javascript' };

const server = createServer(async (req, res) => {
  const path = req.url === '/' ? '/index.html' : req.url.split('?')[0];
  try {
    const data = await readFile(join(root, path));
    res.writeHead(200, { 'content-type': mime[extname(path)] || 'application/octet-stream' });
    res.end(data);
  } catch {
    res.writeHead(404);
    res.end('not found');
  }
});

await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
const port = server.address().port;

const browser = await chromium.launch();
const page = await browser.newPage();
const logs = [];
page.on('console', (msg) => logs.push(`[${msg.type()}] ${msg.text()}`));
page.on('pageerror', (err) => logs.push(`[pageerror] ${err.message}`));

await page.goto(`http://127.0.0.1:${port}/`);
const support = await page.locator('#support').textContent();
console.log(support);

await page.click('#run');
try {
  await page.waitForFunction(
    () => document.getElementById('summary').textContent.length > 0
      || document.getElementById('log').innerText.includes('运行失败'),
    { timeout: 20000 },
  );
} catch {
  /* fall through; dump logs below */
}

const logText = await page.locator('#log').innerText();
const summary = await page.locator('#summary').innerText();
console.log(logText);
console.log('\n===== summary =====\n');
console.log(summary);

const assertions = [
  ['WebCodecs 可用', support.includes('可用')],
  ['三种打包模式出现', /single=\d+, STAP-A=\d+, FU-A=\d+/.test(logText)],
  ['FU-A 丢包模拟', logText.includes('模拟 FU-A 丢包')],
  ['丢包 gap 告警', logText.includes('packet_gap')],
  ['时戳跳变告警', logText.includes('timestamp_jump')],
  ['整帧丢失模拟', logText.includes('模拟整帧丢失')],
  ['不完整 NAL 被跳过', logText.includes('incomplete_fu')],
  ['后续帧仍解码', /VideoDecoder 输出帧/.test(logText)],
  ['每 NAL 切片序号', /slice=\d+/.test(logText)],
  ['总耗时输出', logText.includes('重组后交给解码器的总耗时')],
  ['Opus 混合流零错判', logText.includes('错判 0 个')],
];
let failed = 0;
for (const [name, ok] of assertions) {
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}`);
  if (!ok) failed += 1;
}
if (logs.some((line) => line.includes('pageerror') || line.includes('[error]'))) {
  console.log('\nUnexpected console output:\n' + logs.join('\n'));
  failed += 1;
}

await browser.close();
server.close();
process.exit(failed ? 1 : 0);
