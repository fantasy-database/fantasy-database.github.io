/*
 * model.js — the fixture model, in one place.
 *
 * These are the same calculations that have been inline in index.html: the
 * season blend, the fixture assembly, the ease/projection values, the anchors
 * that map a raw value onto a colour, and the colour ramp itself.
 *
 * They live here so the ticker, the planner and the comparison page all rate a
 * fixture identically. planner.test / model.test check this file against the
 * copy still inside index.html and fail if the two ever disagree.
 *
 * No DOM, no fetch. shade() takes its five colours as an argument so the file
 * runs under node for the tests; rampFromCss() is the browser convenience.
 */

/* ---------------------------------------------------------------------------
 * Team strengths
 *
 * Each side's attack and defence is a blend of this season and a prior, with
 * w = n/(n+k). Attack and defence get different k because they are not equally
 * repeatable — defence needs about three times the evidence. Promoted sides use
 * their own k, because they have no top-flight record to regress toward.
 * ------------------------------------------------------------------------- */
export function strengths(rec, matchesPlayed, kA, kD, kp) {
  const b = {}, n = matchesPlayed;
  for (const id in rec) {
    const r = rec[id];
    const wa = n / (n + (r.promoted ? kp : kA));
    const wd = n / (n + (r.promoted ? kp : kD));
    b[id] = [wa * r.a26 + (1 - wa) * r.pa, wd * r.d26 + (1 - wd) * r.pd];
  }
  const ids = Object.keys(b);
  const la = ids.reduce((s, i) => s + b[i][0], 0) / ids.length;
  const ld = ids.reduce((s, i) => s + b[i][1], 0) / ids.length;
  const ATK = {}, DEF = {};
  ids.forEach(i => { ATK[i] = b[i][0] / la; DEF[i] = b[i][1] / ld; });
  const w = x => matchesPlayed / (matchesPlayed + x);
  return { ATK, DEF, base: (la + ld) / 2, wA: w(kA), wD: w(kD), wP: w(kp) };
}

/** Every team's fixtures between two gameweeks, in order. */
export function collect(fixtures, teamIds, from, to) {
  const out = {};
  for (const id of teamIds) out[id] = [];
  for (const [gw, h, a, hd, ad] of fixtures) {
    if (gw < from || gw > to) continue;
    out[h].push({ gw, opp: a, home: true,  fdr: hd });
    out[a].push({ gw, opp: h, home: false, fdr: ad });
  }
  for (const id in out) out[id].sort((x, y) => x.gw - y.gw);
  return out;
}

/* ---------------------------------------------------------------------------
 * Rating one fixture
 *
 * side "atk" is what the team should score; "def" is how likely they are to
 * keep it out. Two modes:
 *   ease — an opponent-quality index, comparable across gameweeks
 *   proj — the actual quantity: expected goals for, or clean sheet percentage
 * ------------------------------------------------------------------------- */
export function rawVal(side, teamId, fixture, S, home, mode, pen = 1, market = null) {
  // Where the bookmakers have priced the round, their number wins: over one or
  // two gameweeks the market sees the team news and rotation the ratings cannot.
  // `market` is { gameweek: { teamId: expected goals } }; a clean sheet is the
  // Poisson complement of what the OPPONENT is expected to score, exactly as
  // below, so both sources stay on one scale.
  if (mode === "proj" && market) {
    const g = market[fixture.gw];
    if (g) {
      const v = side === "atk" ? g[teamId] : g[fixture.opp];
      if (v != null) return side === "atk" ? v : Math.exp(-v) * 100;
    }
  }
  const m = fixture.home ? home : 1 / home;
  if (side === "atk") {
    return mode === "ease"
      ? S.DEF[fixture.opp] * m
      : S.base * S.ATK[teamId] * S.DEF[fixture.opp] * m * pen;
  }
  return mode === "ease"
    ? m / S.ATK[fixture.opp]
    : Math.exp(-(S.base * S.ATK[fixture.opp] * S.DEF[teamId] / m * pen)) * 100;
}

/*
 * Anchors turn a raw value into 0..1 goodness. They are fixed rather than
 * derived from the current window, so a colour means the same thing whether you
 * are looking at three gameweeks or nine.
 */
export const ANCHOR = {
  atk_ease: [0.70, 1.32], atk_proj: [0.85, 2.45],
  def_ease: [0.72, 1.42], def_proj: [8, 50],
};

/** 0 = hardest, 1 = easiest. */
export function good(side, v, mode) {
  const [lo, hi] = ANCHOR[side + "_" + mode];
  return (v - lo) / (hi - lo);
}

/** Clamped, for anything that feeds a colour. */
export const goodClamped = (side, v, mode) => Math.max(0, Math.min(1, good(side, v, mode)));

/* ---------------------------------------------------------------------------
 * Colour bands
 *
 * A projected number is coloured by the band it lands in rather than by where
 * it sits on a ramp. The same xG always draws the same green, and a cell is
 * one of five colours rather than one of a thousand, which is what makes a
 * column readable at a glance.
 *
 * The edges are ours, not borrowed. The only non-arbitrary anchor in this model
 * is the average fixture — `base * pen`, what a league-average attack is
 * expected to score against a league-average defence, currently 1.556 — so
 * every band is defined against that:
 *
 *   dark red    more than 30% below average   xG <1.20
 *   red         10-30% below                     1.20-1.39
 *   grey        within 10% of average            1.40-1.69
 *   green       10-30% above                     1.70-1.99
 *   dark green  more than 30% above              2.00+
 *
 * Two independent methods agree on these: anchoring to the average gives
 * 1.20/1.40/1.70/2.00, and the quintiles of the actual fixture distribution
 * give 1.25/1.45/1.65/1.90. FPL Joe and FPL Focal both turn green at or below
 * 1.60-1.80, which flatters the middle — 1.60 is three percent above average,
 * not a good fixture.
 *
 * There is ONE table, for expected goals, and the clean sheet colours fall out
 * of it. A clean sheet is the Poisson complement of the opponent's expected
 * goals — the same calculation seen from the other end — so a defensive cell
 * is coloured by reading this table backwards. The two sides of a fixture can
 * then never contradict each other, which is what independent tables do: under
 * FPL Joe's, a 2.00 xG attack is dark green while the defence facing that very
 * number is merely red.
 *
 * The ease index is not banded: it is an opponent-quality score rather than a
 * quantity, and the same index means different things to different teams, so
 * it keeps the continuous ramp.
 * ------------------------------------------------------------------------- */
export const BAND = {
  atk_proj: [1.20, 1.40, 1.70, 2.00],
};

/*
 * Which band a raw value falls in: 0 = hardest … 4 = easiest. null = unbanded.
 *
 * It bands the number as PRINTED, not as held. A fixture rating 1.6996 shows
 * as "1.70" and has to be coloured as 1.70, or the grid draws a grey cell next
 * to a green one that reads the same.
 */
export function bandOf(side, v, mode) {
  if (mode !== "proj") return null;
  if (side === "atk") {
    const shown = Math.round(v * 100) / 100;
    let i = 0;
    while (i < BAND.atk_proj.length && shown >= BAND.atk_proj[i]) i++;
    return i;
  }
  if (side === "def") {
    // The clean sheet IS the opponent's expected goals. Invert the Poisson
    // step and read the one table, so the defensive colour is the exact
    // opposite of the attacking colour the same number would draw.
    const shown = Math.max(0.01, Math.min(100, Math.round(v)));
    return 4 - bandOf("atk", -Math.log(shown / 100), "proj");
  }
  return null;
}

/*
 * What to hand shade(): the centre of the band where there are bands, and the
 * position along the ramp where there are not. It takes the RAW value, never a
 * goodness — a goodness has already thrown away the band.
 */
export function colourT(side, v, mode) {
  const b = bandOf(side, v, mode);
  return b == null ? goodClamped(side, v, mode) : b / 4;
}

/**
 * The bands written out for a key, hardest first. The defensive ones are not
 * stored anywhere — they are found by asking bandOf() where it changes its
 * mind, so the key can never drift from the colours.
 */
export function bandLabels(side, mode) {
  if (mode !== "proj") return null;
  const pct = side === "def";
  let edges;
  if (side === "atk") edges = BAND.atk_proj;
  else if (pct) {
    edges = [];
    for (let p = 1; p <= 100; p++)
      if (bandOf("def", p, "proj") !== bandOf("def", p - 1, "proj")) edges.push(p);
  } else return null;
  const step = pct ? 1 : 0.01;
  const n = v => pct ? String(Math.round(v)) : v.toFixed(2);
  const unit = pct ? "%" : "";
  const out = edges.map((e, i) => i === 0
    ? "<" + n(e) + unit
    : n(edges[i - 1]) + "\u2013" + n(e - step) + unit);
  out.push(n(edges[edges.length - 1]) + unit + "+");
  return out;
}

/** How a number should read: "1.84 xG", "42% CS", or a bare index. */
export function formatVal(side, v, mode, withUnit) {
  const n = mode === "ease" ? v.toFixed(2)
          : side === "atk" ? v.toFixed(2)
          : Math.round(v) + "%";
  if (!withUnit || mode === "ease") return n;
  return side === "atk" ? `${n} xG` : `${n} CS`;
}

/* ---------------------------------------------------------------------------
 * Colour
 * ------------------------------------------------------------------------- */
const hex2rgb = h => { h = h.replace("#", ""); return [0, 2, 4].map(i => parseInt(h.slice(i, i + 2), 16)); };
const mix = (a, b, t) => a.map((v, i) => Math.round(v + (b[i] - v) * t));

/** The five stops, hardest first, as [r,g,b] triples. */
export const rampFrom = hexes => hexes.map(hex2rgb);

/** Reads the stops out of the page's CSS custom properties. Browser only. */
export function rampFromCss(el) {
  const cs = getComputedStyle(el || document.documentElement);
  return rampFrom(["--hard", "--hard2", "--midc", "--easy2", "--easy"]
    .map(n => cs.getPropertyValue(n).trim()));
}

/**
 * t: 0 = hardest (dark red) … 1 = easiest (dark green).
 * Returns a background and a foreground that stays readable on it.
 */
export function shade(t, ramp) {
  t = Math.max(0, Math.min(1, t));
  const x = t * 4, i = Math.min(3, Math.floor(x));
  const rgb = mix(ramp[i], ramp[i + 1], x - i);
  const lum = (0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]) / 255;
  return { bg: `rgb(${rgb.join(",")})`, fg: lum > 0.55 ? "#1A1611" : "#FFFFFF" };
}

/* ---------------------------------------------------------------------------
 * Rotating two of anything
 *
 * The rule the ticker uses for two teams and the comparison page uses for two
 * players is the same rule, and it lived in two places until this was written:
 * play whichever of the two has the better week, every week.
 *
 * Both callers build their own array first — the ticker from a team's fixture
 * ratings, the comparison page from a player's, which has to cope with blanks
 * and double gameweeks — and hand in two arrays of the same length. A gameweek
 * a side does not play is `null`, never a sentinel and never a number: a
 * sentinel averaged in put a negative expected-goals figure on screen once, and
 * the null is also what lets a side that actually plays beat a side that does
 * not when both rate 0.
 *
 *   avg  — the mean across the window of whoever you would have started
 *   gain — what rotating adds over simply owning the better of the two
 *   pick — one entry per week: which side to start (0 = a, 1 = b), its value,
 *          and whether neither of them plays
 * ------------------------------------------------------------------------- */
export function rotate(a, b) {
  if (a.length !== b.length) throw new Error("rotate(): the two series differ in length");
  let sum = 0, sa = 0, sb = 0;
  const pick = [];
  for (let i = 0; i < a.length; i++) {
    const blankA = a[i] == null, blankB = b[i] == null;
    const va = blankA ? 0 : a[i], vb = blankB ? 0 : b[i];
    // A side that plays always beats a side that does not, even where the
    // fixture it plays rates 0 — otherwise a blank week wins on a tie and the
    // strip tells you to start nobody.
    const takeA = (blankA !== blankB) ? !blankA : va >= vb;
    sa += va; sb += vb; sum += Math.max(va, vb);
    pick.push({ i, take: takeA ? 0 : 1, v: takeA ? va : vb, blank: blankA && blankB });
  }
  const n = a.length || 1, avg = sum / n;
  return { avg, gain: avg - Math.max(sa / n, sb / n), pick };
}

/* ---------------------------------------------------------------------------
 * Convenience
 * ------------------------------------------------------------------------- */

/** Turn data.json's teams into the {pa,pd,a26,d26,promoted} records strengths() wants. */
export function recordsFrom(teams) {
  const rec = {};
  for (const id in teams) {
    const t = teams[id];
    rec[id] = { pa: t.pa, pd: t.pd, a26: t.a26, d26: t.d26 };
    if (t.promoted) rec[id].promoted = 1;
  }
  return rec;
}

/**
 * market.json is keyed by three-letter short code; everything else here works in
 * FPL team ids. Translates one into the other, and returns null when there is
 * nothing usable — which is the normal state for every gameweek beyond the next
 * round or two, and the state every caller has to handle anyway.
 */
export function marketByTeamId(gwMap, teams) {
  if (!gwMap) return null;
  const idOf = {};
  for (const id in teams) idOf[teams[id].short] = id;
  const out = {};
  for (const gw in gwMap) {
    const row = {};
    for (const short in gwMap[gw]) if (idOf[short]) row[idOf[short]] = gwMap[gw][short];
    if (Object.keys(row).length) out[gw] = row;
  }
  return Object.keys(out).length ? out : null;
}

/**
 * Everything a page needs from one data.json, with the fitted constants as
 * defaults. Pass different k values to override them.
 */
/**
 * The next gameweek you can still transfer into.
 *
 * data.json carries a `nextGw`, but a scheduled build is hours old at best --
 * and on 7 Sep 2026 it sat on GW3 for a full day after GW3 had been played,
 * because FPL had not yet flipped that round's `finished` flag. So take
 * whichever is later, the file or the deadline calendar. It can only ever move
 * the window forward, never back.
 */
export function nextGwFrom(d) {
  const list = (d.deadlineTimes && d.deadlineTimes.length === d.deadlines.length)
    ? d.deadlineTimes : d.deadlines;
  const now = Date.now();
  let cal = 38;
  for (let i = 0; i < list.length; i++) {
    // A date with no time of day cannot tell us the cut-off hour, so the
    // deadline day itself still counts as upcoming.
    const t = list[i].length > 10
      ? Date.parse(list[i]) : Date.parse(list[i] + "T23:59:59Z");
    if (t > now) { cal = i + 1; break; }
  }
  return Math.min(38, Math.max(d.nextGw || 1, cal));
}


export function fromData(d, { kAtk, kDef, kPromoted = 4, home, pen, market = null } = {}) {
  const rec = recordsFrom(d.teams);
  const S = strengths(rec, d.matchesPlayed,
    kAtk ?? d.fit.kAtk, kDef ?? d.fit.kDef, kPromoted);
  // The fitted constants are not a user setting — update.py measures them and
  // writes them into data.json — but the ticker lets a query string override
  // them for testing, so they are overridable here rather than in the page.
  const H = home ?? d.fit.home, P = pen ?? d.fit.pen;
  return {
    S, rec,
    home: H,
    pen: P,
    nextGw: nextGwFrom(d),
    teamIds: Object.keys(d.teams),
    fixtures: (from, to) => collect(d.fixtures, Object.keys(d.teams), from, to),
    market,
    /**
     * True when this fixture's number came from the odds rather than the model.
     * With a side, it asks about the number that side's cell actually shows: an
     * attacking figure is the team's own price, a clean sheet is the opponent's.
     * Without one, whether either end of the fixture is priced.
     */
    priced: (teamId, fixture, side) => {
      if (!market) return false;
      const g = market[fixture.gw];
      if (!g) return false;
      if (side === "atk") return g[teamId] != null;
      if (side === "def") return g[fixture.opp] != null;
      return g[teamId] != null || g[fixture.opp] != null;
    },
    rate: (side, teamId, fixture, mode = "proj") =>
      rawVal(side, teamId, fixture, S, H, mode, P, market),
  };
}
