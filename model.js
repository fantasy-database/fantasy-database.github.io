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
export function rawVal(side, teamId, fixture, S, home, mode, pen = 1) {
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
 * Everything a page needs from one data.json, with the fitted constants as
 * defaults. Pass different k values to override them.
 */
export function fromData(d, { kAtk, kDef, kPromoted = 4 } = {}) {
  const rec = recordsFrom(d.teams);
  const S = strengths(rec, d.matchesPlayed,
    kAtk ?? d.fit.kAtk, kDef ?? d.fit.kDef, kPromoted);
  return {
    S, rec,
    home: d.fit.home,
    pen: d.fit.pen,
    nextGw: d.nextGw,
    teamIds: Object.keys(d.teams),
    fixtures: (from, to) => collect(d.fixtures, Object.keys(d.teams), from, to),
    rate: (side, teamId, fixture, mode = "proj") =>
      rawVal(side, teamId, fixture, S, d.fit.home, mode, d.fit.pen),
  };
}
