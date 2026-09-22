/*
 * model.js — does it still produce exactly what the site used to produce?
 *
 * Until 21 September 2026 this was a parity test: it lifted the maths out of
 * index.html, ran it, and compared it with model.js. index.html no longer has
 * its own copy — it imports this module, as planner.html and compare.html do —
 * so there is nothing left to compare against inside the page.
 *
 * What replaces it is model.fixture.json: every value the inline code produced
 * on the day it was deleted, frozen together with the inputs that produced
 * them. The inputs are frozen too, so the weekly build cannot make this test
 * stale, and a change in the maths shows up as a named value rather than a
 * vague failure. Regenerate the fixture only when the model is deliberately
 * changed, and say so in the commit.
 *
 * Run:  node model.test.mjs
 */
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import * as M from "./model.js";

// Resolved against this file, not the shell's working directory, so the test
// runs from anywhere and on any machine. It used to carry absolute paths from
// the container it was written in, which made it unrunnable everywhere else.
const HERE = path.dirname(fileURLToPath(import.meta.url));
const read = f => fs.readFileSync(path.join(HERE, f), "utf8");

const F = JSON.parse(read("model.fixture.json"));
const d = JSON.parse(read("data.json"));          // live, for the calendar check only
const IN = F.input, EX = F.expected;
const ids = Object.keys(IN.teams).sort((x, y) => +x - +y);

let passed = 0, failed = 0;
const test = (name, fn) => {
  try { fn(); passed++; console.log(`  ok   ${name}`); }
  catch (e) { failed++; console.log(`  FAIL ${name}\n       ${e.message}`); }
};
// The fixture stores six decimal places; anything that moves a fixture's value
// by less than that is not a change anyone can see.
const close = (x, y, msg, tol = 5e-7) =>
  assert.ok(Math.abs(x - y) <= tol, `${msg}: frozen ${x} vs model ${y}`);

const REC = M.recordsFrom(IN.teams);

console.log("\nStrengths — every team, across a spread of k values");
for (const e of EX.strengths) {
  const [kA, kD, kp] = e.k;
  test(`k = ${kA}/${kD}/${kp}`, () => {
    const now = M.strengths(REC, IN.matchesPlayed, kA, kD, kp);
    close(e.base, now.base, "base");
    close(e.wA, now.wA, "wA"); close(e.wD, now.wD, "wD"); close(e.wP, now.wP, "wP");
    for (const id of ids) {
      close(e.ATK[id], now.ATK[id], `ATK[${id}]`);
      close(e.DEF[id], now.DEF[id], `DEF[${id}]`);
    }
  });
}

console.log("\nFixture assembly");
for (const window in EX.collected) {
  const [from, to] = window.split("-").map(Number);
  test(`GW${from}–${to}`, () => {
    const now = M.collect(IN.fixtures, ids, from, to);
    assert.deepEqual(Object.keys(now).sort(), Object.keys(EX.collected[window]).sort(), "team list");
    for (const id of ids) {
      const frozen = EX.collected[window][id].map(([gw, opp, home, fdr]) => ({ gw, opp, home: !!home, fdr }));
      assert.deepEqual(now[id], frozen, `fixtures for ${id}`);
    }
  });
}

console.log("\nFixture values — every team, every fixture, both sides, both modes");
for (const key in EX.values) {
  const [mode, home] = key.split("@");
  test(`${mode}, home ${home}`, () => {
    const S = M.strengths(REC, IN.matchesPlayed, IN.fit.kAtk, IN.fit.kDef, 4);
    const fx = M.collect(IN.fixtures, ids, 1, 38);
    const want = EX.values[key];
    // One entry per fixture in each of the four arrays, so the counter moves
    // once per fixture and not once per side.
    let k = 0;
    for (const id of ids) for (const f of fx[id]) {
      for (const side of ["atk", "def"]) {
        const v = M.rawVal(side, id, f, S, +home, mode, IN.fit.pen);
        close(want[side][k], v, `${side} ${mode} team ${id} GW${f.gw}`);
        close(want[side + "Good"][k], M.good(side, v, mode), `good ${side} ${mode} team ${id} GW${f.gw}`);
      }
      k++;
    }
    assert.equal(k, want.atk.length, "checked a different number of fixtures than were frozen");
    assert.ok(k * 4 > 700, `only checked ${k * 4} values`);
  });
}

console.log("\nBookmakers' odds override the model where they exist");
test(`${EX.marketCases.length} priced fixtures take the market price`, () => {
  const S = M.strengths(REC, IN.matchesPlayed, IN.fit.kAtk, IN.fit.kDef, 4);
  for (const c of EX.marketCases) {
    const f = { gw: c.gw, opp: c.opp, home: !!c.home };
    close(c.v, M.rawVal(c.side, c.team, f, S, IN.fit.home, "proj", IN.fit.pen, EX.market),
      `${c.side} team ${c.team} GW${c.gw}`);
  }
});
test("with no market every one of them falls back to the model", () => {
  const S = M.strengths(REC, IN.matchesPlayed, IN.fit.kAtk, IN.fit.kDef, 4);
  for (const c of EX.marketCases) {
    const f = { gw: c.gw, opp: c.opp, home: !!c.home };
    close(c.unpriced, M.rawVal(c.side, c.team, f, S, IN.fit.home, "proj", IN.fit.pen, null),
      `${c.side} team ${c.team} GW${c.gw}`);
  }
  assert.ok(EX.marketCases.some(c => Math.abs(c.v - c.unpriced) > 0.01),
    "the market and the model agree everywhere, so this proves nothing");
});
test("the odds are ignored in the opponent-index view", () => {
  const S = M.strengths(REC, IN.matchesPlayed, IN.fit.kAtk, IN.fit.kDef, 4);
  for (const c of EX.marketCases.slice(0, 20)) {
    const f = { gw: c.gw, opp: c.opp, home: !!c.home };
    close(M.rawVal(c.side, c.team, f, S, IN.fit.home, "ease", IN.fit.pen, EX.market),
          M.rawVal(c.side, c.team, f, S, IN.fit.home, "ease", IN.fit.pen, null),
          `${c.side} team ${c.team} GW${c.gw}`);
  }
});
test("market.json's short codes translate to team ids", () => {
  const short = {};              // rebuild the file's own shape from the fixture
  for (const gw in EX.market) {
    short[gw] = {};
    for (const id in EX.market[gw]) short[gw][IN.teams[id].short] = EX.market[gw][id];
  }
  assert.deepEqual(M.marketByTeamId(short, IN.teams), EX.market);
  assert.equal(M.marketByTeamId(null, IN.teams), null);
  assert.equal(M.marketByTeamId({ 7: { nope: 1.5 } }, IN.teams), null, "unknown codes are not a market");
});
test("priced() answers for the side whose number is on screen", () => {
  const gw = +Object.keys(EX.market)[0];
  const teams = Object.keys(EX.market[gw]);
  const m = M.fromData({ ...IN, nextGw: 1, deadlines: Array(38).fill("2099-01-01") },
    { market: EX.market });
  const [a, b] = teams;
  assert.equal(m.priced(a, { gw, opp: b }, "atk"), true);
  assert.equal(m.priced(a, { gw, opp: b }, "def"), true);
  // a clean sheet is priced off the OPPONENT's goals, so an unpriced opponent
  // leaves the defensive number ours even where the team itself is priced
  const unpriced = ids.find(id => EX.market[gw][id] == null);
  if (unpriced) {
    assert.equal(m.priced(a, { gw, opp: +unpriced }, "def"), false);
    assert.equal(m.priced(a, { gw, opp: +unpriced }, "atk"), true);
  }
  assert.equal(m.priced(a, { gw: 99, opp: b }, "atk"), false, "an unpriced round");
  assert.equal(M.fromData({ ...IN, nextGw: 1, deadlines: Array(38).fill("2099-01-01") })
    .priced(a, { gw, opp: b }), false, "no market at all");
});

console.log("\nColour ramp");
test(`${EX.ramp221.length} points along the scale match, including out of range`, () => {
  const ramp = M.rampFrom(F.rampHex);
  EX.ramp221.forEach(([bg, fg], i) => {
    const x = (i - 10) / 200;
    const s = M.shade(x, ramp);
    assert.equal(s.bg, bg, `bg at t=${x}`);
    assert.equal(s.fg, fg, `fg at t=${x}`);
  });
});

console.log("\nAnchors and formatting");
test("the anchor table is unchanged", () => {
  assert.deepEqual(M.ANCHOR, EX.anchor);
});

console.log("\nColour bands");
test("the band table is Nahom's", () => {
  assert.deepEqual(M.BAND, { atk_proj: [1, 1.3, 1.6, 2], def_proj: [15, 25, 35, 45] });
});
test("every edge belongs to the band above it", () => {
  const cases = [
    ["atk", [[0.99, 0], [1, 1], [1.29, 1], [1.3, 2], [1.59, 2],
             [1.6, 3], [1.99, 3], [2, 4], [9, 4]]],
    ["def", [[14, 0], [15, 1], [24, 1], [25, 2], [34, 2],
             [35, 3], [44, 3], [45, 4], [100, 4]]],
  ];
  for (const [side, rows] of cases)
    for (const [v, want] of rows) {
      assert.equal(M.bandOf(side, v, "proj"), want, `${side} ${v}`);
      assert.equal(M.colourT(side, v, "proj"), want / 4, `${side} ${v} colour`);
    }
});
test("a number is banded as it is printed, not as it is held", () => {
  assert.equal(M.bandOf("atk", 1.5996, "proj"), 3, "1.5996 prints as 1.60");
  assert.equal(M.bandOf("atk", 1.5949, "proj"), 2, "1.5949 prints as 1.59");
  assert.equal(M.bandOf("def", 34.6, "proj"), 3, "34.6 prints as 35%");
  assert.equal(M.bandOf("def", 14.5, "proj"), 1, "14.5 prints as 15%");
});
test("a band centre lands exactly on one of the five stops", () => {
  const ramp = M.rampFrom(F.rampHex);
  for (let b = 0; b < 5; b++)
    assert.equal(M.shade(b / 4, ramp).bg,
      `rgb(${ramp[b].join(",")})`, `band ${b} is not a flat stop`);
});
test("the ease index is not banded and keeps the ramp", () => {
  assert.equal(M.bandOf("atk", 1.1, "ease"), null);
  assert.equal(M.bandLabels("atk", "ease"), null);
  assert.equal(M.colourT("atk", 1.1, "ease"), M.goodClamped("atk", 1.1, "ease"));
});
test("the key reads the way the bands are written", () => {
  assert.deepEqual(M.bandLabels("atk", "proj"),
    ["<1.00", "1.00\u20131.29", "1.30\u20131.59", "1.60\u20131.99", "2.00+"]);
  assert.deepEqual(M.bandLabels("def", "proj"),
    ["<15%", "15\u201324%", "25\u201334%", "35\u201344%", "45%+"]);
});
test("the two rows are independent, and that is deliberate", () => {
  // A clean sheet is the Poisson complement of the opponent's expected goals,
  // so a scale derived from the xG row would colour the two sides of a fixture
  // as exact opposites. These rows are not derived from each other and do not:
  // an attack rated grey at 1.59 faces a defence its own table calls red at
  // 20%. That is the intended behaviour — the clean sheet colours are absolute,
  // not a mirror. If this test starts failing, someone has derived one row from
  // the other, which is a real decision and should be a deliberate one.
  assert.equal(M.bandOf("atk", 1.59, "proj"), 2, "1.59 xG is grey");
  assert.equal(M.bandOf("def", Math.exp(-1.59) * 100, "proj"), 1,
    "the defence facing it reads red, not the grey a mirrored scale would give");
});

console.log("\nfromData convenience");
const boot = extra => M.fromData({ ...IN, nextGw: 1, deadlines: Array(38).fill("2099-01-01") }, extra);
test("defaults to the fitted constants", () => {
  const m = boot();
  const S = M.strengths(REC, IN.matchesPlayed, IN.fit.kAtk, IN.fit.kDef, 4);
  for (const id of ids) close(m.S.ATK[id], S.ATK[id], `ATK[${id}]`);
  assert.equal(m.home, IN.fit.home);
  assert.equal(m.pen, IN.fit.pen);
});
test("rate() agrees with rawVal()", () => {
  const m = boot();
  const fx = m.fixtures(1, 38);
  for (const id of ids) for (const f of fx[id]) for (const side of ["atk", "def"])
    close(m.rate(side, id, f, "proj"),
      M.rawVal(side, id, f, m.S, IN.fit.home, "proj", IN.fit.pen), `rate ${side}`);
});
test("the ticker's query-string overrides reach the model", () => {
  // ?home=1&kAtk=20 — no UI, but the ticker honours them and they must not be
  // quietly ignored now that the page no longer builds its own strengths().
  const m = boot({ home: 1, kAtk: 20 });
  assert.equal(m.home, 1);
  const S = M.strengths(REC, IN.matchesPlayed, 20, IN.fit.kDef, 4);
  for (const id of ids) close(m.S.ATK[id], S.ATK[id], `ATK[${id}]`);
  const f = { gw: 1, opp: +ids[1], home: true };
  close(m.rate("atk", +ids[0], f, "ease"),
    M.rawVal("atk", +ids[0], f, S, 1, "ease", IN.fit.pen), "home advantage removed");
});

/* -- rotating two of anything --------------------------------------------- */
// One rule, two callers: the ticker rotates two teams, the comparison page two
// players. It lived in both files until 21 Sep 2026 with nothing stopping the
// copies drifting, and a blank gameweek had already been got wrong twice.
console.log("\nrotate");
const rot = (a, b) => M.rotate(a, b);
test("plays whichever side has the better week", () => {
  const r = rot([1, 0.2, 0.9], [0.5, 0.8, 0.1]);
  assert.deepEqual(r.pick.map(p => p.take), [0, 1, 0]);
  close(r.avg, (1 + 0.8 + 0.9) / 3, "avg");
});
test("the gain is measured against the better of the two, not the worse", () => {
  const a = [1, 0.2, 0.9], b = [0.5, 0.8, 0.1];
  const r = rot(a, b);
  const mean = x => x.reduce((s, v) => s + v, 0) / x.length;
  close(r.gain, r.avg - Math.max(mean(a), mean(b)), "gain");
});
test("rotating two equal sides gains nothing", () => {
  const r = rot([0.4, 0.6], [0.4, 0.6]);
  close(r.gain, 0, "gain");
});
test("a blank gameweek scores 0, never a sentinel", () => {
  // A sentinel averaged in once put a negative expected-goals figure on screen.
  const r = rot([0.8, null], [0.6, null]);
  close(r.avg, 0.4, "avg");
  assert.equal(r.pick[1].blank, true);
  assert.ok(r.avg >= 0, "a blank week can never drag the average below zero");
});
test("a blank week never wins a tie", () => {
  // Both rate 0, but one of them actually plays. Hand it to the blank side and
  // the strip tells you to start nobody.
  assert.equal(rot([null, 1], [0, 1]).pick[0].take, 1, "b plays, a is blank");
  assert.equal(rot([0, 1], [null, 1]).pick[0].take, 0, "a plays, b is blank");
  assert.equal(rot([null, 1], [null, 1]).pick[0].blank, true, "neither plays");
});
test("covering the other's blank beats owning either alone", () => {
  const r = rot([0.9, null], [null, 0.9]);
  close(r.avg, 0.9, "avg");
  close(r.gain, 0.45, "gain");
});
test("a tie between two sides that both play goes to the first", () => {
  assert.equal(rot([0.5], [0.5]).pick[0].take, 0);
});
test("an empty window does not divide by zero", () => {
  const r = rot([], []);
  assert.ok(Number.isFinite(r.avg) && Number.isFinite(r.gain), `avg ${r.avg}, gain ${r.gain}`);
  assert.deepEqual(r.pick, []);
});
test("mismatched series are a programming error, not a silent wrong answer", () => {
  assert.throws(() => rot([1, 2], [1]), /differ in length/);
});

/* -- the gameweek window rolls over --------------------------------------- */
// A scheduled build can be a day stale, and FPL leaves a round's `finished`
// flag False long after it has been played, so nextGw in the file is not
// trusted on its own. These pin the rule: never behind the calendar, never
// dragged backwards, and never past 38.
console.log("\nnextGwFrom");
const cal = (nextGw, deadlines, deadlineTimes) =>
  M.nextGwFrom({ nextGw, deadlines, ...(deadlineTimes ? { deadlineTimes } : {}) });

const past = new Date(Date.now() - 3 * 864e5).toISOString();
const soon = new Date(Date.now() + 5 * 864e5).toISOString();
const later = new Date(Date.now() + 12 * 864e5).toISOString();

test("moves past a gameweek whose deadline has gone", () => {
  assert.equal(cal(3, [past, past, past, soon, later]), 4);
});
test("stays put when the file is already right", () => {
  assert.equal(cal(4, [past, past, past, soon, later]), 4);
});
test("never drags the window backwards", () => {
  assert.equal(cal(5, [past, past, past, soon, later]), 5);
});
test("uses deadlineTimes when they are there", () => {
  assert.equal(cal(3, ["x", "x", "x", "x", "x"].map(() => past.slice(0, 10)),
                   [past, past, past, soon, later]), 4);
});
test("a date-only deadline keeps the deadline day itself upcoming", () => {
  const today = new Date().toISOString().slice(0, 10);
  assert.equal(cal(2, [past.slice(0, 10), today, later.slice(0, 10)]), 2);
});
test("clamps to 38 once the season is out of deadlines", () => {
  assert.equal(cal(37, Array(38).fill(past)), 38);
});
test("the live data.json is never behind its own calendar", () => {
  const n = M.nextGwFrom(d);
  assert.ok(n >= 1 && n <= 38, `nextGw ${n} out of range`);
  const dl = d.deadlines[n - 1];
  const cutoff = dl.length > 10 ? Date.parse(dl) : Date.parse(dl + "T23:59:59Z");
  assert.ok(cutoff > Date.now(),
    `GW${n} is offered as next but its deadline (${dl}) has passed`);
});

/* -- the pages no longer carry copies of any of this ---------------------- */
// The whole point of the migration. If a copy of the maths reappears in a page,
// the two can drift and nothing else here would notice.
console.log("\nOne copy of the maths, not three");
for (const page of ["index.html", "planner.html", "compare.html"]) {
  test(`${page} imports model.js rather than repeating it`, () => {
    const html = read(page);
    assert.match(html, /import\s*\{[^}]*\}\s*from\s*"\.\/model\.js"/,
      "no import of model.js");
    for (const copy of ["function strengths(", "function collect(",
                        "function rawVal(", "const nextGwFrom", "hex2rgb"]) {
      assert.ok(!html.includes(copy), `${page} has its own ${copy}…) again`);
    }
    assert.ok(!/const ANCHOR\s*=/.test(html), `${page} has its own anchor table again`);
    assert.ok(!/const BAND\s*=/.test(html), `${page} has its own band table again`);
  });
}

console.log(`\n${passed} passed, ${failed} failed\n`);
process.exit(failed ? 1 : 0);
