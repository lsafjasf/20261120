const fs = require('fs');
const path = require('path');
const W = __dirname;
const puppeteer = require('/home/hj/userfeedback/uid4-0025/node_modules/puppeteer');
const { buildPage } = require(W + '/lib/browser-page');
const { generate } = require(W + '/lib/generate-css');
const { buildMappedHtml } = require(W + '/lib/build-html');
const CHROME = '/home/hj/.cache/puppeteer/chrome/linux-157.0.8103.50/chrome-linux64/chrome';
const IMSC_URL = 'file://' + path.resolve(W + '/node_modules/imsc/dist/main/main.js');

(async () => {
  const browser = await puppeteer.launch({ executablePath: CHROME,
    headless: 'new', args: ['--no-sandbox', '--allow-file-access-from-files'] });
  const file = process.argv[2] || 'vertical-ruby.itt';
  const ttmlXml = fs.readFileSync(W + '/samples/' + file, 'utf8');
  const mapped = generate(ttmlXml);
  const html = buildPage({ imscUrl: IMSC_URL, ttmlXml,
    mappedHtml: buildMappedHtml(ttmlXml), mappedCss: mapped.css });
  fs.writeFileSync(W + '/reports/dbg.html', html);
  const page = await browser.newPage();
  await page.goto('file://' + path.resolve(W + '/reports/dbg.html'));
  await page.waitForFunction('window.__result || window.__error');
  const out = await page.evaluate(() => {
    return [...document.querySelectorAll('#ref-root div')].map((d, i) => {
      const r = d.getBoundingClientRect();
      return { i, pos: getComputedStyle(d).position,
        inline: (d.getAttribute('style') || '').match(/(top|left|width|height|padding|writing|justify):[^;]+/g),
        rect: { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) },
        kids: d.children.length };
    });
  });
  console.log(JSON.stringify(out, null, 1));
  await browser.close();
})().catch(e => { console.error(e); process.exit(1); });
