const fs = require('fs');
const path = require('path');
const W = __dirname;
const puppeteer = require('/home/hj/userfeedback/uid4-0025/node_modules/puppeteer');
const CHROME = '/home/hj/.cache/puppeteer/chrome/linux-157.0.8103.50/chrome-linux64/chrome';
const IMSC_URL = 'file://' + path.resolve(W + '/node_modules/imsc/dist/main/main.js');

const html = `<!doctype html><html><head><script type="importmap">
{"imports":{"tslib":"file://${W}/node_modules/tslib/tslib.es6.js"}}</script></head>
<body><script type="module">
const imsc = await import(${JSON.stringify(IMSC_URL)});
const xml = ${JSON.stringify(fs.readFileSync(W + '/samples/vertical-ruby.itt', 'utf8'))};
const doc = imsc.fromXML(xml, e => { throw new Error(String(e)); });
const isd = imsc.generateISD(doc, 3.0);
function walk(n, d, out) {
  if (d > 3) return;
  const sa = n.styleAttrs || {};
  const q = name => Object.keys(sa).find(k => k.split(/[# ]/).pop() === name);
  const originQ = q('origin'), extentQ = q('extent'), padQ = q('padding');
  const keys = originQ ? Object.keys(sa[originQ]) : [];
  const raw = originQ ? JSON.parse(JSON.stringify(sa[originQ],
    (k, v) => typeof v === 'number' ? v : String(v))) : null;
  const used = o => o ? { w: o.w.toUsedLength(1280, 720), h: o.h.toUsedLength(1280, 720) } : null;
  out.push({ d, kind: n.kind, id: n.id,
    keys, raw,
    originPx: originQ ? used(sa[originQ]) : null,
    extentPx: extentQ ? used(sa[extentQ]) : null,
    paddingPx: padQ ? sa[padQ].map(x => x.toUsedLength(1280, 720)) : null });
  (n.contents || n.children || []).forEach(c => walk(c, d + 1, out));
}
const out = [];
walk(isd, 0, out);
window.__isd = out;
</script></body></html>`;

(async () => {
  fs.writeFileSync(W + '/reports/isd.html', html);
  const browser = await puppeteer.launch({ executablePath: CHROME,
    headless: 'new', args: ['--no-sandbox', '--allow-file-access-from-files'] });
  const page = await browser.newPage();
  await page.goto('file://' + path.resolve(W + '/reports/isd.html'));
  await page.waitForFunction('window.__isd', { timeout: 10000 });
  console.log(JSON.stringify(await page.evaluate(() => window.__isd), null, 1));
  await browser.close();
})().catch(e => { console.error(e); process.exit(1); });
