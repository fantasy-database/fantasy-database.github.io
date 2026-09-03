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
export function applyTransfers(state, transfers, { rules = DEFAULT_RULES, prices = null } = {}) {
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
  }

  // A wildcard or free hit makes the week's transfers free and unlimited, and
  // leaves the free-transfer bank untouched for the following week.
  const free = state.chip === "wildcard" || state.chip === "freehit";
  const made = transfers.length;
  const paidFor = free ? 0 : Math.max(0, made - state.ft);
  const ftLeft = free ? state.ft : Math.max(0, state.ft - made);

  return {
    ...state,
    ...before,
    squad,
    bank,
    transfers,
    ftLeft,
    hits: paidFor,
    cost: paidFor * rules.hitCost,
    problems: problems.concat(validateSquad(squad, { rules, prices, bank }))
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
