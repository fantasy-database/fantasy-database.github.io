/*
 * planner.js — the squad rules engine for Fantasy DataBase.
 *
 * Pure logic. No DOM, no fetch, no projections. It knows what a legal FPL squad
 * is, what a transfer costs, and how bank and free transfers carry from one
 * gameweek to the next. Everything else in the planner sits on top of this.
 *
 * Loads as a module in the browser (<script type="module">) and runs under node
 * for the tests. No dependencies, no build step — same as the rest of the site.
 *
 * Position ids follow FPL and players.json's `p`: 1 GK, 2 DEF, 3 MID, 4 FWD.
 * All money is in tenths of a million, as the API gives it: 1000 = £100.0m.
 */

/* ---------------------------------------------------------------------------
 * Rules
 *
 * These come from bootstrap-static's game_settings — update.py already fetches
 * that file, so it should write them into data.json rather than have us hardcode
 * them here. FPL has changed the chip and free-transfer rules more than once,
 * and a planner that hardcodes them is wrong the season they change again.
 * The defaults below are 2026/27, read from the API on 3 September 2026.
 * ------------------------------------------------------------------------- */

export const DEFAULT_RULES = {
  budget: 1000,
  squad: { 1: 2, 2: 5, 3: 5, 4: 3 },              // exact count per position
  xi: { 1: [1, 1], 2: [3, 5], 3: [2, 5], 4: [1, 3] }, // [min, max] in the XI
  xiSize: 11,
  maxPerClub: 3,
  baseFreeTransfers: 1,
  maxBankedFreeTransfers: 5,   // 1 per week plus 4 banked
  hitCost: 4,
  sellOnFee: 0.5,
  chipWindows: {               // [firstGw, lastGw] for each use, in order
    wildcard: [[2, 19], [20, 38]],
    freehit:  [[2, 19], [20, 38]],
    bench:    [[1, 19], [20, 38]],
    triple:   [[1, 19], [20, 38]]
  }
};

export const POS = { GK: 1, DEF: 2, MID: 3, FWD: 4 };
const POS_NAME = { 1: "GK", 2: "DEF", 3: "MID", 4: "FWD" };

/* ---------------------------------------------------------------------------
 * Money
 * ------------------------------------------------------------------------- */

/**
 * What a player sells for. FPL gives you back what you paid plus half of any
 * rise since, rounded down to the nearest 0.1. A fall is taken in full.
 *
 * This is why a draft has to store each player's *purchase* price and not just
 * today's price: two managers holding the same player have different money.
 */
export function sellPrice(buy, now) {
  if (now <= buy) return now;
  return buy + Math.floor((now - buy) / 2);
}

/** Selling value of a whole squad — what you would get if you sold everyone. */
export function squadValue(squad, prices) {
  return squad.reduce((t, p) => t + sellPrice(p.buy, priceOf(p, prices)), 0);
}

function priceOf(p, prices) {
  const now = prices && prices[p.id];
  return now === undefined || now === null ? p.buy : now;
}

/* ---------------------------------------------------------------------------
 * Validation
 *
 * Every check returns a list of plain-English problems rather than throwing, so
 * the interface can show all of them at once and grey out the offending cells.
 * A planner that reports one error at a time is miserable to use.
 * ------------------------------------------------------------------------- */

export function validateSquad(squad, { rules = DEFAULT_RULES, prices = null, bank = 0,
                                       teamName = id => `team ${id}` } = {}) {
  const errors = [];

  const want = rules.squad;
  const total = Object.values(want).reduce((a, b) => a + b, 0);
  if (squad.length !== total) {
    errors.push(`Squad has ${squad.length} players, needs ${total}.`);
  }

  for (const pos of Object.keys(want)) {
    const n = squad.filter(p => p.pos === +pos).length;
    if (n !== want[pos]) {
      errors.push(`${POS_NAME[pos]}: ${n} of ${want[pos]}.`);
    }
  }

  const seen = new Set();
  for (const p of squad) {
    if (seen.has(p.id)) errors.push(`${label(p)} is in the squad twice.`);
    seen.add(p.id);
  }

  const byClub = new Map();
  for (const p of squad) byClub.set(p.team, (byClub.get(p.team) || 0) + 1);
  for (const [team, n] of byClub) {
    if (n > rules.maxPerClub) {
      errors.push(`${n} players from ${teamName(team)} — the limit is ${rules.maxPerClub}.`);
    }
  }

  // Bank is tracked as state rather than derived, because the money you have
  // depends on what you paid, not on what the squad is worth today.
  if (bank < 0) {
    errors.push(`Over budget by £${(-bank / 10).toFixed(1)}m.`);
  }

  return errors;
}

export function validateXI(xi, squad, { rules = DEFAULT_RULES } = {}) {
  const errors = [];
  const byId = new Map(squad.map(p => [p.id, p]));

  if (xi.length !== rules.xiSize) {
    errors.push(`Starting XI has ${xi.length} players, needs ${rules.xiSize}.`);
  }

  const missing = xi.filter(id => !byId.has(id));
  if (missing.length) errors.push(`Picked ${missing.length} player(s) not in the squad.`);

  for (const pos of Object.keys(rules.xi)) {
    const [min, max] = rules.xi[pos];
    const n = xi.filter(id => byId.get(id)?.pos === +pos).length;
    if (n < min) errors.push(`Only ${n} ${POS_NAME[pos]} — need at least ${min}.`);
    if (n > max) errors.push(`${n} ${POS_NAME[pos]} — the most you can play is ${max}.`);
  }

  return errors;
}

/** Every formation the rules allow, as "3-5-2" strings. Useful for a picker. */
export function legalFormations(rules = DEFAULT_RULES) {
  const out = [];
  const [dMin, dMax] = rules.xi[POS.DEF];
  const [mMin, mMax] = rules.xi[POS.MID];
  const [fMin, fMax] = rules.xi[POS.FWD];
  for (let d = dMin; d <= dMax; d++)
    for (let m = mMin; m <= mMax; m++)
      for (let f = fMin; f <= fMax; f++)
        if (1 + d + m + f === rules.xiSize) out.push(`${d}-${m}-${f}`);
  return out;
}

function label(p) {
  return p.name || `player ${p.id}`;
}

/* ---------------------------------------------------------------------------
 * Transfers
 * ------------------------------------------------------------------------- */

/**
 * Apply a set of transfers to a gameweek's state.
 *
 * Returns a NEW state — nothing is mutated, so a draft can be forked and
 * compared without the two copies bleeding into each other.
 *
 * `transfers` is [{ out: playerId, in: playerObject }]. The incoming player is
 * bought at today's price, which becomes their purchase price from then on.
 */
export function applyTransfers(state, transfers, { rules = DEFAULT_RULES, prices = null,
                                                   teamName = undefined } = {}) {
  const squad = state.squad.map(p => ({ ...p }));
  const byId = new Map(squad.map(p => [p.id, p]));
  let bank = state.bank;
  const problems = [];

  // On a free hit, remember what the squad was before we touched it, so
  // advance() can put it back next week.
  const before = state.chip === "freehit"
    ? {
        squadBefore: state.squadBefore ?? state.squad.map(p => ({ ...p })),
        bankBefore:  state.bankBefore  ?? state.bank,
        xiBefore:    state.xiBefore    ?? state.xi
      }
    : {};

  const applied = [];
  for (const t of transfers) {
    const out = byId.get(t.out);
    if (!out) { problems.push(`Cannot sell a player who is not in the squad (${t.out}).`); continue; }
    if (byId.has(t.in.id)) { problems.push(`${label(t.in)} is already in the squad.`); continue; }

    const buyAt = (prices && prices[t.in.id]) ?? t.in.buy ?? t.in.cost;
    if (buyAt === undefined) { problems.push(`No price for ${label(t.in)}.`); continue; }

    bank += sellPrice(out.buy, priceOf(out, prices));
    bank -= buyAt;

    const i = squad.findIndex(p => p.id === t.out);
    squad[i] = { ...t.in, buy: buyAt };
    byId.delete(t.out);
    byId.set(t.in.id, squad[i]);
    applied.push(t);
  }

  // A wildcard or free hit makes the week's transfers free and unlimited, and
  // leaves the free-transfer bank untouched for the following week.
  // Only the transfers that actually went through are charged for: one that
  // was refused above is reported, not billed.
  const free = state.chip === "wildcard" || state.chip === "freehit";
  const made = applied.length;
  const paidFor = free ? 0 : Math.max(0, made - state.ft);
  const ftLeft = free ? state.ft : Math.max(0, state.ft - made);

  return {
    ...state,
    ...before,
    squad,
    bank,
    transfers: applied,
    ftLeft,
    hits: paidFor,
    cost: paidFor * rules.hitCost,
    problems: problems.concat(validateSquad(squad, { rules, prices, bank, teamName }))
  };
}

/**
 * Move to the next gameweek: bank a free transfer, and undo a free hit.
 *
 * The free-hit revert is the subtle one. The squad only exists for that single
 * week, so the following week starts from whatever you had before it — a
 * planner that carries the free-hit squad forward silently invents a team the
 * manager never owned.
 */
export function advance(state, { rules = DEFAULT_RULES } = {}) {
  const reverted = state.chip === "freehit" && state.squadBefore
    ? state.squadBefore.map(p => ({ ...p }))
    : state.squad.map(p => ({ ...p }));

  const bank = state.chip === "freehit" && state.bankBefore !== undefined
    ? state.bankBefore
    : state.bank;

  const ft = Math.min(
    (state.ftLeft ?? state.ft) + rules.baseFreeTransfers,
    rules.maxBankedFreeTransfers
  );

  return {
    gw: state.gw + 1,
    squad: reverted,
    bank,
    ft,
    xi: state.chip === "freehit" ? (state.xiBefore ?? state.xi) : state.xi,
    captain: state.captain,
    vice: state.vice,
    chip: null,
    transfers: [],
    hits: 0,
    cost: 0
  };
}

/* ---------------------------------------------------------------------------
 * Chips
 * ------------------------------------------------------------------------- */

/**
 * Can this chip be played in this gameweek, given the ones already spent?
 *
 * There are two of each now, one per half of the season, so "have I used my
 * wildcard" is no longer a yes/no question — it depends which half you are in.
 */
export function chipAvailable(chip, gw, usedChips = [], rules = DEFAULT_RULES) {
  const windows = rules.chipWindows[chip];
  if (!windows) return { ok: false, why: `Unknown chip "${chip}".` };

  const window = windows.find(([a, b]) => gw >= a && gw <= b);
  if (!window) return { ok: false, why: `${chipName(chip)} cannot be played in GW${gw}.` };

  const spent = usedChips.some(u => u.chip === chip && u.gw >= window[0] && u.gw <= window[1]);
  if (spent) {
    return { ok: false, why: `${chipName(chip)} is already used in GW${window[0]}–${window[1]}.` };
  }

  const another = usedChips.find(u => u.gw === gw);
  if (another) return { ok: false, why: `${chipName(another.chip)} is already played in GW${gw}.` };

  return { ok: true };
}

function chipName(c) {
  return { wildcard: "Wildcard", freehit: "Free Hit", bench: "Bench Boost", triple: "Triple Captain" }[c] || c;
}

/* ---------------------------------------------------------------------------
 * Whole plans
 * ------------------------------------------------------------------------- */

/** Total points spent on hits across a run of gameweeks. */
export function planCost(states) {
  return states.reduce((t, s) => t + (s.cost || 0), 0);
}

/** Every problem in a plan, tagged with the gameweek it belongs to. */
export function planProblems(states, opts = {}) {
  const out = [];
  const used = [];
  for (const s of states) {
    for (const p of s.problems || []) out.push({ gw: s.gw, problem: p });
    for (const p of validateXI(s.xi || [], s.squad, opts)) out.push({ gw: s.gw, problem: p });
    if (s.chip) {
      const check = chipAvailable(s.chip, s.gw, used, opts.rules || DEFAULT_RULES);
      if (!check.ok) out.push({ gw: s.gw, problem: check.why });
      used.push({ chip: s.chip, gw: s.gw });
    }
  }
  return out;
}

/** A fresh, empty gameweek to start a draft from. */
export function newState(gw, squad, { bank = 0, ft = 1 } = {}) {
  return {
    gw, squad: squad.map(p => ({ ...p })), bank, ft,
    xi: [], captain: null, vice: null, chip: null,
    transfers: [], hits: 0, cost: 0
  };
}

/* ---------------------------------------------------------------------------
 * Planning ahead with expected points
 *
 * Everything below takes a points projection as a plain function,
 * xp(playerId, gw) -> number, so this file still knows nothing about where the
 * numbers come from. The page passes in players.json's "xp" arrays.
 * ------------------------------------------------------------------------- */

/**
 * The starting eleven with the most expected points.
 *
 * Fill each position's minimum with its best players, then take the best of
 * whoever is left. With FPL's shape (2/5/5/3 squad; 1 keeper, 3+ defenders,
 * 2+ midfielders, 1+ forward) that greedy choice is also the optimum: the only
 * maximum that can bind is the one keeper, and that is also his minimum.
 *
 * `start` and `bench` are the manager's own calls for the week. They are
 * obeyed ahead of the numbers wherever the formation allows it.
 */
export function bestXI(squad, xpOf, { start = [], bench = [], rules = DEFAULT_RULES } = {}) {
  const score = p => (xpOf(p.id) || 0)
    + (start.includes(p.id) ? 1000 : 0) - (bench.includes(p.id) ? 1000 : 0);
  const order = [...squad].sort((a, b) => score(b) - score(a) || a.id - b.id);
  const chosen = [];
  const count = {};
  for (const pos of Object.keys(rules.xi)) count[pos] = 0;
  for (const pos of Object.keys(rules.xi)) {
    for (const p of order) {
      if (p.pos === +pos && count[pos] < rules.xi[pos][0] && !chosen.includes(p)) {
        chosen.push(p); count[pos]++;
      }
    }
  }
  for (const p of order) {
    if (chosen.length >= rules.xiSize) break;
    if (chosen.includes(p) || count[p.pos] >= rules.xi[p.pos][1]) continue;
    chosen.push(p); count[p.pos]++;
  }
  return chosen.map(p => p.id);
}

/**
 * Run a plan forward, one gameweek at a time.
 *
 *   base   { squad: [rules player], bank, ft }  -- the team before the first deadline
 *   weeks  { [gw]: { transfers: [{out: id, in: id}], chip, start: [ids], bench: [ids],
 *                    captain: id } }            -- what the manager plans to do
 *   gws    the gameweeks to run, in order
 *
 * Each week starts from the one before (advance(): a free transfer banked, a
 * free hit undone), takes that week's chip and transfers, then picks the XI
 * and captain by expected points unless the manager has said otherwise.
 *
 * Expected points for the week: the XI, plus the captain once more (twice on
 * a triple captain), plus the bench on a bench boost, less any hits. If the
 * captain is expected nothing -- no fixture -- the armband passes to the vice,
 * as it does in the game. Automatic substitutions are otherwise not modelled;
 * a player's xP already allows for the chance that he does not play.
 */
export function simulate(base, weeks, gws, { xp, player, prices = null, rules = DEFAULT_RULES,
                                              usedChips = [], teamName = undefined } = {}) {
  const out = [];
  const used = usedChips.slice();
  let state = null;
  for (const gw of gws) {
    const w = (weeks && weeks[gw]) || {};
    state = state ? advance(state, { rules })
                  : newState(gw, base.squad, { bank: base.bank, ft: base.ft });
    state.gw = gw;
    const problems = [];

    // A chip that cannot be played this week is reported and not applied, so
    // an illegal wildcard cannot quietly make a week's transfers free.
    let chip = w.chip || null;
    if (chip) {
      const c = chipAvailable(chip, gw, used, rules);
      if (c.ok) used.push({ chip, gw });
      else { problems.push(c.why); chip = null; }
    }
    state.chip = chip;

    const transfers = [];
    for (const t of w.transfers || []) {
      const p = player(t.in);
      if (p) transfers.push({ out: t.out, in: p });
      else problems.push(`A planned signing (${t.in}) is no longer in the game.`);
    }
    state = applyTransfers(state, transfers, { rules, prices, teamName });

    const xpOf = id => { const v = xp(id, gw); return typeof v === "number" && isFinite(v) ? v : 0; };
    const xi = bestXI(state.squad, xpOf, { start: w.start || [], bench: w.bench || [], rules });
    const byXp = ids => [...ids].sort((a, b) => xpOf(b) - xpOf(a) || a - b);
    const ranked = byXp(xi);
    const captain = xi.includes(w.captain) ? w.captain : ranked[0] ?? null;
    const vice = ranked.find(id => id !== captain) ?? null;
    const armband = captain !== null && xpOf(captain) > 0 ? captain : vice;

    // Bench order as FPL shows it: the spare keeper first, then outfielders by xP.
    const benchIds = state.squad.filter(p => !xi.includes(p.id));
    const bench = [...benchIds.filter(p => p.pos === 1), ...benchIds.filter(p => p.pos !== 1)
      .sort((a, b) => xpOf(b.id) - xpOf(a.id) || a.id - b.id)].map(p => p.id);

    const xiPts = xi.reduce((t, id) => t + xpOf(id), 0);
    const capPts = armband === null ? 0 : xpOf(armband) * (chip === "triple" ? 2 : 1);
    const benchPts = chip === "bench" ? bench.reduce((t, id) => t + xpOf(id), 0) : 0;

    state = { ...state, xi, captain, vice };
    out.push({
      gw, state, chip, xi, bench, captain, vice, armband,
      ft: state.ft, ftLeft: state.ftLeft, made: state.transfers.length,
      hits: state.hits, cost: state.cost, bank: state.bank,
      points: { xi: xiPts, captain: capPts, bench: benchPts, hits: -state.cost,
                total: xiPts + capPts + benchPts - state.cost },
      problems: problems.concat(state.problems)
    });
  }
  return out;
}

/* ---------------------------------------------------------------------------
 * Reading a manager's own history (entry/{id}/history/ in the FPL API)
 * ------------------------------------------------------------------------- */

const API_CHIP = { wildcard: "wildcard", freehit: "freehit", bboost: "bench", "3xc": "triple" };

/** The chips already played, in this file's names. Anything unknown is left out. */
export function chipsFromHistory(history) {
  return ((history && history.chips) || [])
    .filter(c => API_CHIP[c.name])
    .map(c => ({ chip: API_CHIP[c.name], gw: c.event }));
}

/**
 * Free transfers available for `nextGw`, worked out from the transfers made
 * each week. The public API does not publish the number itself, so this is an
 * estimate: it follows the standing rules (one a week, five at most, kept
 * through a wildcard or free hit) and cannot know about one-off top-ups FPL
 * sometimes hands out. The page lets the manager correct it.
 */
export function ftFromHistory(history, nextGw, rules = DEFAULT_RULES) {
  const rows = ((history && history.current) || [])
    .filter(r => r.event < nextGw).sort((a, b) => a.event - b.event);
  const chipAt = new Map(chipsFromHistory(history).map(c => [c.gw, c.chip]));
  let ft = null;
  for (const r of rows) {
    if (ft === null) { ft = rules.baseFreeTransfers; continue; }   // the first squad is free
    const chip = chipAt.get(r.event);
    const left = chip === "wildcard" || chip === "freehit"
      ? ft : Math.max(0, ft - (r.event_transfers || 0));
    ft = Math.min(left + rules.baseFreeTransfers, rules.maxBankedFreeTransfers);
  }
  return ft ?? rules.baseFreeTransfers;
}

/**
 * What the manager paid for each player in his squad, from
 * entry/{id}/transfers/ -- every transfer, wildcards and free hits included,
 * each with element_in_cost.
 *
 * A player's price is the cost of the last time he was bought, up to the
 * gameweek the squad was read from. Free Hit weeks are left out: that squad
 * reverts, so nothing bought in one is still owned because of it. Anyone with
 * no transfer at all has been there since the team's first gameweek; he comes
 * back in `missing`, and his price is his price that week (the page reads it
 * from element-summary).
 */
export function purchasePrices(squadIds, transfers, { upTo = Infinity, freeHitWeeks = [] } = {}) {
  const skip = new Set(freeHitWeeks);
  const last = {};
  (transfers || [])
    .filter(t => t.event <= upTo && !skip.has(t.event))
    .sort((a, b) => (a.time < b.time ? -1 : a.time > b.time ? 1 : 0))
    .forEach(t => { last[t.element_in] = t.element_in_cost; });
  const prices = {}, missing = [];
  for (const id of squadIds) {
    if (typeof last[id] === "number") prices[id] = last[id];
    else missing.push(id);
  }
  return { prices, missing };
}

/** A player's price in gameweek `gw`, from element-summary's history (the first game on or after it). */
export function priceInWeek(summary, gw) {
  const rows = ((summary && summary.history) || []).filter(r => r.round >= gw)
    .sort((a, b) => a.round - b.round);
  return rows.length ? rows[0].value : null;
}
