/*
 * Tests for planner.js. Run with:  node planner.test.mjs
 * No test framework — node's own assert, so there is nothing to install.
 */
import assert from "node:assert/strict";
import {
  DEFAULT_RULES, POS, sellPrice, squadValue, validateSquad, validateXI,
  legalFormations, applyTransfers, advance, chipAvailable, planCost, newState
} from "./planner.js";

let passed = 0, failed = 0;
function test(name, fn) {
  try { fn(); passed++; console.log(`  ok   ${name}`); }
  catch (e) { failed++; console.log(`  FAIL ${name}\n       ${e.message}`); }
}
function section(s) { console.log(`\n${s}`); }

/* -- a legal 15 to work from: 2/5/5/3, no more than 3 per club -------------- */
function makeSquad() {
  const s = [];
  let id = 1;
  const add = (pos, n) => {
    for (let i = 0; i < n; i++) {
      s.push({ id: id, name: `p${id}`, pos, team: ((id - 1) % 8) + 1, buy: 50 });
      id++;
    }
  };
  add(POS.GK, 2); add(POS.DEF, 5); add(POS.MID, 5); add(POS.FWD, 3);
  return s;
}
const legalXI = sq => [
  sq.find(p => p.pos === 1).id,
  ...sq.filter(p => p.pos === 2).slice(0, 4).map(p => p.id),
  ...sq.filter(p => p.pos === 3).slice(0, 4).map(p => p.id),
  ...sq.filter(p => p.pos === 4).slice(0, 2).map(p => p.id)
];

section("Selling price — buy price plus half the rise, rounded down");
test("a rise is halved and rounded down", () => {
  assert.equal(sellPrice(50, 55), 52);   // +0.5m rise -> +0.2m back (2.5 floors to 2)
  assert.equal(sellPrice(50, 54), 52);
  assert.equal(sellPrice(50, 56), 53);
});
test("a fall is taken in full", () => {
  assert.equal(sellPrice(50, 47), 47);
});
test("no change sells at cost", () => {
  assert.equal(sellPrice(50, 50), 50);
});
test("squad value uses selling prices, not market prices", () => {
  const sq = makeSquad();
  const prices = Object.fromEntries(sq.map(p => [p.id, 56]));  // everyone +0.6m
  assert.equal(squadValue(sq, prices), 15 * 53);
});

section("Squad validation");
test("a legal squad has no problems", () => {
  assert.deepEqual(validateSquad(makeSquad()), []);
});
test("wrong shape is caught per position", () => {
  const sq = makeSquad().filter(p => p.pos !== POS.FWD);
  const errs = validateSquad(sq);
  assert.ok(errs.some(e => e.includes("FWD: 0 of 3")));
});
test("a fourth player from one club is caught", () => {
  const sq = makeSquad();
  sq[0].team = 1; sq[1].team = 1; sq[2].team = 1; sq[3].team = 1;
  assert.ok(validateSquad(sq).some(e => e.includes("limit is 3")));
});
test("duplicates are caught", () => {
  const sq = makeSquad();
  sq[14] = { ...sq[0] };
  assert.ok(validateSquad(sq).some(e => e.includes("twice")));
});
test("a negative bank reports how far over budget", () => {
  assert.ok(validateSquad(makeSquad(), { bank: -25 }).some(e => e.includes("£2.5m")));
});

section("Formation");
test("a legal XI passes", () => {
  const sq = makeSquad();
  assert.deepEqual(validateXI(legalXI(sq), sq), []);
});
test("two keepers in the XI is rejected", () => {
  const sq = makeSquad();
  const xi = legalXI(sq);
  xi[1] = sq.filter(p => p.pos === 1)[1].id;   // swap a defender for the 2nd GK
  assert.ok(validateXI(xi, sq).some(e => e.includes("GK")));
});
test("only two defenders is rejected", () => {
  const sq = makeSquad();
  const xi = [
    sq.find(p => p.pos === 1).id,
    ...sq.filter(p => p.pos === 2).slice(0, 2).map(p => p.id),
    ...sq.filter(p => p.pos === 3).slice(0, 5).map(p => p.id),
    ...sq.filter(p => p.pos === 4).slice(0, 3).map(p => p.id)
  ];
  assert.ok(validateXI(xi, sq).some(e => e.includes("at least 3")));
});
test("the legal formations are the eight FPL allows", () => {
  const f = legalFormations();
  // 5-2-3 is the one people forget: two midfielders is legal, just unusual.
  for (const shape of ["3-4-3", "3-5-2", "4-4-2", "4-3-3", "4-5-1", "5-3-2", "5-4-1", "5-2-3"]) {
    assert.ok(f.includes(shape), `missing ${shape}`);
  }
  assert.equal(f.length, 8);
});

section("Transfers, bank and hits");
test("a transfer moves the right money, using the sell-on fee", () => {
  const sq = makeSquad();
  const prices = Object.fromEntries(sq.map(p => [p.id, 56]));   // held player worth 5.6
  prices[99] = 70;
  const st = newState(3, sq, { bank: 20, ft: 1 });
  const out = applyTransfers(st, [{ out: sq[10].id, in: { id: 99, name: "new", pos: POS.MID, team: 12 } }], { prices });
  // sells at 50 + floor(6/2) = 53, buys at 70, so bank 20 + 53 - 70 = 3
  assert.equal(out.bank, 3);
  assert.equal(out.squad.find(p => p.id === 99).buy, 70, "purchase price is recorded as today's price");
});
test("one transfer with one free transfer costs nothing", () => {
  const sq = makeSquad();
  const st = newState(3, sq, { bank: 100, ft: 1 });
  const out = applyTransfers(st, [{ out: sq[10].id, in: { id: 99, pos: POS.MID, team: 12, buy: 50 } }]);
  assert.equal(out.cost, 0);
  assert.equal(out.ftLeft, 0);
});
test("a second transfer on one free transfer costs four points", () => {
  const sq = makeSquad();
  const st = newState(3, sq, { bank: 200, ft: 1 });
  const out = applyTransfers(st, [
    { out: sq[10].id, in: { id: 99,  pos: POS.MID, team: 12, buy: 50 } },
    { out: sq[11].id, in: { id: 100, pos: POS.MID, team: 13, buy: 50 } }
  ]);
  assert.equal(out.hits, 1);
  assert.equal(out.cost, 4);
});
test("a wildcard makes transfers free and leaves the free transfers alone", () => {
  const sq = makeSquad();
  const st = { ...newState(3, sq, { bank: 500, ft: 2 }), chip: "wildcard" };
  const out = applyTransfers(st, [
    { out: sq[10].id, in: { id: 99,  pos: POS.MID, team: 12, buy: 50 } },
    { out: sq[11].id, in: { id: 100, pos: POS.MID, team: 13, buy: 50 } },
    { out: sq[12].id, in: { id: 101, pos: POS.MID, team: 14, buy: 50 } }
  ]);
  assert.equal(out.cost, 0);
  assert.equal(out.ftLeft, 2, "the wildcard should not spend banked free transfers");
});
test("transfers are not applied to the original state", () => {
  const sq = makeSquad();
  const st = newState(3, sq, { bank: 100, ft: 1 });
  applyTransfers(st, [{ out: sq[10].id, in: { id: 99, pos: POS.MID, team: 12, buy: 50 } }]);
  assert.equal(st.squad.length, 15);
  assert.ok(st.squad.some(p => p.id === sq[10].id), "the original draft must be untouched");
});
test("an illegal result is reported, not silently accepted", () => {
  const sq = makeSquad();
  const st = newState(3, sq, { bank: 0, ft: 1 });
  // buy a forward to replace a midfielder: shape goes wrong, and it costs more than we have
  const out = applyTransfers(st, [{ out: sq[10].id, in: { id: 99, pos: POS.FWD, team: 12, buy: 120 } }]);
  assert.ok(out.problems.some(e => e.includes("MID")));
  assert.ok(out.problems.some(e => e.includes("Over budget")));
});

section("Rolling over a gameweek");
test("a free transfer is banked each week", () => {
  const sq = makeSquad();
  const st = { ...newState(3, sq, { bank: 0, ft: 1 }), ftLeft: 1 };
  assert.equal(advance(st).ft, 2);
});
test("banked free transfers stop at five", () => {
  const sq = makeSquad();
  const st = { ...newState(3, sq, { bank: 0, ft: 5 }), ftLeft: 5 };
  assert.equal(advance(st).ft, 5);
});
test("a free hit squad does not carry into the next week", () => {
  const sq = makeSquad();
  const st = { ...newState(3, sq, { bank: 10, ft: 1 }), chip: "freehit" };
  const played = applyTransfers(st, [
    { out: sq[10].id, in: { id: 99,  pos: POS.MID, team: 12, buy: 50 } },
    { out: sq[11].id, in: { id: 100, pos: POS.MID, team: 13, buy: 50 } }
  ]);
  assert.ok(played.squad.some(p => p.id === 99), "the free hit squad applies that week");
  const next = advance(played);
  assert.ok(!next.squad.some(p => p.id === 99), "and is gone the week after");
  assert.ok(next.squad.some(p => p.id === sq[10].id), "the original player is back");
  assert.equal(next.bank, 10, "and so is the money");
});
test("a wildcard squad does carry forward", () => {
  const sq = makeSquad();
  const st = { ...newState(3, sq, { bank: 500, ft: 1 }), chip: "wildcard" };
  const played = applyTransfers(st, [{ out: sq[10].id, in: { id: 99, pos: POS.MID, team: 12, buy: 50 } }]);
  assert.ok(advance(played).squad.some(p => p.id === 99));
});

section("Chips");
test("a chip can be played in its window", () => {
  assert.equal(chipAvailable("wildcard", 5, []).ok, true);
});
test("a wildcard used in the first half is gone for the first half only", () => {
  const used = [{ chip: "wildcard", gw: 5 }];
  assert.equal(chipAvailable("wildcard", 12, used).ok, false);
  assert.equal(chipAvailable("wildcard", 25, used).ok, true, "the second wildcard is a separate chip");
});
test("a wildcard cannot be played in GW1", () => {
  assert.equal(chipAvailable("wildcard", 1, []).ok, false);
});
test("bench boost can be played in GW1", () => {
  assert.equal(chipAvailable("bench", 1, []).ok, true);
});
test("only one chip per gameweek", () => {
  const used = [{ chip: "bench", gw: 7 }];
  const r = chipAvailable("triple", 7, used);
  assert.equal(r.ok, false);
  assert.ok(r.why.includes("Bench Boost"));
});

section("Whole plans");
test("hits are totalled across the plan", () => {
  assert.equal(planCost([{ cost: 0 }, { cost: 4 }, { cost: 8 }]), 12);
});

console.log(`\n${passed} passed, ${failed} failed\n`);
process.exit(failed ? 1 : 0);
