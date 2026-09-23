/*
 * Tests for planner.js. Run with:  node planner.test.mjs
 * No test framework — node's own assert, so there is nothing to install.
 */
import assert from "node:assert/strict";
import {
  DEFAULT_RULES, POS, sellPrice, squadValue, validateSquad, validateXI,
  legalFormations, applyTransfers, advance, chipAvailable, planCost, newState,
  bestXI, simulate, ftFromHistory, chipsFromHistory
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

section("Refused transfers are not charged");
test("a transfer of a player you do not own costs no hit", () => {
  const sq = makeSquad();
  const st = applyTransfers(newState(5, sq, { bank: 100, ft: 1 }),
    [{ out: 999, in: { id: 50, name: "x", pos: 3, team: 9, buy: 50 } },
     { out: sq[7].id, in: { id: 51, name: "y", pos: 3, team: 9, buy: 50 } }]);
  assert.equal(st.hits, 0);
  assert.equal(st.transfers.length, 1);
  assert.ok(st.problems.some(p => p.includes("not in the squad")));
});

section("Best XI by expected points");
const XP = { 1: 4, 2: 5, 3: 2, 4: 3, 5: 1, 6: 0.5, 7: 6, 8: 7, 9: 1, 10: 2, 11: 8, 12: 9, 13: 1, 14: 2, 15: 3 };
const xpOf = id => XP[id] ?? 0;
function bruteBest(sq, f) {
  // every legal XI, the hard way
  let best = -1;
  const ids = sq.map(p => p.id), n = ids.length;
  for (let mask = 0; mask < (1 << n); mask++) {
    let c = 0; for (let i = 0; i < n; i++) if (mask & (1 << i)) c++;
    if (c !== 11) continue;
    const xi = ids.filter((_, i) => mask & (1 << i));
    if (validateXI(xi, sq).length) continue;
    best = Math.max(best, xi.reduce((t, id) => t + f(id), 0));
  }
  return best;
}
test("the XI it picks is legal", () => {
  const sq = makeSquad();
  assert.deepEqual(validateXI(bestXI(sq, xpOf), sq), []);
});
test("and has the most expected points of any legal XI (checked against every XI)", () => {
  const sq = makeSquad();
  let seed = 7;
  const rnd = () => (seed = (seed * 16807) % 2147483647) / 2147483647;
  for (let k = 0; k < 25; k++) {
    const r = {}; sq.forEach(p => r[p.id] = Math.round(rnd() * 90) / 10);
    const f = id => r[id];
    const got = bestXI(sq, f).reduce((t, id) => t + f(id), 0);
    assert.ok(Math.abs(got - bruteBest(sq, f)) < 1e-9, `trial ${k}: ${got} vs ${bruteBest(sq, f)}`);
  }
});
test("a player the manager benches stays out", () => {
  const sq = makeSquad();
  assert.ok(!bestXI(sq, xpOf, { bench: [12] }).includes(12));
});
test("a player the manager starts goes in, even on few points", () => {
  const sq = makeSquad();
  assert.ok(bestXI(sq, xpOf, { start: [6] }).includes(6));
});
test("benching both keepers still leaves one in goal", () => {
  const sq = makeSquad();
  const xi = bestXI(sq, xpOf, { bench: [1, 2] });
  assert.deepEqual(validateXI(xi, sq), []);
});

section("Running a plan forward");
const sq0 = makeSquad();
const pool = new Map(sq0.map(p => [p.id, p]));
[[60, 3, 9, 60], [61, 3, 10, 55], [62, 2, 11, 45], [63, 4, 12, 80]]
  .forEach(([id, pos, team, buy]) => pool.set(id, { id, name: `n${id}`, pos, team, buy }));
const player = id => pool.get(id);
const flat = id => (id >= 60 ? 6 : 2);            // everyone 2, the new players 6
const base = { squad: sq0, bank: 50, ft: 1 };
const run = (weeks, gws = [5, 6, 7], opts = {}) =>
  simulate(base, weeks, gws, { xp: (id, gw) => (opts.xp || flat)(id, gw), player, ...opts });

test("with no plan, every week is the same squad and one more free transfer", () => {
  const r = run({});
  assert.deepEqual(r.map(w => w.ft), [1, 2, 3]);
  assert.ok(r.every(w => w.made === 0 && w.cost === 0 && w.problems.length === 0));
});
test("expected points are the XI plus the captain once more", () => {
  const r = run({});
  assert.equal(r[0].points.xi, 22);
  assert.equal(r[0].points.captain, 2);
  assert.equal(r[0].points.total, 24);
});
test("a transfer changes the squad from that week on, and the money", () => {
  const r = run({ 6: { transfers: [{ out: sq0[7].id, in: 60 }] } });
  assert.ok(!r[0].state.squad.some(p => p.id === 60));
  assert.ok(r[1].state.squad.some(p => p.id === 60) && r[2].state.squad.some(p => p.id === 60));
  assert.equal(r[1].bank, 50 + 50 - 60);
  assert.equal(r[2].bank, r[1].bank);
});
test("the signing plays and is captain when he is the best bet", () => {
  const r = run({ 6: { transfers: [{ out: sq0[7].id, in: 60 }] } });
  assert.ok(r[1].xi.includes(60));
  assert.equal(r[1].captain, 60);
  assert.equal(r[1].points.total, 20 + 6 + 6);
});
test("a banked transfer is spent, the rest roll on", () => {
  const r = run({ 6: { transfers: [{ out: sq0[7].id, in: 60 }] } });
  assert.deepEqual(r.map(w => w.ft), [1, 2, 2]);
  assert.equal(r[1].cost, 0);
});
test("a transfer beyond the free ones costs four", () => {
  const r = run({ 5: { transfers: [{ out: sq0[7].id, in: 60 }, { out: sq0[8].id, in: 61 }] } });
  assert.equal(r[0].hits, 1);
  assert.equal(r[0].points.hits, -4);
  assert.equal(r[1].ft, 1);
});
test("a free hit squad lasts one week, and so does its money", () => {
  const r = run({ 6: { chip: "freehit", transfers: [{ out: sq0[7].id, in: 60 }, { out: sq0[8].id, in: 61 }] } });
  assert.equal(r[1].cost, 0);
  assert.ok(r[1].state.squad.some(p => p.id === 61));
  assert.ok(!r[2].state.squad.some(p => p.id === 61));
  assert.equal(r[2].bank, 50);
  assert.equal(r[2].ft, 3);
});
test("a triple captain counts the armband twice over", () => {
  const r = run({ 5: { chip: "triple" } });
  assert.equal(r[0].points.captain, 4);
});
test("a bench boost counts the bench", () => {
  const r = run({ 5: { chip: "bench" } });
  assert.equal(r[0].points.bench, 8);
  assert.equal(r[0].points.total, 22 + 2 + 8);
});
test("a chip that is already spent is refused and not applied", () => {
  const r = simulate(base, { 5: { chip: "wildcard", transfers: [{ out: sq0[7].id, in: 60 }, { out: sq0[8].id, in: 61 }] } },
    [5], { xp: flat, player, usedChips: [{ chip: "wildcard", gw: 3 }] });
  assert.equal(r[0].chip, null);
  assert.equal(r[0].cost, 4);
  assert.ok(r[0].problems.some(p => p.includes("already used")));
});
test("the same chip twice in one half is refused the second time", () => {
  const r = run({ 5: { chip: "bench" }, 7: { chip: "bench" } });
  assert.equal(r[0].chip, "bench");
  assert.equal(r[2].chip, null);
  assert.ok(r[2].problems.length > 0);
});
test("a chosen captain is kept; a benched one is not", () => {
  const r = run({ 5: { captain: sq0[3].id }, 6: { captain: sq0[3].id, bench: [sq0[3].id] } });
  assert.equal(r[0].captain, sq0[3].id);
  assert.notEqual(r[1].captain, sq0[3].id);
});
test("a captain with no game hands the armband to the vice", () => {
  const cap = sq0[3].id;
  const r = run({ 5: { captain: cap, start: [cap] } }, [5],
    { xp: id => (id === cap ? 0 : 2) });
  assert.equal(r[0].armband, r[0].vice);
  assert.equal(r[0].points.captain, 2);
});
test("selling a player in a later week who was sold earlier is reported", () => {
  const r = run({ 5: { transfers: [{ out: sq0[7].id, in: 60 }] }, 6: { transfers: [{ out: sq0[7].id, in: 61 }] } });
  assert.ok(r[1].problems.some(p => p.includes("not in the squad")));
  assert.equal(r[1].cost, 0);
});
test("going over budget is reported in the week it happens", () => {
  const r = simulate({ ...base, bank: 0 }, { 5: { transfers: [{ out: sq0[3].id, in: 62 }, { out: sq0[7].id, in: 63 }] } },
    [5], { xp: flat, player });
  assert.ok(r[0].problems.some(p => p.includes("Over budget")));
});
test("the plan is not changed by running it", () => {
  const weeks = { 6: { transfers: [{ out: sq0[7].id, in: 60 }], chip: "freehit" } };
  const copy = JSON.stringify(weeks);
  run(weeks);
  assert.equal(JSON.stringify(weeks), copy);
  assert.equal(sq0.length, 15);
});

test("the vice is never the captain", () => {
  const auto = run({}, [5]);
  // name the automatic vice as captain: the vice must then be someone else
  const r = run({ 5: { captain: auto[0].vice } }, [5]);
  assert.equal(r[0].captain, auto[0].vice);
  assert.notEqual(r[0].vice, r[0].captain);
  assert.ok(r[0].xi.includes(r[0].vice));
});
test("a refused chip does not use up its half", () => {
  const r = simulate(base, { 5: { chip: "wildcard" }, 6: { chip: "wildcard" } }, [5, 6],
    { xp: flat, player, rules: { ...DEFAULT_RULES, chipWindows: { ...DEFAULT_RULES.chipWindows, wildcard: [[6, 19], [20, 38]] } } });
  assert.equal(r[0].chip, null);
  assert.equal(r[1].chip, "wildcard");
});
test("the bench is the spare keeper, then the rest best first", () => {
  const f = id => ({ 12: 0.5, 13: 3, 14: 1 })[id] ?? (id <= 2 ? 1 : 5);
  const r = run({}, [5], { xp: f });
  const b = r[0].bench.map(id => pool.get(id));
  assert.equal(b[0].pos, 1);
  const vals = r[0].bench.slice(1).map(f);
  assert.deepEqual(vals, [...vals].sort((a, b) => b - a));
});
test("a wildcard week: any number of transfers, no hits, free transfers kept", () => {
  const r = run({ 6: { chip: "wildcard", transfers: [{ out: sq0[7].id, in: 60 }, { out: sq0[8].id, in: 61 }, { out: sq0[2].id, in: 62 }] } });
  assert.equal(r[1].cost, 0);
  assert.equal(r[1].made, 3);
  assert.equal(r[2].ft, 3);
  assert.ok(r[2].state.squad.some(p => p.id === 62));
});
test("starting from no free transfers, the first one costs four", () => {
  const r = simulate({ ...base, ft: 0 }, { 5: { transfers: [{ out: sq0[7].id, in: 60 }] } }, [5, 6], { xp: flat, player });
  assert.equal(r[0].cost, 4);
  assert.equal(r[1].ft, 1);
});
test("a Free Hit signing cannot be sold the week after -- he has gone", () => {
  const r = run({ 6: { chip: "freehit", transfers: [{ out: sq0[7].id, in: 60 }] }, 7: { transfers: [{ out: 60, in: 61 }] } });
  assert.ok(r[2].problems.some(p => p.includes("not in the squad")));
  assert.equal(r[2].cost, 0);
  assert.ok(r[2].state.squad.some(p => p.id === sq0[7].id));
});

section("Free transfers from a manager's history");
const hist = (rows, chips = []) => ({
  current: rows.map(([event, event_transfers]) => ({ event, event_transfers })),
  chips: chips.map(([name, event]) => ({ name, event }))
});
test("a new team has one", () => {
  assert.equal(ftFromHistory(hist([]), 1), 1);
  assert.equal(ftFromHistory(hist([[1, 0]]), 2), 1);
});
test("unused transfers bank, up to five", () => {
  assert.equal(ftFromHistory(hist([[1, 0], [2, 0], [3, 0]]), 4), 3);
  assert.equal(ftFromHistory(hist([[1, 0], [2, 0], [3, 0], [4, 0], [5, 0], [6, 0], [7, 0], [8, 0]]), 9), 5);
});
test("transfers made are taken off, and a hit leaves one for next week", () => {
  assert.equal(ftFromHistory(hist([[1, 0], [2, 0], [3, 1]]), 4), 2);
  assert.equal(ftFromHistory(hist([[1, 0], [2, 3]]), 3), 1);
});
test("a wildcard or free hit keeps the banked transfers", () => {
  assert.equal(ftFromHistory(hist([[1, 0], [2, 0], [3, 9]], [["wildcard", 3]]), 4), 3);
  assert.equal(ftFromHistory(hist([[1, 0], [2, 0], [3, 9]], [["freehit", 3]]), 4), 3);
});
test("a team that started late counts from its first gameweek", () => {
  assert.equal(ftFromHistory(hist([[4, 0], [5, 0]]), 6), 2);
});
test("only weeks before the one being planned count", () => {
  assert.equal(ftFromHistory(hist([[1, 0], [2, 0], [3, 0]]), 3), 2);
});
test("chips are read in this file's names", () => {
  assert.deepEqual(chipsFromHistory(hist([], [["bboost", 3], ["3xc", 5], ["manager", 7]])),
    [{ chip: "bench", gw: 3 }, { chip: "triple", gw: 5 }]);
});

console.log(`\n${passed} passed, ${failed} failed\n`);
process.exit(failed ? 1 : 0);
