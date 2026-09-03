/*
 * Parity test: does model.js reproduce the ticker's maths exactly?
 *
 * This does not compare against a frozen copy. It pulls the live source out of
 * index.html, runs it, and checks every team, every fixture, both sides, both
 * modes and the whole colour ramp against model.js. If someone edits the maths
 * in index.html without updating model.js — or the other way round — this fails.
 *
 * Run:  node model.test.mjs
 */
import assert from "node:assert/strict";
import fs from "node:fs";
import * as M from "./model.js";

const HTML = "/home/claude/site/index.html";
const DATA = "/home/claude/site/data.json";

const d = JSON.parse(fs.readFileSync(DATA, "utf8"));
const html = fs.readFileSync(HTML, "utf8");

/* -- lift the maths out of index.html ------------------------------------- */
const START = "/* ---- continuous diverging colour ---- */";
const END = "function render(){";
const a = html.indexOf(START), b = html.indexOf(END);
assert.ok(a > 0 && b > a,
  "Could not find the maths block in index.html — the markers moved. " +
  "Check this test still points at the right code before trusting it.");
const src = html.slice(a, b);

for (const name of ["function strengths(", "function collect(", "function rawVal(", "function shade("]) {
  assert.ok(src.includes(name), `index.html no longer defines ${name} where expected`);
}

const RAMP_HEX = ["#80072D", "#FF1751", "#E7E7E7", "#05E585", "#116B33"];  // --hard … --easy
const CSS = { "--hard": RAMP_HEX[0], "--hard2": RAMP_HEX[1], "--midc": RAMP_HEX[2],
              "--easy2": RAMP_HEX[3], "--easy": RAMP_HEX[4] };

function ticker(mode) {
  const REC = M.recordsFrom(d.teams);
  const make = new Function(
    "REC", "TEAMS", "FX", "MATCHES_PLAYED", "MODE", "PEN", "css",
    src + "\n return { shade, strengths, collect, rawVal, good, ANCHOR };");
  return make(REC, d.teams, d.fixtures, d.matchesPlayed, mode, d.fit.pen, n => CSS[n]);
}

let passed = 0, failed = 0;
const test = (name, fn) => {
  try { fn(); passed++; console.log(`  ok   ${name}`); }
  catch (e) { failed++; console.log(`  FAIL ${name}\n       ${e.message}`); }
};
const close = (x, y, msg, tol = 1e-12) =>
  assert.ok(Math.abs(x - y) <= tol, `${msg}: ticker ${x} vs model ${y}`);

const REC = M.recordsFrom(d.teams);
const ids = Object.keys(d.teams);

console.log("\nStrengths — every team, across a spread of k values");
for (const [kA, kD, kp] of [[8, 35, 4], [0, 0, 0], [40, 40, 40], [8, 26, 4], [3, 12, 7]]) {
  test(`k = ${kA}/${kD}/${kp}`, () => {
    const old = ticker("ease").strengths(kA, kD, kp);
    const now = M.strengths(REC, d.matchesPlayed, kA, kD, kp);
    close(old.base, now.base, "base");
    for (const id of ids) {
      close(old.ATK[id], now.ATK[id], `ATK[${id}]`);
      close(old.DEF[id], now.DEF[id], `DEF[${id}]`);
    }
    close(old.wA, now.wA, "wA"); close(old.wD, now.wD, "wD"); close(old.wP, now.wP, "wP");
  });
}

console.log("\nFixture assembly");
for (const [from, to] of [[1, 38], [3, 8], [3, 3], [20, 25]]) {
  test(`GW${from}–${to}`, () => {
    const old = ticker("ease").collect(from, to);
    const now = M.collect(d.fixtures, ids, from, to);
    assert.deepEqual(Object.keys(now).sort(), Object.keys(old).sort(), "team list");
    for (const id of ids) assert.deepEqual(now[id], old[id], `fixtures for ${id}`);
  });
}

console.log("\nFixture values — every team, every fixture, both sides, both modes");
for (const mode of ["ease", "proj"]) {
  for (const home of [d.fit.home, 1]) {
    test(`${mode}, home ${home}`, () => {
      const t = ticker(mode);
      const S_old = t.strengths(d.fit.kAtk, d.fit.kDef, 4);
      const S_new = M.strengths(REC, d.matchesPlayed, d.fit.kAtk, d.fit.kDef, 4);
      const fx_old = t.collect(1, 38), fx_new = M.collect(d.fixtures, ids, 1, 38);
      let n = 0;
      for (const id of ids) {
        for (let i = 0; i < fx_new[id].length; i++) {
          for (const side of ["atk", "def"]) {
            const o = t.rawVal(side, id, fx_old[id][i], S_old, home);
            const m = M.rawVal(side, id, fx_new[id][i], S_new, home, mode, d.fit.pen);
            close(o, m, `${side} ${mode} team ${id} fixture ${i}`);
            close(t.good(side, o), M.good(side, m, mode), `good ${side} ${mode}`);
            n++;
          }
        }
      }
      assert.ok(n > 700, `only checked ${n} values`);
    });
  }
}

console.log("\nColour ramp");
test("201 points along the scale match, including out of range", () => {
  const t = ticker("ease");
  const ramp = M.rampFrom(RAMP_HEX);
  for (let i = -10; i <= 210; i++) {
    const x = i / 200;
    assert.equal(M.shade(x, ramp).bg, t.shade(x).bg, `bg at t=${x}`);
    assert.equal(M.shade(x, ramp).fg, t.shade(x).fg, `fg at t=${x}`);
  }
});

console.log("\nAnchors");
test("the anchor table is unchanged", () => {
  assert.deepEqual(M.ANCHOR, ticker("ease").ANCHOR);
});

console.log("\nfromData convenience");
test("defaults to the fitted constants", () => {
  const m = M.fromData(d);
  const S = M.strengths(REC, d.matchesPlayed, d.fit.kAtk, d.fit.kDef, 4);
  for (const id of ids) close(m.S.ATK[id], S.ATK[id], `ATK[${id}]`);
  assert.equal(m.home, d.fit.home);
  assert.equal(m.nextGw, d.nextGw);
});
test("rate() agrees with rawVal()", () => {
  const m = M.fromData(d);
  const fx = m.fixtures(d.nextGw, d.nextGw + 5);
  for (const id of ids) for (const f of fx[id]) {
    close(m.rate("atk", id, f, "proj"),
          M.rawVal("atk", id, f, m.S, d.fit.home, "proj", d.fit.pen), "rate atk");
    close(m.rate("def", id, f, "proj"),
          M.rawVal("def", id, f, m.S, d.fit.home, "proj", d.fit.pen), "rate def");
  }
});

console.log(`\n${passed} passed, ${failed} failed\n`);
process.exit(failed ? 1 : 0);
