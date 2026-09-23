/**
 * vizMath — pure derivations behind the AI-Analysis charts (presentation only).
 *
 * WHY HERE: node-testable like frontend/src/lib/riskVizMath.ts — the module is
 * self-contained and its ONLY import is `import type` (erased before Node's
 * type-stripping resolves anything), so `node tests/js/ai_analysis_viz.test.mjs`
 * exercises the REAL code with no bundler, no alias, no DOM.
 *
 * Honesty contract (mirrors the viz-kit rules):
 *  - counts come from backend maps verbatim; percentages are arithmetic over
 *    those counts (a restatement, never a new opinion)
 *  - action labels are CLASSIFIED by explicit prefix/exact rules into display
 *    families; an unrecognised label is "unknown", never guessed
 *  - a history row whose timestamp cannot be parsed is DROPPED from the
 *    timeline and counted in `dropped` so the caption can say so; a missing
 *    confidence becomes a null sample (a gap), never a fabricated value
 *  - rrRatio is direction-agnostic arithmetic over the three recorded levels
 *    (|tp-entry| / |entry-sl|); any missing/non-finite level -> null
 */
// Deliberately ZERO imports (type-erasure-safe for node tests — riskVizMath.ts pattern).

/** Display family for a backend action label (explicit rules, never guessed). */
export type ActionFamily = "buy" | "sell" | "none" | "close" | "unknown";

/** Prefix/exact classification of backend action labels (BUY_LIMIT, SELL_MARKET,
 *  NO_TRADE, CLOSE_POSITION, ...). Case/whitespace tolerant. */
export function actionFamily(action: string | null | undefined): ActionFamily {
  const a = (action ?? "").trim().toUpperCase();
  if (a.startsWith("BUY")) return "buy";
  if (a.startsWith("SELL")) return "sell";
  if (a === "NO_TRADE") return "none";
  if (a.startsWith("CLOSE")) return "close";
  return "unknown";
}

/** Human word for a family (captions/aria; display only). */
export function familyWord(f: ActionFamily): string {
  return f === "none" ? "no trade" : f === "close" ? "close" : f === "unknown" ? "other" : f;
}

// ---------------------------------------------------------------------------
// KPI aggregation over /api/v1/decisions/stats by_action
// ---------------------------------------------------------------------------

export interface ActionKpi {
  total: number;
  /** Family "none" (NO_TRADE) count. */
  noTrade: number;
  /** total - noTrade — every non-NO_TRADE action, including closes. */
  trade: number;
  /** share of total occupied by `trade`, in 0..1 (null when total = 0). */
  tradeShare: number | null;
  /** share of total occupied by `noTrade`, in 0..1 (null when total = 0). */
  noTradeShare: number | null;
}

export function actionKpi(byAction: Record<string, number> | undefined): ActionKpi {
  let total = 0;
  let noTrade = 0;
  for (const [label, raw] of Object.entries(byAction ?? {})) {
    const n = Number(raw);
    if (!Number.isFinite(n) || n <= 0) continue;
    total += n;
    if (actionFamily(label) === "none") noTrade += n;
  }
  const trade = total - noTrade;
  return {
    total,
    noTrade,
    trade,
    tradeShare: total > 0 ? trade / total : null,
    noTradeShare: total > 0 ? noTrade / total : null,
  };
}

/** Largest-count entry of a backend map (e.g. dominant decision stage). */
export function topEntry(
  rec: Record<string, number> | undefined,
): { label: string; count: number } | null {
  let best: { label: string; count: number } | null = null;
  for (const [label, raw] of Object.entries(rec ?? {})) {
    const n = Number(raw);
    if (!Number.isFinite(n) || n <= 0) continue;
    if (!best || n > best.count) best = { label, count: n };
  }
  return best;
}

// ---------------------------------------------------------------------------
// Count rows (stage bars / reason bars / donut legend share one derivation)
// ---------------------------------------------------------------------------

export interface CountRow {
  label: string;
  count: number;
  /** count / sum(counts), in 0..100 (0 when the map is empty). */
  pct: number;
}

/** Sorted-descending count rows with their share of the map's own total. */
export function countRows(rec: Record<string, number> | undefined): CountRow[] {
  const entries: Array<[string, number]> = [];
  for (const [label, raw] of Object.entries(rec ?? {})) {
    const n = Number(raw);
    entries.push([label, Number.isFinite(n) && n > 0 ? n : 0]);
  }
  const total = entries.reduce((s, [, n]) => s + n, 0);
  return entries
    .map(([label, count]) => ({ label, count, pct: total > 0 ? (count / total) * 100 : 0 }))
    .sort((a, b) => b.count - a.count || a.label.localeCompare(b.label));
}

// ---------------------------------------------------------------------------
// Action-family totals for the donut
// ---------------------------------------------------------------------------

export interface FamilyTotal {
  family: ActionFamily;
  count: number;
}

/** Backend by_action collapsed into display families, sorted by count. */
export function familyTotals(byAction: Record<string, number> | undefined): FamilyTotal[] {
  const acc = new Map<ActionFamily, number>();
  for (const [label, raw] of Object.entries(byAction ?? {})) {
    const n = Number(raw);
    if (!Number.isFinite(n) || n <= 0) continue;
    const f = actionFamily(label);
    acc.set(f, (acc.get(f) ?? 0) + n);
  }
  return [...acc.entries()]
    .map(([family, count]) => ({ family, count }))
    .sort((a, b) => b.count - a.count);
}

export interface DonutSeg {
  family: ActionFamily;
  count: number;
  /** Arc length in viewBox units for the given radius. */
  len: number;
  /** stroke-dashoffset (negative accumulated length) so segments abut. */
  offset: number;
}

/**
 * Donut segment geometry: lengths along the circle's circumference for
 * stroke-dasharray rendering. Segment lengths ARE the backend counts scaled by
 * circumference; zero/negative total yields no segments.
 */
export function donutSegments(
  rows: FamilyTotal[],
  radius: number,
): { segs: DonutSeg[]; total: number } {
  const total = rows.reduce((s, r) => s + Math.max(0, r.count), 0);
  if (total <= 0 || !Number.isFinite(radius) || radius <= 0) return { segs: [], total: 0 };
  const circ = 2 * Math.PI * radius;
  let acc = 0;
  const segs = rows
    .filter((r) => r.count > 0)
    .map((r) => {
      const len = (r.count / total) * circ;
      // -acc, normalized: -0 fails Object.is against 0 (canonical zero offset).
      const seg: DonutSeg = { family: r.family, count: r.count, len, offset: -acc || 0 };
      acc += len;
      return seg;
    });
  return { segs, total };
}

// ---------------------------------------------------------------------------
// R:R from recorded levels — direction-agnostic arithmetic
// ---------------------------------------------------------------------------

/**
 * Reward/risk ratio over the three recorded prices: |tp - entry| /
 * |entry - sl|. Any missing/non-finite level or zero risk -> null (the UI
 * renders "—" — never a guessed number).
 */
export function rrRatio(
  entry: number | null | undefined,
  sl: number | null | undefined,
  tp: number | null | undefined,
): number | null {
  if (typeof entry !== "number" || !Number.isFinite(entry)) return null;
  if (typeof sl !== "number" || !Number.isFinite(sl)) return null;
  if (typeof tp !== "number" || !Number.isFinite(tp)) return null;
  const risk = Math.abs(entry - sl);
  if (!(risk > 0)) return null;
  return Math.abs(tp - entry) / risk;
}

// ---------------------------------------------------------------------------
// Confidence timeline over history rows
// ---------------------------------------------------------------------------

export interface TimelinePoint {
  /** Epoch ms parsed from generated_at. */
  t: number;
  /** Pre-normalised confidence in 0..1, or null = "no sample" (a gap). */
  v: number | null;
  action: string | null;
}

export interface TimelineSeries {
  points: TimelinePoint[];
  /** Rows excluded because generated_at could not be parsed (caption says so). */
  dropped: number;
  /** Epoch window actually plotted (null when nothing plotted). */
  from: number | null;
  to: number | null;
}

/** Build an oldest->newest timeline from history rows. `conf01` is the caller's
 *  already-normalised confidence (model.confidence01) — this module never
 *  re-derives the 0..1/0..100 rule, so there is exactly one owner for it. */
export function timelineSeries(
  items: Array<{ generated_at?: string | null; action?: string | null; conf01?: number | null }> | undefined,
): TimelineSeries {
  const points: TimelinePoint[] = [];
  let dropped = 0;
  for (const s of items ?? []) {
    const t = Date.parse(String(s.generated_at ?? ""));
    if (!Number.isFinite(t)) {
      dropped += 1;
      continue;
    }
    const raw = s.conf01;
    const v = typeof raw === "number" && Number.isFinite(raw) ? Math.max(0, Math.min(1, raw)) : null;
    points.push({ t, v, action: s.action ?? null });
  }
  points.sort((a, b) => a.t - b.t);
  const first = points[0] ?? null;
  const last = points[points.length - 1] ?? null;
  return {
    points,
    dropped,
    from: first ? first.t : null,
    to: last ? last.t : null,
  };
}
