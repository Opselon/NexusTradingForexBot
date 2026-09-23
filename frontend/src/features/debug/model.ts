/**
 * Debug: DTO -> view VOs + pure diff logic (unit-testable, no React).
 *
 * Invariants:
 *  - snapshot compare merges the SERVER diff (/api/debug/compare) with a
 *    key-level client diff of the two raw snapshots: added / changed / removed
 *    paths per top-level section, so the operator sees exactly which keys
 *    appeared/vanished, never a silent heuristic;
 *  - the model-test vector spec is derived from the live feature contract:
 *    dimension comes from /api/debug/features (never hardcoded), values must
 *    be finite numbers within [-1e6, 1e6] before the POST (bounds mirror the
 *    backend sanitizer's tolerance band);
 *  - statuses map ONLY known backend words; anything else renders UNKNOWN.
 */

import type { DebugFeatureRow, DebugState, DebugSubsystem } from "./api";
import { identityT, type Translate } from "@/features/config/validation";

/* ------------------------------------------------------------------ */
/* Status semantics                                                    */
/* ------------------------------------------------------------------ */

export type HealthLevel = "good" | "warn" | "bad" | "neutral";

const GOOD = new Set(["HEALTHY", "PASS", "OK", "READY", "FRESH", "RUNNING", "CONNECTED", "ACTIVE", "VALID", "SUCCESS", "COMPLETED", "UP"]);
const WARN = new Set(["DEGRADED", "WARNING", "STALE", "WARMING_UP", "PENDING", "CONNECTING", "PARTIAL", "BLOCKED_WAIT", "NOT_APPLICABLE"]);
const BAD = new Set(["UNHEALTHY", "FAIL", "FAILED", "ERROR", "DISCONNECTED", "NOT READY", "UNAVAILABLE", "CRITICAL", "NAN", "INF", "HALTED", "TIMEOUT"]);

export function healthLevel(status: string | null | undefined): HealthLevel {
  const s = (status ?? "").toUpperCase();
  if (GOOD.has(s)) return "good";
  if (WARN.has(s)) return "warn";
  if (BAD.has(s)) return "bad";
  if (!s || s === "UNKNOWN") return "neutral";
  return "neutral";
}

export function subsystemLevel(sub: DebugSubsystem): HealthLevel {
  return healthLevel(sub.status);
}

/* ------------------------------------------------------------------ */
/* Feature grid VO                                                     */
/* ------------------------------------------------------------------ */

export interface FeatureCell {
  index: number;
  key: string;
  name: string;
  value: string;
  status: string;
  level: HealthLevel;
}

export function featureCells(rows: readonly DebugFeatureRow[]): FeatureCell[] {
  return rows.map((r) => ({
    index: r.index,
    key: r.key,
    name: r.name,
    value: r.value === null || r.value === undefined ? "—" : Number.isFinite(r.value) ? r.value.toFixed(4) : String(r.value),
    status: r.status,
    level: r.is_valid ? "good" : healthLevel(r.status) === "good" ? "warn" : "bad",
  }));
}

/* ------------------------------------------------------------------ */
/* Snapshot key-level diff (client merge over raw snapshots)           */
/* ------------------------------------------------------------------ */

export type DiffKind = "added" | "removed" | "changed" | "same";

export interface DiffRow {
  path: string;
  kind: DiffKind;
  a: string;
  b: string;
}

function flatten(obj: unknown, prefix: string, out: Map<string, string>, depth = 0, t: Translate = identityT): void {
  if (depth > 4 || out.size > 4000) return;
  if (Array.isArray(obj)) {
    if (obj.length === 0) out.set(prefix, "[]");
    obj.slice(0, 50).forEach((v, i) => flatten(v, `${prefix}[${i}]`, out, depth + 1, t));
    if (obj.length > 50) out.set(`${prefix}.…`, t("debug.diff.more", "{n} more", { n: obj.length - 50 }));
    return;
  }
  if (obj && typeof obj === "object") {
    const entries = Object.entries(obj as Record<string, unknown>);
    if (entries.length === 0) out.set(prefix, "{}");
    for (const [k, v] of entries) flatten(v, prefix ? `${prefix}.${k}` : k, out, depth + 1, t);
    return;
  }
  out.set(prefix, obj === null || obj === undefined ? "—" : String(obj));
}

/**
 * Key-level diff between two snapshot payloads. Only the given top-level
 * sections are expanded (the full snapshot is huge; the operator picks the
 * section). Stable output capped at `cap` rows.
 */
export function diffSnapshotSections(a: DebugState | null, b: DebugState | null, section: string, cap = 300, t: Translate = identityT): DiffRow[] {
  if (!a || !b) return [];
  const fa = new Map<string, string>();
  const fb = new Map<string, string>();
  flatten(a[section], section, fa, 0, t);
  flatten(b[section], section, fb, 0, t);
  const keys = new Set([...fa.keys(), ...fb.keys()]);
  const rows: DiffRow[] = [];
  for (const key of Array.from(keys).sort()) {
    const va = fa.get(key);
    const vb = fb.get(key);
    if (va === undefined && vb !== undefined) rows.push({ path: key, kind: "added", a: "—", b: vb });
    else if (va !== undefined && vb === undefined) rows.push({ path: key, kind: "removed", a: va, b: "—" });
    else if (va !== vb) rows.push({ path: key, kind: "changed", a: va ?? "—", b: vb ?? "—" });
    if (rows.length >= cap) break;
  }
  return rows;
}

export function diffCounts(rows: readonly DiffRow[]): { added: number; removed: number; changed: number } {
  let added = 0;
  let removed = 0;
  let changed = 0;
  for (const r of rows) {
    if (r.kind === "added") added += 1;
    else if (r.kind === "removed") removed += 1;
    else if (r.kind === "changed") changed += 1;
  }
  return { added, removed, changed };
}

/* ------------------------------------------------------------------ */
/* Model-test vector spec                                              */
/* ------------------------------------------------------------------ */

export interface VectorSpec {
  dimension: number;
  min: number;
  max: number;
}

/** Bounds for the raw (pre-scaler) feature inputs. Deliberately wide: we
 *  only block nonsense (non-finite, absurd magnitudes); the backend stays
 *  the sanitizer of record. */
export const VECTOR_VALUE_BOUNDS = { min: -1e6, max: 1e6 } as const;

/**
 * Fresh feature vector specs (required dimension comes from the live
 * /api/debug/features contract read; fall back to NO vector until the
 * backend tells us the width — never guess).
 */
export function vectorSpec(featureCount: number | null | undefined): VectorSpec | null {
  if (!featureCount || featureCount <= 0) return null;
  return { dimension: featureCount, ...VECTOR_VALUE_BOUNDS };
}

/** Parse the textarea/inputs into the numeric array for POST /api/debug/model-test. */
export function parseVector(cells: readonly string[]): Array<number | null> {
  return cells.map((c) => {
    const t = c.trim();
    if (t === "") return null;
    const n = Number(t);
    return Number.isFinite(n) ? n : null;
  });
}

/* ------------------------------------------------------------------ */
/* Trace timeline VO                                                   */
/* ------------------------------------------------------------------ */

export interface TimelineRow {
  ts: string;
  stage: string;
  detail: string;
}

const SIGNAL_TS_KEYS = ["generated_at", "timestamp", "created_at"];
const ORDER_TS_KEYS = ["timestamp", "created_at", "updated_at"];

function pick(row: Record<string, unknown>, keys: string[]): string {
  for (const k of keys) {
    const v = row[k];
    if (typeof v === "string" && v) return v;
    if (typeof v === "number") return String(v);
  }
  return "—";
}

/** Merge signals+orders from the forensic trace into one chronological
 *  timeline (unknown timestamps sink to the bottom, never fabricated). */
export function traceTimeline(
  trace: { signal: Array<Record<string, unknown>> | null; orders: Array<Record<string, unknown>> },
  t: Translate = identityT,
): TimelineRow[] {
  const rows: TimelineRow[] = [];
  for (const s of trace.signal ?? []) {
    rows.push({
      ts: pick(s, SIGNAL_TS_KEYS),
      stage: `${t("debug.trace.signal_stage", "SIGNAL")} ${String(s.decision ?? s.action ?? s.signal ?? "")}`.trim(),
      detail: Object.entries(s)
        .filter(([k]) => !SIGNAL_TS_KEYS.includes(k))
        .slice(0, 8)
        .map(([k, v]) => `${k}=${String(v)}`)
        .join(" · "),
    });
  }
  for (const o of trace.orders ?? []) {
    rows.push({
      ts: pick(o, ORDER_TS_KEYS),
      stage: `${t("debug.trace.order_stage", "ORDER")} ${String(o.status ?? o.state ?? "")}`.trim(),
      detail: Object.entries(o)
        .filter(([k]) => !ORDER_TS_KEYS.includes(k))
        .slice(0, 8)
        .map(([k, v]) => `${k}=${String(v)}`)
        .join(" · "),
    });
  }
  return rows.sort((x, y) => {
    if (x.ts === "—" && y.ts !== "—") return 1;
    if (y.ts === "—" && x.ts !== "—") return -1;
    return x.ts.localeCompare(y.ts);
  });
}

/** Top-level section names of a debug state payload (fixed contract order). */
export const DEBUG_SECTIONS = [
  "runtime", "contract", "features", "model", "confidence", "policy", "risk", "exposure",
  "execution", "positions", "exit", "liquidity", "mslie", "news", "workers", "database",
  "caches", "chart", "sse", "errors",
] as const;

export function snapshotSections(snap: DebugState | null): string[] {
  if (!snap) return [];
  return Object.keys(snap).filter((k) => !["snapshot_id", "correlation_id", "timestamp", "engine_attached", "available", "reason"].includes(k));
}
