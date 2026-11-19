const fs = require('fs');
const path = require('path');
const W = __dirname;
const puppeteer = require('/home/hj/userfeedback/uid4-0025/node_modules/puppeteer');
const { buildPage } = require(W + '/lib/browser-page');
const { generate } = require(W + '/lib/generate-css');
const { buildMappedHtml } = require(W + '/lib/build-html');

(async () => {
  const browser = await puppeteer.launch({
    executablePath: '/home/hj/.cache/puppeteer/chrome/linux-157.0.8103.50/chrome-linux64/chrome',
    headless: 'new', args: ['--no-sandbox', '--allow-file-access-from-files'] });
  const ttmlXml = fs.readFileSync(W + '/samples/multilang.itt', 'utf8');
  const mapped = generate(ttmlXml);
  const html = buildPage({
    imscUrl: 'file://' + path.resolve(W + '/node_modules/imsc/dist/main/main.js'),
    ttmlXml, mappedHtml: buildMappedHtml(ttmlXml), mappedCss: mapped.css });
  fs.writeFileSync(W + '/reports/dbg.html', html);
  const page = await browser.newPage();
  await page.goto('file://' + path.resolve(W + '/reports/dbg.html'));
  await page.waitForFunction('window.__result || window.__error');
  const out = await page.evaluate(() => {
    const ref = document.getElementById('ref-root');
    const ps = [...ref.querySelectorAll('p')];
    const last = ps[ps.length - 1];
    const walk = el => ({
      tag: el.tagName.toLowerCase(),
      style: (el.getAttribute('style') || '').match(/(font-size|line-height|padding|height)[^;]+/g),
      rect: (r => ({ h: Math.round(r.height * 10) / 10, y: Math.round(r.y * 10) / 10 }))(
        el.getBoundingClientRect()),
      kids: [...el.children].map(walk) });
    return { lastP: walk(last) };
  });
  console.log(JSON.stringify(out, null, 1));
  await browser.close();
})().catch(e => { console.error(e); process.exit(1); });
