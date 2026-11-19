const fs = require('fs');
const path = require('path');
const W = __dirname;
const puppeteer = require('/home/hj/userfeedback/uid4-0025/node_modules/puppeteer');
const CHROME = '/home/hj/.cache/puppeteer/chrome/linux-157.0.8103.50/chrome-linux64/chrome';
const IMSC_URL = 'file://' + path.resolve(W + '/node_modules/imsc/dist/main/main.js');

const xml = `<?xml version="1.0"?>
<tt xml:lang="ja" xmlns="http://www.w3.org/ns/ttml"
  xmlns:tts="http://www.w3.org/ns/ttml#styling"
  xmlns:ttp="http://www.w3.org/ns/ttml#parameter"
  ttp:cellResolution="32 15" tts:extent="1280px 720px">
 <head><layout>
  <region xml:id="r1" tts:origin="1040px 48px" tts:extent="200px 624px"
    tts:writingMode="tbrl" tts:padding="0.25c"/>
 </layout></head>
 <body><div region="r1"><p begin="0s" end="10s">TEST</p></div></body>
</tt>`;

const html = `<!doctype html><html><head><script type="importmap">
{"imports":{"tslib":"file://${W}/node_modules/tslib/tslib.es6.js"}}</script></head>
<body><div id="h"></div><script type="module">
const imsc = await import(${JSON.stringify(IMSC_URL)});
const errs = [];
const doc = imsc.fromXML(${JSON.stringify(xml)}, e => errs.push(String(e && e.message || e)));
const out = { errs };
const docRegion = Object.values(doc.head.layout.regions)[0];
out.pxLength = { w: String(doc.pxLength.w), h: String(doc.pxLength.h) };
out.cellLength = { w: String(doc.cellLength.w), h: String(doc.cellLength.h) };
out.dimensions = { w: String(doc.dimensions.w), h: String(doc.dimensions.h) };
out.docRegionAttrs = Object.keys(docRegion.styleAttrs).map(k => {
  const v = docRegion.styleAttrs[k];
  const name = k.split(/[# ]/).pop();
  if (v && typeof v === 'object') {
    if (v.w && v.h) return name + ': ' + JSON.stringify({ w: v.w, h: v.h });
    if (Array.isArray(v)) return name + ': [' + v.map(x => x.value + x.unit).join(',') + ']';
  }
  return name + '=' + String(v);
});
const isd = imsc.generateISD(doc, 3.0);
const region = isd.contents[0];
const sa = region.styleAttrs;
out.keys = Object.keys(sa).map(k => k.split(/[# ]/).pop());
for (const k of Object.keys(sa)) {
  const v = sa[k];
  if (v && v.w && v.h) out[k.split(/[# ]/).pop()] = {
    w: v.w.toUsedLength(1280, 720), h: v.h.toUsedLength(1280, 720) };
}
imsc.renderHTML(isd, document.getElementById('h'), null, 720, 1280, false,
  m => errs.push(String(m && m.message || m)), null, false);
const reg = document.querySelector('#h div[style*="absolute"]');
out.renderedRegion = reg ? reg.getAttribute('style').match(/(top|left|width|height):[^;]+/g) : null;
window.__mini = out;
</script></body></html>`;

(async () => {
  const browser = await puppeteer.launch({ executablePath: CHROME,
    headless: 'new', args: ['--no-sandbox', '--allow-file-access-from-files'] });
  const page = await browser.newPage();
  page.on('pageerror', e => console.log('PAGEERROR', String(e)));
  page.on('console', m => console.log('CONSOLE', m.text()));
  const f = path.resolve(W + '/reports/mini.html');
  fs.writeFileSync(f, html);
  await page.goto('file://' + f);
  await page.waitForFunction('window.__mini', { timeout: 10000 });
  console.log(JSON.stringify(await page.evaluate(() => window.__mini), null, 1));
  await browser.close();
})().catch(e => { console.error(e); process.exit(1); });
