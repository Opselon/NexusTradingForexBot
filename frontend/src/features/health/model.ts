/**
 * Health: DTO -> matrix VO mappers (pure, unit-testable).
 *
 * The matrix is built from several independent reads; each cell carries its
 * OWN fetch timestamp, and staleness coloring is derived from the cell age
 * against the poll budget (10s) — never from the payload claiming freshness.
 * Verdict semantics are the backend's (verdict/status strings are mapped,
 * unknown values render as UNKNOWN, never guessed) — the vocabulary below is
 * the same table the debug hub uses, kept local so bounded contexts stay
 * import-independent (config/validation owns the shared FORM logic instead).
 */

import type { DebugSubsystem, HealthCheck, WorkerRow } from "./api";

export type HealthLevel = "good" | "warn" | "bad" | "neutral";

const GOOD = new Set(["HEALTHY", "PASS", "OK", "READY", "FRESH", "RUNNING", "CONNECTED", "ACTIVE", "VALID", "SUCCESS", "COMPLETED", "UP"]);
const WARN = new Set(["DEGRADED", "WARNING", "STALE", "WARMING_UP", "PENDING", "CONNECTING", "PARTIAL", "ELEVATED"]);
const BAD = new Set(["UNHEALTHY", "FAIL", "FAILED", "ERROR", "DISCONNECTED", "NOT READY", "UNAVAILABLE", "CRITICAL", "NAN", "INF", "HALTED", "TIMEOUT", "BLOCKED"]);

export function healthLevel(status: string | null | undefined): HealthLevel {
  const s = (status ?? "").toUpperCase();
  if (GOOD.has(s)) return "good";
  if (WARN.has(s)) return "warn";
  if (BAD.has(s)) return "bad";
  return "neutral";
}

export interface MatrixCell {
  id: string;
  name: string;
  status: string;
  level: HealthLevel;
  detail: string;
  metrics: Array<[string, unknown]>;
  /** client-captured wall clock of the successful fetch that produced it */
  fetchedAtMs: number | null;
  source: string;
}

export function cellFromSubsystem(sub: DebugSubsystem, source: string, fetchedAtMs: number | null): MatrixCell {
  return {
    id: `${source}:${sub.name}`,
    name: sub.name,
    status: sub.status,
    level: healthLevel(sub.status),
    detail: sub.detail ?? "",
    metrics: Object.entries(sub.metrics ?? {}),
    fetchedAtMs,
    source,
  };
}

export function cellFromCheck(check: HealthCheck, source: string, fetchedAtMs: number | null): MatrixCell {
  return {
    id: `${source}:${check.category}`,
    name: String(check.category ?? "UNKNOWN"),
    status: String(check.verdict ?? "UNKNOWN"),
    level: healthLevel(check.verdict),
    detail: [check.reason, check.suggestion].filter(Boolean).join(" → "),
    metrics: Object.entries(check)
      .filter(([k]) => !["category", "verdict", "reason", "suggestion", "state", "optional"].includes(k))
      .slice(0, 6),
    fetchedAtMs,
    source,
  };
}

/** Worker state -> cell (NOT_ATTACHED / STOPPED are reported, not hidden). */
export function cellFromWorker(w: WorkerRow, fetchedAtMs: number | null): MatrixCell {
  const s = String(w.state ?? "UNKNOWN").toUpperCase();
  const level: HealthLevel = s === "RUNNING" || s === "HEALTHY" ? "good" : s === "STOPPED" || s === "NOT_ATTACHED" || s === "UNKNOWN" ? "neutral" : healthLevel(s);
  return {
    id: `workers:${w.name}`,
    name: w.name,
    status: s,
    level,
    detail: w.attached === false ? "attribute missing on engine" : "",
    metrics: Object.entries(w)
      .filter(([k]) => k !== "name" && k !== "state" && k !== "attached")
      .slice(0, 4),
    fetchedAtMs,
    source: "workers",
  };
}

export interface CellTone {
  /** visual staleness class given cell age and the poll budget. */
  staleClass: "" | "stale";
  /** semantic color given verdict + age: an old GOOD reading is amber, not green. */
  level: HealthLevel;
  ageMs: number | null;
}

export function cellTone(cell: MatrixCell, nowMs: number, pollBudgetMs = 10_000): CellTone {
  const ageMs = cell.fetchedAtMs === null ? null : Math.max(0, nowMs - cell.fetchedAtMs);
  const stale = ageMs === null || ageMs > pollBudgetMs * 2.5;
  const level: HealthLevel = stale && (cell.level === "good" || cell.level === "neutral") ? "warn" : cell.level;
  return { staleClass: stale ? "stale" : "", level, ageMs };
}

export function matrixSummary(cells: readonly MatrixCell[]): { good: number; warn: number; bad: number; neutral: number } {
  let good = 0;
  let warn = 0;
  let bad = 0;
  let neutral = 0;
  for (const c of cells) {
    if (c.level === "good") good += 1;
    else if (c.level === "warn") warn += 1;
    else if (c.level === "bad") bad += 1;
    else neutral += 1;
  }
  return { good, warn, bad, neutral };
}
