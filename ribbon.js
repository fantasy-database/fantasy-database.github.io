/* ---------------------------------------------------------------------------
 * The gameweek ribbon, shared by the fixtures and comparison pages.
 *
 * Dragging two sliders to reach GW9 was the wrong tool for "show me GW9". The
 * ribbon is the whole control:
 *   - click a week: that week alone; click a later one: the run between them
 *     (while one end is set, hovering a later week previews the run)
 *   - press and drag across it: the run you swept
 *   - the arrows slide the window a week either way, keeping its length
 *   - the presets start it at the next gameweek
 *   - left/right arrow keys walk the weeks
 *
 * The page owns the window. It hands in a `change(from,to)` callback and calls
 * `paint(from,to)` whenever it redraws; the ribbon never renders the page
 * itself, so it cannot fall out of step with it.
 *
 * Markup it expects (ids passed in):
 *   pick    an empty element, becomes the row of week buttons
 *   prev    "‹" button     next   "›" button
 *   presets buttons with data-next="N" (first N weeks from the next one)
 *   hint    a <p> for the one-line help text
 * ------------------------------------------------------------------------- */
const MON = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];

export function ribbon({ pick, prev, next, presets = [], hint, deadlines, first, change }) {
  let from = first, to = first, anchor = null, drag = null, justDragged = false;
  const clamp = (a, b) => {
    a = Math.max(first, Math.min(38, a)); b = Math.max(first, Math.min(38, b));
    return a <= b ? [a, b] : [b, a];
  };
  const set = (a, b) => { [a, b] = clamp(a, b); change(a, b); };

  // a month label on the first gameweek of each month, so it reads as a timeline
  let h = "", lastMon = -1;
  for (let g = first; g <= 38; g++) {
    const iso = deadlines[g - 1], m = iso ? +iso.slice(5, 7) - 1 : -1;
    const mon = m !== lastMon && m >= 0 ? MON[m].toUpperCase() : ""; lastMon = m;
    const day = iso ? `, ${+iso.slice(8, 10)} ${MON[m]}` : "";
    h += `<button type="button" class="gwb" data-gw="${g}" aria-label="Gameweek ${g}${day}">`
       + `<s aria-hidden="true">${mon}</s><b>${g}</b></button>`;
  }
  pick.innerHTML = h;

  function paint(a, b, preview) {
    if (a !== undefined) { from = a; to = b; }
    for (const el of pick.children) {
      const g = +el.dataset.gw, inR = g >= from && g <= to;
      el.dataset.in = inR ? "1" : "0";
      el.dataset.start = g === from ? "1" : "0";
      el.dataset.end = g === to ? "1" : "0";
      el.dataset.edge = (g === from || g === to) ? "1" : "0";
      el.dataset.anchor = (anchor !== null && g === anchor) ? "1" : "0";
      el.dataset.preview = (preview && !inR && g >= preview[0] && g <= preview[1]) ? "1" : "0";
      el.setAttribute("aria-pressed", inR ? "true" : "false");
    }
    if (hint) hint.textContent = anchor !== null
      ? `GW${anchor} picked — click a later gameweek to take the window up to it.`
      : "Click a gameweek, or drag across the ribbon to pick a run. The arrows slide the window a week at a time.";
    if (prev) prev.disabled = from <= first;
    if (next) next.disabled = to >= 38;
    for (const b of presets) {
      const on = from === first && to === Math.min(38, first + (+b.dataset.next) - 1);
      b.setAttribute("aria-pressed", on ? "true" : "false");
    }
  }

  function reveal() {
    const a = pick.querySelector(`[data-gw="${from}"]`), z = pick.querySelector(`[data-gw="${to}"]`);
    if (!a || !z) return;
    const l = a.offsetLeft - pick.offsetLeft, r = z.offsetLeft - pick.offsetLeft + z.offsetWidth;
    const s = pick.scrollLeft, cw = pick.clientWidth;
    if (l < s + 4) pick.scrollLeft = Math.max(0, l - 34);
    else if (r > s + cw - 4) pick.scrollLeft = Math.min(l - 34, r - cw + 34);
  }

  const gwAt = (x, y) => {
    const el = document.elementFromPoint(x, y);
    const b = el && el.closest && el.closest(".gwb");
    return b && pick.contains(b) ? +b.dataset.gw : null;
  };
  pick.addEventListener("pointerdown", e => {
    if (e.button !== 0) return;
    const g = gwAt(e.clientX, e.clientY); if (g == null) return;
    drag = { start: g, moved: false };
  });
  pick.addEventListener("pointermove", e => {
    const g = gwAt(e.clientX, e.clientY);
    if (drag && (e.buttons & 1)) {
      if (g == null || (g === drag.start && !drag.moved)) return;
      drag.moved = true; anchor = null;
      set(drag.start, g);
      return;
    }
    // with one end picked, show where the run would go
    if (anchor !== null && g != null && g > anchor) paint(undefined, undefined, [anchor, g]);
    else if (anchor !== null) paint();
  });
  addEventListener("pointerup", () => {
    if (drag && drag.moved) { justDragged = true; setTimeout(() => { justDragged = false; }, 0); }
    drag = null;
  });
  pick.addEventListener("pointercancel", () => { drag = null; });
  pick.addEventListener("pointerleave", () => { if (anchor !== null && !drag) paint(); });
  pick.addEventListener("click", e => {
    if (justDragged) return;
    const b = e.target.closest(".gwb"); if (!b) return;
    const g = +b.dataset.gw;
    if (anchor === null || g <= anchor) { anchor = g; set(g, g); }   // start fresh on a single week
    else { const a = anchor; anchor = null; set(a, g); }             // close the range
    reveal();
  });
  pick.addEventListener("keydown", e => {
    const b = e.target.closest(".gwb"); if (!b) return;
    const t = e.key === "ArrowRight" ? b.nextElementSibling : e.key === "ArrowLeft" ? b.previousElementSibling : null;
    if (t) { e.preventDefault(); t.focus(); }
  });
  const nudge = d => {
    const n = to - from, a = Math.max(first, Math.min(38 - n, from + d));
    anchor = null; set(a, a + n); reveal();
  };
  if (prev) prev.addEventListener("click", () => nudge(-1));
  if (next) next.addEventListener("click", () => nudge(1));
  for (const b of presets) b.addEventListener("click", () => {
    anchor = null; set(first, first + (+b.dataset.next) - 1); reveal();
  });

  return { paint, reveal, set: (a, b) => { anchor = null; set(a, b); } };
}
