// The fixtures page header must end at "data updated <generated>", and never
// show a separate ratings date -- even while Understat is down and data.json's
// ratingsAsOf is days older than generated. Run: node stamp.test.mjs
// Needs Playwright with Chromium; skipped (not failed) without it.
import { createRequire } from "module";
import http from "http";
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const require = createRequire(import.meta.url);
let chromium;
try { ({ chromium } = require("playwright")); }
catch { console.log("  (playwright not found - skipped the header check)"); process.exit(0); }

let passed = 0, failed = 0;
const check = (name, cond, detail = "") => {
  if (cond) { passed++; console.log("  ok  ", name); }
  else { failed++; console.log("  FAIL", name, detail); }
};

const TYPES = { ".html": "text/html", ".js": "text/javascript", ".mjs": "text/javascript",
  ".css": "text/css", ".json": "application/json", ".svg": "image/svg+xml", ".png": "image/png" };
const server = http.createServer((req, res) => {
  const f = path.join(HERE, decodeURIComponent(new URL(req.url, "http://x").pathname));
  if (!f.startsWith(HERE) || !fs.existsSync(f) || fs.statSync(f).isDirectory()) {
    res.writeHead(404); return res.end();
  }
  res.writeHead(200, { "Content-Type": TYPES[path.extname(f)] || "application/octet-stream" });
  fs.createReadStream(f).pipe(res);
});
await new Promise(r => server.listen(0, "127.0.0.1", r));
const base = `http://127.0.0.1:${server.address().port}`;

const real = JSON.parse(fs.readFileSync(path.join(HERE, "data.json"), "utf8"));
const browser = await chromium.launch();
try {
  for (const [label, ratingsAsOf] of [
    ["ratings 9 days older than generated", "2026-11-01T21:12:47+00:00"],
    ["ratings as of generated", null],
  ]) {
    const d = { ...real, generated: "2026-11-10T09:00:00+00:00",
                ratingsAsOf: ratingsAsOf || "2026-11-10T09:00:00+00:00" };
    const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
    const errors = [];
    page.on("pageerror", e => errors.push(e.message));
    page.on("console", m => { if (m.type() === "error") errors.push(m.text()); });
    // Only data.json is swapped; everything else is the page as served.
    await page.route(/\/data\.json/, r => r.fulfill({ contentType: "application/json", body: JSON.stringify(d) }));
    await page.route(/goatcounter/, r => r.abort());
    await page.goto(`${base}/index.html`);
    await page.waitForFunction(() => !/Loading/.test(document.querySelector("#stamp").textContent));
    const text = await page.$eval("#stamp", e => e.textContent);
    check(`${label}: no "Ratings last updated"`, !/ratings/i.test(text), text);
    check(`${label}: ends at data updated <generated>`,
          text.endsWith("data updated 10 Nov") && text.includes(`${d.matchesPlayed} matches played`), text);
    check(`${label}: no page errors`, errors.filter(e => !/goatcounter|ERR_FAILED/.test(e)).length === 0, errors);
    await page.close();
  }
} finally {
  await browser.close();
  server.close();
}
console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed ? 1 : 0);
