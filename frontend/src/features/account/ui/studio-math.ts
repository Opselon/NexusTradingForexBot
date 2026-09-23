/**
 * studio-math — pure presentation helpers for the analytics-studio pass.
 *
 * PURPOSE:  shared, unit-testable math for the account studio widgets:
 *           emphasis bars, distribution panel widths, P&L heat intensity,
 *           KPI delta tones. Presentational ONLY — every input is a number
 *           the backend already returned; nothing here fetches or invents.
 * OWNER:    uiux-w6-account
 * CONSUMES: nothing (pure leaf module)
 * PROVIDES: EmphBar + EmphSegment, distStyle/heatStyle/deltaTone, tones
 * INVARIANTS: a null input always yields a null/neutral result — never 0 and
 *           never a fabricated bar. Division-by-zero is clamped to 0.
 * EXTEND:   add a new exported pure function; do not add React here (this is
 *           the math core, intentionally separate from the visual variants).
 */

/* --------------------------- emphasis bars ------------------------------- */

/** One emphasis-bar segment: a magnitude + the token tone to draw it in. */
export interface EmphSegment {
  /** abs() is taken by the scaler; sign is only used for the tone. */
  value: number | null;
  tone?: "pos" | "neg" | "flat";
}

export interface EmphBar {
  segments: EmphSegment[];
  /** Min height in % (a real-but-tiny sample still reads as a real sample). */
  minPct?: number;
  maxPct?: number;
}

/**
 * Pure scaler: raw segments -> renderable heights (0..100) + tones.
 * Scale = max absolute magnitude over the set (derived from on-screen values
 * ONLY, and shown nowhere as a number — the label lives in the panel footer).
 * null stays null so the component can render an explicit gap slot.
 */
export function scaleEmphBars(bars: EmphBar[]): Array<Array<{ h: number | null; tone: string }>> {
  const min = bars.length ? (bars[0]?.minPct ?? 6) : 6;
  const max = bars.length ? (bars[0]?.maxPct ?? 100) : 100;
  let peak = 0;
  for (const b of bars) {
    for (const s of b.segments) {
      if (s.value === null) continue;
      const a = Math.abs(s.value);
      if (a > peak) peak = a;
    }
  }
  return bars.map((b) =>
    b.segments.map((s) => {
      if (s.value === null) return { h: null, tone: "gap" };
      const a = Math.abs(s.value);
      if (peak <= 0) return { h: min, tone: "flat" };
      const t = a / peak;
      return {
        h: Math.round(min + (max - min) * t),
        tone: s.tone ?? (s.value > 0 ? "pos" : s.value < 0 ? "neg" : "flat"),
      };
    }),
  );
}

/* ------------------------- distribution panels --------------------------- */

export type Tone = "pos" | "neg" | "dim" | "warn";

/**
 * Style props for one .acc-dist row: the fill width (0..100 over `full`) and
 * its tone. A null value returns the striped "unknown" variant — never a
 * zero-width solid bar that could be read as a healthy 0.
 */
export function distStyle(
  value: number | null,
  full: number,
  opts: { tone?: Tone } = {},
): { width: number; tone: Tone; unknown: boolean } {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return { width: 0, tone: "dim", unknown: true };
  }
  if (full <= 0 || !Number.isFinite(full)) return { width: 0, tone: "dim", unknown: true };
  const w = Math.min(100, Math.max(0, (Math.abs(value) / full) * 100));
  const tone = opts.tone ?? (value >= 0 ? "pos" : "neg");
  return { width: Math.round(w * 10) / 10, tone, unknown: false };
}

/**
 * Scale a signed quantity to a 0..100 track that spans [-full, +full] so a
 * negative value anchors at center (diverging scale, R-distribution style).
 */
export function divergingStyle(
  value: number | null,
  full: number,
): { insetInlineStart: number; width: number; tone: Tone; unknown: boolean } {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return { insetInlineStart: 50, width: 0, tone: "dim", unknown: true };
  }
  const f = full > 0 && Number.isFinite(full) ? full : 1;
  const half = Math.min(50, Math.max(0, (Math.abs(value) / f) * 50));
  if (value >= 0) return { insetInlineStart: 50, width: half, tone: "pos", unknown: false };
  return { insetInlineStart: 50 - half, width: half, tone: "neg", unknown: false };
}

/* ----------------------------- heat ramp --------------------------------- */

/**
 * P&L heat intensity for the trades table, derived from the on-screen set:
 * 0..1 where 1 = the largest abs net PnL on THIS page (nothing outside the
 * fetched window participates). 0 stays 0 so an exactly-flat row has no tint.
 */
export function heatIntensity(v: number | null | undefined, peak: number): number {
  if (v === null || v === undefined || !Number.isFinite(v)) return 0;
  if (peak <= 0 || !Number.isFinite(peak)) return 0;
  return Math.max(0, Math.min(1, Math.abs(v) / peak));
}

/** The largest abs value of a set — used to compute the on-screen peak. */
export function peakAbs(values: Array<number | null | undefined>): number {
  let p = 0;
  for (const v of values) {
    if (v === null || v === undefined || !Number.isFinite(v)) continue;
    const a = Math.abs(v);
    if (a > p) p = a;
  }
  return p;
}

/* ------------------------------ KPI deltas ------------------------------- */

export interface Delta {
  /** The rendered delta text, already signed and unit-aware. */
  text: string;
  tone: Tone;
  /** True when the delta is a client-side subtraction (label it "derived"). */
  derived: boolean;
}

export function deltaTone(v: number): "pos" | "neg" | "dim" {
  return v > 0 ? "pos" : v < 0 ? "neg" : "dim";
}

/** Signed money delta for KPI cards ("+$120.50" / "-$32.10"). */
export function moneyDelta(curr: number | null, base: number | null, digits = 2): Delta {
  if (curr === null || base === null) return { text: "—", tone: "dim", derived: false };
  const d = curr - base;
  const s = Math.abs(d).toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
  const text = `${d < 0 ? "−" : "+"}$${s}`;
  return { text, tone: deltaTone(d), derived: true };
}

/** Signed percentage-point delta for KPI cards. */
export function pctDelta(curr: number | null, base: number | null, digits = 2): Delta {
  if (curr === null || base === null) return { text: "—", tone: "dim", derived: false };
  const d = curr - base;
  return { text: `${d < 0 ? "−" : "+"}${Math.abs(d).toFixed(digits)}pt`, tone: deltaTone(d), derived: true };
}

/* ------------------------------ utilities -------------------------------- */

/** Null-safe max over an array (returns null when empty) — for footer stats. */
export function maxOf(values: Array<number | null | undefined>): number | null {
  let m: number | null = null;
  for (const v of values) {
    if (v === null || v === undefined || !Number.isFinite(v)) continue;
    if (m === null || v > m) m = v;
  }
  return m;
}
