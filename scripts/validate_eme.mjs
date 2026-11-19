#!/usr/bin/env node
// Standard EME validation for a rewritten PSSH initData blob.
//
// The environment only ships Chromium's built-in ClearKey CDM (no Widevine
// CDM), so the initData is the standard cenc concatenation of the rewritten
// Widevine PSSH plus a ClearKey PSSH carrying the same KID.  Chromium's EME
// implementation parses every PSSH box and emits a ClearKey license request;
// the key ids inside that request are compared with the expected default KID.

import { readFile, writeFile } from "node:fs/promises";
import { createHash } from "node:crypto";
import { createServer } from "node:http";
import { chromium } from "playwright";

function parseArgs(argv) {
  const args = {};
  for (let i = 2; i < argv.length; i += 1) {
    if (argv[i].startsWith("--")) {
      args[argv[i].slice(2)] = argv[i + 1];
      i += 1;
    }
  }
  if (!args.pssh || !args["default-kid"] || !args.out) {
    throw new Error("usage: validate_eme.mjs --pssh BIN --default-kid UUID --out JSON");
  }
  return args;
}

const args = parseArgs(process.argv);
const initData = await readFile(args.pssh);
const expectedKid = args["default-kid"].replaceAll("-", "").toLowerCase();
const sha256 = createHash("sha256").update(initData).digest("hex");

const browser = await chromium.launch({
  headless: true,
  args: ["--no-sandbox", "--disable-dev-shm-usage"],
});
const page = await browser.newPage();
const pageErrors = [];
page.on("pageerror", (error) => pageErrors.push(String(error)));
const server = createServer((request, response) => {
  response.writeHead(200, { "Content-Type": "text/html" });
  response.end("<!doctype html><html><body><h1>EME PSSH validation</h1></body></html>");
});
await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
const pageUrl = `http://127.0.0.1:${server.address().port}/`;
await page.goto(pageUrl);

const result = await page.evaluate(async ({ b64, expectedKidHex }) => {
  const initData = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
  const out = {
    emeAvailable: typeof navigator.requestMediaKeySystemAccess === "function",
    secureContext: window.isSecureContext,
    initDataBytes: initData.length,
  };
  if (!out.emeAvailable) return out;

  const configuration = {
    initDataTypes: ["cenc"],
    audioCapabilities: [],
    videoCapabilities: [
      { contentType: 'video/mp4; codecs="avc1.42E01E"' },
    ],
    distinctiveIdentifier: "not-allowed",
    persistentState: "not-allowed",
    sessionTypes: ["temporary"],
  };
  const access = await navigator.requestMediaKeySystemAccess(
    "org.w3.clearkey", [configuration]
  );
  out.configuration = access.getConfiguration();
  const mediaKeys = await access.createMediaKeys();
  const session = mediaKeys.createSession("temporary");
  const message = await new Promise((resolve, reject) => {
    session.addEventListener("message", (event) => resolve(event), { once: true });
    session.addEventListener("keystatuseschange", () => resolve(null));
    session
      .generateRequest("cenc", initData)
      .then(() => { out.generated = true; })
      .catch((error) => reject(error));
    setTimeout(() => reject(new Error("EME message timeout")), 15000);
  });
  out.generated = true;
  if (message) {
    out.messageReceived = true;
    const request = JSON.parse(new TextDecoder().decode(message.message));
    out.licenseRequest = request;
    out.parsedKids = (request.kids ?? []).map((kid) =>
      // base64url -> hex
      Array.from(
        new Uint8Array([...atob(kid.replaceAll("-", "+").replaceAll("_", "/"))]
          .map((ch) => ch.charCodeAt(0)))
      ).map((byte) => byte.toString(16).padStart(2, "0")).join("")
    );
  } else {
    out.messageReceived = false;
    out.parsedKids = [];
  }
  out.expectedKid = expectedKidHex;
  out.matchesDefault =
    out.parsedKids.length === 1 && out.parsedKids[0] === expectedKidHex;
  out.keySystem = "org.w3.clearkey";
  return out;
}, { b64: initData.toString("base64"), expectedKidHex: expectedKid });

const version = browser.version();
const userAgent = await page.evaluate(() => navigator.userAgent);
await browser.close();
await new Promise((resolve) => server.close(resolve));

const report = {
  passed: Boolean(result.matchesDefault && result.messageReceived),
  key_system: "org.w3.clearkey",
  pssh_sha256: sha256,
  browser_version: version.replace(/^[A-Za-z ]+/, ""),
  user_agent: userAgent,
  page_errors: pageErrors,
  result,
};
await writeFile(args.out, JSON.stringify(report, null, 2));
console.log(JSON.stringify({ passed: report.passed, parsedKids: result.parsedKids }, null, 2));
if (!report.passed) process.exit(1);
