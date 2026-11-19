// Screenshot the vertical-ruby subtitle element with headless Chromium.
const path = require("path");
const { chromium } = require("playwright");

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ deviceScaleFactor: 1 });
  const file = "file://" + path.resolve(__dirname, "subtitle.html");
  await page.goto(file);
  await page.evaluate(() => document.fonts.ready);
  const el = await page.locator("#sub");
  const box = await el.boundingBox();
  console.log("element box:", JSON.stringify(box));
  // dump per-ruby-character geometry for ground truth
  const geom = await page.evaluate(() => {
    const out = [];
    document.querySelectorAll("#sub ruby").forEach((r) => {
      const b = r.getBoundingClientRect();
      const rt = r.querySelector("rt").getBoundingClientRect();
      out.push({ base: { x: b.x, y: b.y, w: b.width, h: b.height },
                 ruby: { x: rt.x, y: rt.y, w: rt.width, h: rt.height } });
    });
    return out;
  });
  console.log("geometry:", JSON.stringify(geom, null, 1));
  require("fs").writeFileSync(
    path.resolve(__dirname, "..", "artifacts", "browser_geometry.json"),
    JSON.stringify(geom, null, 1));
  // ruby annotations overflow the element's content box; clip the
  // screenshot to the union of base and ruby boxes so nothing is cut off
  const x1 = Math.min(...geom.map(g => Math.min(g.base.x, g.ruby.x)));
  const y1 = Math.min(...geom.map(g => Math.min(g.base.y, g.ruby.y)));
  const x2 = Math.max(...geom.map(g => Math.max(g.base.x + g.base.w,
                                                g.ruby.x + g.ruby.w)));
  const y2 = Math.max(...geom.map(g => Math.max(g.base.y + g.base.h,
                                                g.ruby.y + g.ruby.h)));
  await page.screenshot({
    path: path.resolve(__dirname, "..", "artifacts", "subtitle_browser.png"),
    clip: { x: x1, y: y1, width: x2 - x1, height: y2 - y1 },
  });
  await browser.close();
})();
