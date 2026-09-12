/**
 * opsChromeMath — pure derivation layer for the pro ops-chrome widgets
 * (KpiStrip trend arrows, QuoteTape flash contract, feed-quality ages,
 * watermark truth, SL/TP input parsing).
 *
 * TRUTH CONTRACT (pinned by tests/js/pro_ops_chrome.test.mjs):
 *   1. `computeTrend` only ever compares a value the parent PROVIDED against a
 *      previous reading the parent PROVIDED. No prev (or no cur) → "neutral":
 *      the UI renders a dash, never a guessed arrow. There is no internal
 *      history here — remembering "prev" is the parent's job.
 *   2. `flashKeyDiffers` is the single equality check behind QuoteTape's
 *      flash. The KEY itself is owned by the parent and must only advance when
 *      `state_version` advanced (see OpsChrome.tsx) — this helper deliberately
 *      knows nothing about tick content so a reconnect replay cannot be
 *      dressed up as a market move.
 *   3. `formatTickAge` / `ageSecToMs` are formatting only. null/NaN stay null
 *      and render "—" (UNKNOWN), never 0 (which would read as "fresh").
 *   4. `watermarkWord` merges parent truth flags; precedence feedDown > stale;
 *      both false → null (no overlay). It never inspects data itself.
 *   5. `parseNumericField` validates FORMAT only (does this text parse to a
 *      finite number?). It never judges trading semantics — positive/negative,
 *      SL-above-entry etc. are the backend's call, and its verdict is relayed.
 *
 * Erasable TypeScript only (no enums / namespaces / parameter properties):
 * this module is imported directly by a node:test file that relies on Node's
 * built-in type stripping.
 */

export type TrendDirection = "up" | "down" | "flat" | "neutral";

/** Strict prev/cur comparison; any missing or non-finite side → "neutral". */
export function computeTrend(
  cur: number | null | undefined,
  prev: number | null | undefined,
): TrendDirection {
  if (cur === null || cur === undefined || !Number.isFinite(cur)) return "neutral";
  if (prev === null || prev === undefined || !Number.isFinite(prev)) return "neutral";
  if (cur > prev) return "up";
  if (cur < prev) return "down";
  return "flat";
}

/** Signed delta for tooltips — null when either side is missing (never 0). */
export function trendDelta(
  cur: number | null | undefined,
  prev: number | null | undefined,
): number | null {
  if (cur === null || cur === undefined || !Number.isFinite(cur)) return null;
  if (prev === null || prev === undefined || !Number.isFinite(prev)) return null;
  return cur - prev;
}

/** True when the QuoteTape flash key changed (strict — 41 vs "41" differ). */
export function flashKeyDiffers(
  next: number | string | null,
  last: number | string | null,
): boolean {
  return next !== last;
}

/** ms → human age ("12.3s" / "2m 5s" / "1h 2m"). null/NaN → "—" (UNKNOWN). */
export function formatTickAge(ageMs: number | null | undefined): string {
  if (ageMs === null || ageMs === undefined || !Number.isFinite(ageMs)) return "—";
  const s = Math.max(0, ageMs) / 1000;
  if (s < 60) return `${s.toFixed(1)}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m ${Math.floor(s % 60)}s`;
  return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
}

/** Backend age in SECONDS → ms for client age math; null stays null. */
export function ageSecToMs(sec: number | null | undefined): number | null {
  if (sec === null || sec === undefined || !Number.isFinite(sec)) return null;
  return Math.max(0, sec) * 1000;
}

export interface WatermarkFlags {
  /** Realtime transport is down (disconnected/failed) — parent truth. */
  feedDown: boolean;
  /** Backend truth flag (snapshot.is_stale / tick_stale) — parent truth. */
  stale: boolean;
}

/** Feed-down outranks stale; neither → null (render no overlay). */
export function watermarkWord(flags: WatermarkFlags): string | null {
  if (flags.feedDown) return "LIVE FEED DOWN";
  if (flags.stale) return "STALE";
  return null;
}

/**
 * Legacy envelope verdict for 200-response mutation bodies (pure, tested in
 * tests/js/pro_ops_chrome.test.mjs). The legacy routes return raw
 * `{success: bool}` bodies with NO `ok` key, so the hook's default
 * `res.ok && ...` rule cannot read them. Rule (parity with Web/app.js
 * `result.ok && result.body.success` and with useMutationFeedback's spirit):
 *   - success:false        → REFUSAL (HTTP 200 is not success)
 *   - success missing      → NOT an accepted command (never assume success)
 *   - success true         → accepted
 * `message` is the backend's own words if present, else null (caller falls
 * back to a fixed, honest line — no fabricated detail).
 */
export interface LegacyVerdict {
  ok: boolean;
  message: string | null;
}

export function legacyMutationVerdict(res: { success?: boolean | null; message?: string | null } | null | undefined): LegacyVerdict {
  if (!res || typeof res !== "object") return { ok: false, message: null };
  const ok = res.success === true;
  const message = typeof res.message === "string" && res.message.trim() !== "" ? res.message : null;
  return { ok, message };
}

/** True when the inter-frame gap (s) exceeds the budget — a WARNING, not a
 *  verdict: only the backend may call data STALE. null stays false (unknown). */
export function gapExceedsBudget(lastGapSec: number | null | undefined, budgetSec: number): boolean {
  if (lastGapSec === null || lastGapSec === undefined || !Number.isFinite(lastGapSec)) return false;
  if (!Number.isFinite(budgetSec) || budgetSec <= 0) return false;
  return lastGapSec > budgetSec;
}

export type NumericFieldParse =
  | { kind: "empty" }
  | { kind: "ok"; value: number }
  | { kind: "invalid"; raw: string };

/**
 * FORMAT-only numeric validation for the SL/TP editor:
 *   ""        → empty      (the editor must NOT send a user value for it)
 *   parses to a finite number (decimal, optional sign/exponent) → ok
 *   anything else ("abc", "1,5", "NaN", "1e999", "1.2.3")       → invalid
 * No trading-semantics judgement here — the backend owns verdicts.
 */
export function parseNumericField(raw: string): NumericFieldParse {
  const trimmed = raw.trim();
  if (trimmed === "") return { kind: "empty" };
  if (!/^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$/.test(trimmed)) {
    return { kind: "invalid", raw: trimmed };
  }
  const n = Number(trimmed);
  if (!Number.isFinite(n)) return { kind: "invalid", raw: trimmed };
  return { kind: "ok", value: n };
}
