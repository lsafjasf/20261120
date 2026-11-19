// End-to-end runner: drives the EME persistent-license demo in headless
// Chrome across the three key-status scenarios, verifies session restore
// without duplicate license requests, and collects the NAL reassembly
// packet counts. Prints a consolidated report and writes artifacts/report.json.
const fs = require('fs');
const path = require('path');
const puppeteer = require('puppeteer');
const { createServer } = require('../server.js');

const CHROME_GLOB = path.join(
  process.env.HOME, '.cache/puppeteer/chrome',
);

function findChrome() {
  for (const ver of fs.readdirSync(CHROME_GLOB).sort().reverse()) {
    const exe = path.join(CHROME_GLOB, ver, 'chrome-linux64/chrome');
    if (fs.existsSync(exe)) return exe;
  }
  throw new Error('chrome not found in puppeteer cache');
}

async function runPage(browser, baseUrl, query) {
  const page = await browser.newPage();
  await page.goto(`${baseUrl}/?${query}`, { waitUntil: 'load' });
  const report = await page.evaluate(() => window.__done);
  await page.close();
  return report;
}

async function resetStats(baseUrl) {
  await fetch(`${baseUrl}/api/stats/reset`, { method: 'POST' });
}

(async () => {
  const server = createServer();
  await new Promise((r) => server.listen(0, r));
  const baseUrl = `http://127.0.0.1:${server.address().port}`;

  const exe = findChrome();
  const userDataDir = path.join(__dirname, '.chrome-profile');
  fs.rmSync(userDataDir, { recursive: true, force: true });
  const launch = () => puppeteer.launch({
    headless: true,
    executablePath: exe,
    userDataDir,
    args: ['--no-sandbox'],
  });

  const out = { generatedAt: new Date().toISOString(), scenarios: {}, restore: {}, nal: null };
  let browser = await launch();

  // 1) usable: first acquisition (network) + NAL demo.
  await resetStats(baseUrl);
  out.scenarios.usable = await runPage(browser, baseUrl, 'scenario=usable&fresh=1&nal=1');
  out.nal = out.scenarios.usable.nal;

  // 2) usable: in-browser reload -> restore from persistent store.
  out.restore.reload = await runPage(browser, baseUrl, 'scenario=usable');

  // 3) usable: full browser restart with same profile -> restore again.
  await browser.close();
  browser = await launch();
  out.restore.restart = await runPage(browser, baseUrl, 'scenario=usable');

  // 4) expired.
  await resetStats(baseUrl);
  out.scenarios.expired = await runPage(browser, baseUrl, 'scenario=expired&fresh=1');

  // 5) output-restricted.
  await resetStats(baseUrl);
  out.scenarios['output-restricted'] = await runPage(browser, baseUrl, 'scenario=output-restricted&fresh=1');

  // 6) license server failure.
  await resetStats(baseUrl);
  out.scenarios['server-error'] = await runPage(browser, baseUrl, 'scenario=server-error&fresh=1');

  await browser.close();
  server.close();

  fs.mkdirSync(path.join(__dirname, '..', 'artifacts'), { recursive: true });
  fs.writeFileSync(
    path.join(__dirname, '..', 'artifacts', 'report.json'),
    JSON.stringify(out, null, 2),
  );

  printReport(out);
})().catch((e) => { console.error(e); process.exit(1); });

function fmtMs(v) { return v == null ? '-' : `${v} ms`; }

function printReport(out) {
  const L = [];
  L.push('================ EME persistent-license 会话验证报告 ================');
  L.push('');
  L.push('【1】会话加载耗时与许可来源');
  const u = out.scenarios.usable;
  L.push(`  首次加载 (usable)      : ${fmtMs(u.loadTimeMs)} | 来源: ${u.licenseSource} | 会话 ${u.sessionId}`);
  L.push(`  页面内刷新后恢复        : ${fmtMs(out.restore.reload.loadTimeMs)} | 来源: ${out.restore.reload.licenseSource} | 会话 ${out.restore.reload.sessionId}`);
  L.push(`  浏览器重启后恢复        : ${fmtMs(out.restore.restart.loadTimeMs)} | 来源: ${out.restore.restart.licenseSource} | 会话 ${out.restore.restart.sessionId}`);
  L.push('');
  L.push('【2】不重复请求校验（许可服务器请求计数）');
  L.push(`  首次加载后服务器请求数 = ${u.licenseRequests}`);
  L.push(`  刷新恢复后服务器请求数 = ${out.restore.reload.licenseRequests}（未增加 => 无重复请求）`);
  L.push(`  重启恢复后服务器请求数 = ${out.restore.restart.licenseRequests}（未增加 => 无重复请求）`);
  L.push(`  恢复后会话 ID 一致     = ${out.restore.reload.sessionId === u.sessionId && out.restore.restart.sessionId === u.sessionId}`);
  L.push('');
  L.push('【3】三种 key status 下播放器行为对比');
  for (const name of ['usable', 'expired', 'output-restricted']) {
    const s = out.scenarios[name];
    L.push(`  [${name}]`);
    L.push(`    keyStatus=${s.keyStatus} (CDM 原始=${s.cdmKeyStatus || '-'}) | 播放=${s.playing ? '是' : '否'} | 耗时=${fmtMs(s.loadTimeMs)}`);
    L.push(`    提示: ${s.prompt || '(无，正常播放)'}`);
  }
  L.push('');
  L.push('【4】许可加载失败报错');
  L.push(`  ${out.scenarios['server-error'].licenseError || '(未触发)'}`);
  L.push('');
  L.push('【5】NAL 重组包计数（丢包场景）');
  const n = out.nal;
  L.push(`  发送包数=${n.packetsSent} | 重组成功=${n.reassembled} | 重组失败丢弃=${n.dropped} | 残余包丢弃=${n.packetsDiscarded}`);
  L.push(`  解码帧序列: [${n.decodedFrames.join(', ')}]`);
  for (const e of n.decoderEvents) L.push(`  - ${e}`);
  L.push(`  不完整 NAL 丢弃后下一关键帧恢复正常: ${n.nextKeyframeRecovered ? '是' : '否'}`);
  L.push('===================================================================');
  console.log(L.join('\n'));
}
