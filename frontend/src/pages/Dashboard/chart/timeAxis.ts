/**
 * Wave-3 LANE B: TF-aware time axis ticks.
 *
 * The pre-scaffold painter drew 4 fixed labels straight off shown-bar
 * timestamps — colliding on dense timeframes and duplicating text across a
 * date boundary. This module computes a denser, non-colliding, timeframe-
 * aware tick set over the VISIBLE window [startISO..endISO] only:
 *
 *   FORMAT (from the window's median bar spacing — the TF fingerprint):
 *     M1/M5/M10 -> "HH:MM"      H1/H4 -> "MM-DD HH:MM"
 *     D1/W1     -> "MM-DD"      (+ " 'YY" once the year changes, so labels
 *                                stay unique and readable across years —
 *                                required by the dedupe rule for W1, and it
 *                                keeps D1 windows that cross New Year sane)
 *   One deliberate widening: a minute-class window longer than 24h cannot be
 *   BOTH dense and deduped as bare "HH:MM" (any nice step is <= 12h and
 *   divides the day, so <= 8 distinct clock strings exist while span/step >
 *   8 — dedupe then starves the right side of the axis). Such windows
 *   escalate to "MM-DD HH:MM", the same date-first behaviour TradingView
 *   uses for multi-day intraday ranges. The named TFs at normal zoom (M1,
 *   M5, M10 <= 24h windows) stay bare "HH:MM".
 *
 *   DENSITY: <= maxLabels ticks, each >= MIN_GAP_PX (58px) from the
 *   previous one, anchored on round grid times (minute/hour/day/month
 *   boundaries), deduped by label text, and never past the price-axis
 *   reserve: slot*slotW <= plotW - 58 uses exactly the threshold of the
 *   painter's clamp `min(max(PAD_LEFT, x(slot)), w - AXIS_W - 58)`
 *   (chartPainter.ts L475) — a tick that would cross it is DROPPED rather
 *   than clamped onto the edge, which is what keeps the >=58px guarantee
 *   exact instead of letting the clamp squeeze the last pair together.
 *
 * GEOMETRY: the painter passes bw (= plotW / view.visible — one slot per
 * bar on a full window) plus the shown bars' endpoints. Plot width is not
 * in the signature, so it is measured from the mounted .mc-stage (the same
 * box the painter's ResizeObserver reports as size.w); node/tests fall back
 * to FALLBACK_PLOT_W. Then slots = plotW/slotW and median spacing =
 * span/(slots-1) — exact whenever bars fill the window (the live/default
 * view). Two seam limits, documented rather than papered over: a window
 * panned into future space (or with fewer bars than slots) reads coarse
 * spacing — labels stay honest times but can sit right of their bars; and
 * slots are TIME-linear while candles are INDEX-linear, so an interior gap
 * (weekend) shifts interior labels by up to that gap — the seam sees only
 * two endpoints and cannot see interior bar structure.
 *
 * lane-09: presentation only — it labels times the backend already served.
 * No derived indicator math, no bar synthesis.
 */

/** Seconds per known timeframe bucket — the TF fingerprint table. */
const TF_SECONDS: Record<string, number> = {
  M1: 60,
  M2: 120,
  M3: 180,
  M5: 300,
  M6: 360,
  M10: 600,
  M12: 720,
  M15: 900,
  M20: 1200,
  M30: 1800,
  H1: 3600,
  H2: 7200,
  H3: 10800,
  H4: 14400,
  H6: 21600,
  H8: 28800,
  H12: 43200,
  D1: 86400,
  W1: 604800,
  MN: 2592000,
};

/** Nice label-grid steps in seconds. All divide or mirror a day around the
 *  epoch (a UTC midnight), so day-scale grids land on 00:00 and intraday
 *  grids on round clock times (the 18h/36h rungs cycle 00/06/12/18 / 00/12). */
const STEP_SECONDS: readonly number[] = [
  60, 120, 300, 600, 900, 1800, // 1m..30m
  3600, 7200, 10800, 14400, 21600, 28800, 43200, 64800, // 1h..18h
  86400, 129600, 172800, 259200, 604800, 1209600, // 1d..14d
];

/** Month-scale grid steps (calendar months, anchored at UTC month starts). */
const STEP_MONTHS: readonly number[] = [1, 2, 3, 6, 12, 18, 24, 36, 48];
const MONTH_SECONDS = 30.4375 * 86400; // mean month, for ladder comparisons
const DAY_SECONDS = 86400;

/** Minimum px between adjacent labels — the painter's price-axis reserve
 *  (`w - AXIS_W - 58`) reused as the label-vs-label spacing floor. */
const MIN_GAP_PX = 58;
/** Canvas px the painter reserves for the axis: AXIS_W 60 + PAD_LEFT 6. */
const AXIS_TOTAL_PX = 66;
/** Plot width assumed when there is no DOM (node --test): a typical desktop
 *  dashboard stage. The browser path measures the real .mc-stage instead. */
const FALLBACK_PLOT_W = 800;

export interface TimeTick {
  /** Slot index (window-relative; 0 = first shown bar) the label anchors at. */
  slot: number;
  label: string;
}

/** Canvas plot width (painter: `w - AXIS_W - PAD_LEFT`) — measured from the
 *  mounted stage, canonical fallback when running outside a browser. */
function readPlotWidth(): number {
  if (typeof document === "undefined" || typeof document.querySelector !== "function") {
    return FALLBACK_PLOT_W;
  }
  try {
    const stage = document.querySelector(".mc-stage") as HTMLElement | null;
    const w = stage?.clientWidth ?? 0;
    if (w > AXIS_TOTAL_PX) return w - AXIS_TOTAL_PX;
  } catch {
    /* detached/unusual document — fall through to the canonical width */
  }
  return FALLBACK_PLOT_W;
}

/** Parse a backend bar ISO time to epoch ms. The backend contract is UTC ISO
 *  ("2026-09-23T14:30:00.000Z"); a zone-less string is read as UTC too, so
 *  labels never shift with the viewer's timezone. NaN = must not trust. */
function parseIso(iso: string | null): number {
  if (!iso) return Number.NaN;
  const s = iso.trim();
  const hasZone = /(?:Z|[+-]\d{2}:?\d{2})$/.test(s);
  const ms = Date.parse(hasZone ? s : `${s}Z`);
  if (Number.isFinite(ms)) return ms;
  return hasZone ? Number.NaN : Date.parse(s); // date-only forms parse as UTC
}

const pad2 = (n: number): string => (n < 10 ? `0${n}` : String(n));

/** "09-23" */
function fmtMD(d: Date): string {
  return `${pad2(d.getUTCMonth() + 1)}-${pad2(d.getUTCDate())}`;
}

/** Snap the observed median bar spacing to the nearest known TF bucket
 *  (log-distance, so the choice is symmetric around each bucket edge). */
function nearestTf(spacingSec: number): number {
  let best = 60;
  let bestErr = Number.POSITIVE_INFINITY;
  for (const key of Object.keys(TF_SECONDS)) {
    const sec = TF_SECONDS[key];
    if (sec === undefined) continue;
    const err = Math.abs(Math.log(sec / spacingSec));
    if (err < bestErr) {
      bestErr = err;
      best = sec;
    }
  }
  return best;
}

/** Pick the grid step: the ladder rung closest (in log space) to the density
 *  target spanSec/maxLabels, floored at the 58px minimum spacing. */
function pickStepSec(targetSec: number, pxMinSec: number): number {
  const base = Math.max(1, targetSec);
  let rung = STEP_SECONDS[0] ?? 60;
  let bestErr = Number.POSITIVE_INFINITY;
  for (const s of STEP_SECONDS) {
    const err = Math.abs(Math.log(s / base));
    if (err < bestErr) {
      bestErr = err;
      rung = s;
    }
  }
  if (rung < pxMinSec) {
    // closest fell under the px floor — take the first rung that clears it
    // (unreachable past 14d: useMonths routes those grids to the month ladder)
    rung =
      STEP_SECONDS.find((s) => s >= pxMinSec) ??
      (STEP_SECONDS[STEP_SECONDS.length - 1] ?? 1209600);
  }
  return rung;
}

/** Same ladder choice for calendar-month grids. */
function pickStepMonths(targetSec: number, pxMinSec: number): number {
  const base = Math.max(1, targetSec / MONTH_SECONDS);
  let rung = STEP_MONTHS[0] ?? 1;
  let bestErr = Number.POSITIVE_INFINITY;
  for (const m of STEP_MONTHS) {
    const err = Math.abs(Math.log(m / base));
    if (err < bestErr) {
      bestErr = err;
      rung = m;
    }
  }
  if (rung * MONTH_SECONDS < pxMinSec) {
    rung = STEP_MONTHS.find((m) => m * MONTH_SECONDS >= pxMinSec) ?? STEP_MONTHS[STEP_MONTHS.length - 1] ?? 48;
  }
  return rung;
}

/**
 * Compute the time-axis labels for the currently visible chart window.
 *
 * @param startISO   first VISIBLE bar time (backend ISO)
 * @param endISO     last VISIBLE bar time (backend ISO)
 * @param slotW      px width of one slot (= plotW / visible slots, the
 *                   painter's `bw`) — drives both the density budget in px
 *                   and the slot count that recovers median bar spacing.
 * @param maxLabels  upper bound on the returned tick count (default 8; the
 *                   painter drops this argument and relies on the default).
 * @returns window-relative ticks, or [] for a degenerate window.
 */
export function niceTimeTicks(
  startISO: string | null,
  endISO: string | null,
  slotW: number,
  maxLabels = 8,
): TimeTick[] {
  const t0 = parseIso(startISO);
  const t1 = parseIso(endISO);
  // Degenerate/unparseable/empty window: honest empty set (painter draws
  // no time labels that frame).
  if (!Number.isFinite(t0) || !Number.isFinite(t1) || t1 <= t0) return [];
  if (!Number.isFinite(slotW) || slotW <= 0) return [];
  const maxN = Math.floor(maxLabels);
  if (!(maxN >= 1)) return [];

  const plotW = readPlotWidth();
  const spanSec = (t1 - t0) / 1000;
  // Slot count the painter will lay out (bw = plotW / count), >= 2 so one
  // bar-spacing gap exists. Median bar spacing = span across those slots.
  const count = Math.max(2, Math.round(plotW / slotW));
  const spacingSec = spanSec / (count - 1);

  // TF fingerprint -> label class: minute / hour / day.
  const tfSec = nearestTf(spacingSec);
  const gran: "min" | "hour" | "day" = tfSec < 3600 ? "min" : tfSec < DAY_SECONDS ? "hour" : "day";
  const spanMs = t1 - t0;
  const escalate = gran === "min" && spanMs > 24 * 3600 * 1000; // see header

  const labelFor = (d: Date): string => {
    const md = fmtMD(d);
    const hm = `${pad2(d.getUTCHours())}:${pad2(d.getUTCMinutes())}`;
    if (gran === "min") return escalate ? `${md} ${hm}` : hm;
    if (gran === "hour") return `${md} ${hm}`;
    return md; // day class: " 'YY" appended per-label on a year change
  };

  // Density: target ≈ maxN labels, floor = 58px between neighbours.
  const targetSec = spanSec / maxN;
  const pxMinSec = (MIN_GAP_PX / slotW) * spacingSec;
  const useMonths = Math.max(targetSec, pxMinSec) > 14 * DAY_SECONDS;

  const ticks: TimeTick[] = [];
  const seen = new Set<string>();
  let lastPx = Number.NEGATIVE_INFINITY;
  let firstYear: number | null = null;
  const rightLimitPx = plotW - MIN_GAP_PX; // never under the price axis

  /** Shared emit step: guards, year suffix, dedupe, cap. True = keep. */
  const emit = (slot: number, d: Date): boolean => {
    if (ticks.length >= maxN) return false;
    if (slot < 0) return false;
    const px = slot * slotW;
    if (px > rightLimitPx) return false;
    if (ticks.length > 0 && px - lastPx < MIN_GAP_PX) return false;
    const year = d.getUTCFullYear();
    if (ticks.length === 0) firstYear = year;
    let label = labelFor(d);
    if (gran === "day" && firstYear !== null && year !== firstYear) {
      label += ` '${String(year).slice(2)}`; // "01-05 '26" — unique across years
    }
    if (seen.has(label)) return false;
    ticks.push({ slot, label });
    seen.add(label);
    lastPx = px;
    return true;
  };

  if (useMonths) {
    const monthsPer = pickStepMonths(targetSec, pxMinSec);
    const d0 = new Date(t0);
    let y = d0.getUTCFullYear();
    let m = d0.getUTCMonth();
    let t = Date.UTC(y, m, 1);
    if (t < t0) {
      m += 1; // keep the grid phase — advancing only `t` would misalign
      t = Date.UTC(y, m, 1); // every later point for rungs > 1 month
    }
    while (t <= t1) {
      const d = new Date(t);
      const slot = Math.round((t - t0) / 1000 / spacingSec);
      if (!emit(slot, d)) {
        if (ticks.length >= maxN) break;
        if (slot * slotW > rightLimitPx) break; // slots only grow from here
      }
      m += monthsPer;
      t = Date.UTC(y, m, 1); // Date.UTC normalises month overflow
    }
    return ticks;
  }

  const stepSec = pickStepSec(targetSec, pxMinSec);
  const stepMs = stepSec * 1000;
  for (let t = Math.ceil(t0 / stepMs) * stepMs; t <= t1; t += stepMs) {
    const slot = Math.round((t - t0) / 1000 / spacingSec);
    if (!emit(slot, new Date(t))) {
      if (ticks.length >= maxN) break;
      if (slot >= 0 && slot * slotW > rightLimitPx) break; // past the axis: done
    }
  }
  return ticks;
}
