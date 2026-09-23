/**
 * dependencyModel — derivation over the dependency-graph payload.
 *
 * PURE FUNCTIONS ONLY. No React, no fetch. Every function takes the raw
 * /api/dependency/* payload and derives a display value, so the contract can
 * be unit-pinned in tests/js/pro_dependency_viz.test.mjs against the VERBATIM
 * live payload (probed 2026-09-23, port 59273).
 *
 * TRUTHFULNESS CONTRACT (the reason this module exists):
 *  1. A verdict word ("healthy"/"degraded"/"HIGH RISK"/"unknown") is never
 *     manufactured — it is read from the producer's own `status` /
 *     `impact_kind` string, or reported as UNKNOWN. No client-side heuristic
 *     invents a risk the backend did not compute.
 *  2. A MISSING key stays UNKNOWN — never coerced to a passing value. A null
 *     limit never renders as a satisfied one (mirrors riskGateTrace.ts law).
 *  3. Node ids are the producer's canonical `id` (e.g. mod:nexus_scalp…,
 *     cls:…, stdlib:os); qualified_name is the fallback ONLY when id is absent.
 *  4. Impact is read as LISTS (direct/transitive), never as the legacy
 *     impacted/impacted_count keys the producer never emits — the old UI
 *     rendered "No downstream impact recorded" on every node because of it.
 */

import type {
  DependencyCycle,
  DependencyHotspot,
  DependencyImpactResponse,
  DependencyNode,
  DependencyPathResponse,
  DependencySummaryResponse,
  DependencyViolation,
  NodeMetrics,
} from "./api";

export const UNKNOWN_WORD = "unknown";

/* ------------------------------- node ids -------------------------------- */

/** Canonical node id: producer `id`, qualified_name fallback, else "". */
export function nodeId(n: DependencyNode | undefined | null): string {
  return String(n?.id ?? n?.qualified_name ?? "");
}

/**
 * Short, kind-colored label for a node id: strips the `mod:`/`cls:`/`ext:`
 * prefix and trims to the last two dotted segments
 * (nexus_scalp.application.live_engine -> application.live_engine). The full
 * id stays in the title / copy target — this is a display compression, never
 * a different identity.
 */
export function shortNodeLabel(id: string | undefined | null): string {
  const raw = String(id ?? "");
  if (!raw) return "";
  const colon = raw.indexOf(":");
  const body = colon > 0 ? raw.slice(colon + 1) : raw;
  const last = body.split(/[\\/]/).pop() ?? body;
  const parts = last.split(".");
  return parts.slice(Math.max(0, parts.length - 2)).join(".");
}

/** Prefix class of a node id -> mod / cls / stdlib / external / other. */
export function nodeKindPrefix(id: string | undefined | null): string {
  const raw = String(id ?? "");
  const colon = raw.indexOf(":");
  return colon > 0 ? raw.slice(0, colon) : "other";
}

/** True for stdlib:/external: leaves — never NSE-architecture hotspots. */
export function isForeignLeaf(id: string | undefined | null): boolean {
  const p = nodeKindPrefix(id);
  return p === "stdlib" || p === "external" || p === "ext";
}

/* ------------------------------- badges ---------------------------------- */

export type BadgeLevel = "good" | "warn" | "bad" | "neutral" | "unknown";

/** Backend status string -> badge level. Unrecognized = UNKNOWN (never guessed). */
export function statusLevel(status: string | null | undefined): BadgeLevel {
  if (!status) return "unknown";
  const s = String(status).toUpperCase();
  if (["READY", "IDLE", "PASS", "CONNECTED", "ACTIVE", "OK", "RUNNING", "NORMAL"].includes(s))
    return "good";
  if (["STALE", "DEGRADED", "CONNECTING", "ELEVATED", "WARNING", "PENDING"].includes(s))
    return "warn";
  if (["ERROR", "DISCONNECTED", "UNAVAILABLE", "FAILED", "BLOCKED", "INVALID", "HALTED"].includes(s))
    return "bad";
  if (["UNKNOWN", "UNRESOLVED"].includes(s)) return "neutral";
  return "unknown";
}

export function severityLevel(severity: string | null | undefined): BadgeLevel {
  const s = String(severity ?? "").toUpperCase();
  if (s === "CRITICAL" || s === "HIGH") return "bad";
  if (s === "MEDIUM") return "warn";
  if (s === "LOW") return "neutral";
  return "unknown";
}

/**
 * Health verdict from the SUMMARY payload (producer-authoritative).
 * Degraded when the producer's own health counts are non-zero; `healthy` only
 * when the producer said ok AND every health count is zero. Any absent count
 * degrades to unknown rather than silently passing.
 */
export function summaryHealth(data: DependencySummaryResponse | undefined | null): {
  level: BadgeLevel;
  word: string;
  healthy: boolean;
} {
  if (!data) return { level: "unknown", word: UNKNOWN_WORD, healthy: false };
  const h = data.health ?? {};
  const counts = [
    h.cycles,
    h.unresolved_imports,
    h.unresolved_di_bindings,
    h.architecture_violations,
  ];
  const present = counts.filter((c) => c != null);
  const bad = present.filter((c) => (c as number) > 0).length;
  if (present.length < counts.length) {
    // a health count the producer did not return -> not provably healthy
    return {
      level: bad > 0 ? "bad" : "unknown",
      word: bad > 0 ? "degraded" : UNKNOWN_WORD,
      healthy: false,
    };
  }
  const healthy = bad === 0;
  return { level: healthy ? "good" : "bad", word: healthy ? "healthy" : "degraded", healthy };
}

/* ------------------------------ hotspots --------------------------------- */

export const HOTSPOT_FLAGS = [
  "HIGH_FAN_IN",
  "HIGH_FAN_OUT",
  "CYCLE",
  "ARCHITECTURE_VIOLATION",
  "UNRESOLVED_DI",
  "RUNTIME_CRITICAL",
] as const;
export type HotspotFlag = (typeof HOTSPOT_FLAGS)[number];

export function hotspotNode(h: DependencyHotspot | undefined | null): string {
  return String(h?.node_id ?? "");
}

export function hotspotScore(h: DependencyHotspot | undefined | null): number | null {
  const s = h?.risk_score;
  return typeof s === "number" && Number.isFinite(s) ? s : null;
}

/** Risk score 0..max normalized for the proportional bar (never fabricated). */
export function hotspotScorePct(h: DependencyHotspot | undefined | null, max: number): number {
  const s = hotspotScore(h);
  if (s === null || !(max > 0)) return 0;
  return Math.max(0, Math.min(1, s / max)) * 100;
}

/** Tone for the score bar: the producer's own criticality, else score bands. */
export function hotspotTone(h: DependencyHotspot | undefined | null): "red" | "amber" | "blue" {
  const crit = String(h?.criticality ?? "").toUpperCase();
  if (crit === "CRITICAL" || crit === "HIGH") return "red";
  const s = hotspotScore(h);
  if (s !== null && s >= 40) return "amber";
  return "blue";
}

/** Flag chips as the producer emitted them (sorted into the known order). */
export function hotspotFlags(h: DependencyHotspot | undefined | null): string[] {
  const f = h?.flags;
  if (!Array.isArray(f)) return [];
  return f
    .filter((x): x is string => typeof x === "string")
    .sort(
      (a, b) =>
        HOTSPOT_FLAGS.indexOf(a as HotspotFlag) - HOTSPOT_FLAGS.indexOf(b as HotspotFlag),
    );
}

/** Instability I = FanOut/(FanIn+FanOut) as the producer computed it. */
export function instabilityPct(m: NodeMetrics | undefined | null): number | null {
  const v = m?.instability;
  if (typeof v !== "number" || !Number.isFinite(v)) return null;
  return Math.max(0, Math.min(1, v)) * 100;
}

/** Fan-in/-out display: "247 / 14". */
export function fanPair(m: { fan_in?: number; fan_out?: number } | undefined | null): string {
  const fi = m?.fan_in;
  const fo = m?.fan_out;
  return `${fi ?? "—"} / ${fo ?? "—"}`;
}

/* ------------------------------- impact ---------------------------------- */

export const IMPACT_KINDS = ["HIGH_RISK", "TRANSITIVE"] as const;
export type ImpactKind = (typeof IMPACT_KINDS)[number];

/** Producer's impact_kind -> badge level (never invented). */
export function impactLevel(kind: string | null | undefined): BadgeLevel {
  const k = String(kind ?? "").toUpperCase();
  if (k === "HIGH_RISK") return "bad";
  if (k === "TRANSITIVE") return "warn";
  return "unknown";
}

export function impactWord(kind: string | null | undefined): string {
  const k = String(kind ?? "").toUpperCase();
  if (k === "HIGH_RISK") return "HIGH RISK";
  if (k === "TRANSITIVE") return "transitive";
  return UNKNOWN_WORD;
}

/** Total blast radius = direct + transitive (both are id lists). */
export function impactTotal(d: DependencyImpactResponse | undefined | null): number {
  if (!d) return 0;
  const direct = Array.isArray(d.direct) ? d.direct.length : 0;
  const transitive = Array.isArray(d.transitive) ? d.transitive.length : 0;
  return direct + transitive;
}

/** The producer's unknown-node error payload -> reported, not "no impact". */
export function impactUnknownNode(d: DependencyImpactResponse | undefined | null): boolean {
  return !!(d && d.error && d.node_id);
}

/**
 * Partition an impact payload into rows for the blast-radius bars.
 * direct/transitive are id lists; tests/api/runtime are the producer's own
 * classification lists, surfaced first because they are the actionable ones.
 */
export function impactRows(
  d: DependencyImpactResponse | undefined | null,
): Array<{ label: string; ids: string[]; tone: BadgeLevel }> {
  if (!d) return [];
  const rows: Array<{ label: string; ids: string[]; tone: BadgeLevel }> = [];
  const push = (label: string, ids: string[] | undefined, tone: BadgeLevel) => {
    const list = (Array.isArray(ids) ? ids : []).filter(
      (x): x is string => typeof x === "string" && x !== "",
    );
    if (list.length === 0) return;
    rows.push({ label, ids: list, tone });
  };
  push("runtime (critical/high)", d.runtime_impact, "bad");
  push("tests likely affected", d.tests_likely_affected, "warn");
  push("api endpoints", d.api_impact, "warn");
  push("direct dependents", d.direct, "neutral");
  push("transitive", d.transitive, "neutral");
  return rows;
}

/** Same rows with stdlib/external leaves counted out of the id list. */
export function impactRowsNSE(
  d: DependencyImpactResponse | undefined | null,
): Array<{ label: string; ids: string[]; tone: BadgeLevel; foreign: number }> {
  return impactRows(d).map((r) => {
    const nse = r.ids.filter((x) => !isForeignLeaf(x));
    return { ...r, ids: nse, foreign: r.ids.length - nse.length };
  });
}

/* -------------------------------- cycles --------------------------------- */

export function cycleSeverity(c: DependencyCycle | undefined | null): BadgeLevel {
  return severityLevel(c?.severity);
}

export function cycleLength(c: DependencyCycle | undefined | null): number {
  const p = c?.path;
  return Array.isArray(p) ? p.length : 0;
}

export function cycleLabel(c: DependencyCycle | undefined | null): string {
  return String(c?.cycle_id ?? "");
}

export function cyclePathText(c: DependencyCycle | undefined | null): string {
  const p = c?.path;
  if (!Array.isArray(p) || p.length === 0) return "";
  return p.map((id) => shortNodeLabel(id)).join(" → ");
}

/* ------------------------------ violations ------------------------------- */

export function violationSeverity(v: DependencyViolation | undefined | null): BadgeLevel {
  return severityLevel(v?.severity);
}

/* ------------------------------- path ------------------------------------ */

export function pathFound(p: DependencyPathResponse | undefined | null): boolean {
  return !!(p && p.found === true);
}

export function pathLength(p: DependencyPathResponse | undefined | null): number {
  const list = p?.path;
  return Array.isArray(list) ? list.length : 0;
}

/** Producer's unknown-node error on a path query -> reported as an error. */
export function pathUnknownNode(p: DependencyPathResponse | undefined | null): boolean {
  return !!(p && p.error && (p.source || p.target));
}

/* ------------------------- graph composition ---------------------------- */

export type NodeKindFilter =
  | "all"
  | "MODULE"
  | "CLASS"
  | "PROTOCOL"
  | "INTERFACE"
  | "EXTERNAL"
  | "UNRESOLVED";

export const KIND_OPTIONS: NodeKindFilter[] = [
  "all",
  "MODULE",
  "CLASS",
  "PROTOCOL",
  "INTERFACE",
  "EXTERNAL",
  "UNRESOLVED",
];

/** Counts per node kind for the filter chips (UNRESOLVED is a status slice). */
export function kindCounts(nodes: DependencyNode[]): Record<string, number> {
  const out: Record<string, number> = {};
  for (const n of nodes) {
    const k = String(n.kind ?? "");
    if (!k) continue;
    out[k] = (out[k] ?? 0) + 1;
  }
  out["UNRESOLVED"] = nodes.filter((n) => isUnresolved(n)).length;
  return out;
}

export function isUnresolved(n: DependencyNode): boolean {
  return String(n.status ?? "").toUpperCase() === "UNRESOLVED";
}

export function isCritical(n: DependencyNode): boolean {
  return ["CRITICAL", "HIGH"].includes(String(n.criticality ?? "").toUpperCase());
}

/** Set of hotspot node ids from the summary, for row marking. */
export function hotspotIdSet(hotspots: DependencyHotspot[] | undefined | null): Set<string> {
  return new Set((hotspots ?? []).map((h) => hotspotNode(h)).filter(Boolean));
}

/* ------------------------------- sorting --------------------------------- */

export type NodeSortKey =
  | "id"
  | "kind"
  | "fan_in"
  | "fan_out"
  | "instability"
  | "criticality";
export type SortDir = "asc" | "desc";

/** Metrics map from /api/dependency/metrics, keyed by node id. */
export type MetricsMap = Record<string, NodeMetrics>;

const CRITICALITY_RANK: Record<string, number> = { CRITICAL: 0, HIGH: 1, UNKNOWN: 2 };

/** Sort nodes by the chosen key; fan/instability need the metrics map. */
export function sortNodes(
  nodes: DependencyNode[],
  key: NodeSortKey,
  dir: SortDir,
  metrics: MetricsMap | undefined | null,
): DependencyNode[] {
  const mul = dir === "asc" ? 1 : -1;
  const num = (n: DependencyNode, k: "fan_in" | "fan_out" | "instability"): number => {
    const m = metrics?.[nodeId(n)];
    const v = m?.[k];
    return typeof v === "number" && Number.isFinite(v) ? v : 0;
  };
  return [...nodes].sort((a, b) => {
    let va: number | string;
    let vb: number | string;
    switch (key) {
      case "id":
        va = nodeId(a);
        vb = nodeId(b);
        break;
      case "kind":
        va = String(a.kind ?? "");
        vb = String(b.kind ?? "");
        break;
      case "criticality":
        va = CRITICALITY_RANK[String(a.criticality ?? "UNKNOWN").toUpperCase()] ?? 3;
        vb = CRITICALITY_RANK[String(b.criticality ?? "UNKNOWN").toUpperCase()] ?? 3;
        break;
      case "fan_in":
      case "fan_out":
      case "instability":
        va = num(a, key);
        vb = num(b, key);
        break;
    }
    if (typeof va === "number" && typeof vb === "number") return (va - vb) * mul;
    return String(va).localeCompare(String(vb)) * mul;
  });
}

/* ------------------------------ filtering -------------------------------- */

export function filterNodes(
  nodes: DependencyNode[],
  opts: { query: string; kind: NodeKindFilter },
): DependencyNode[] {
  const q = opts.query.trim().toLowerCase();
  return nodes.filter((n) => {
    const id = nodeId(n).toLowerCase();
    const qn = String(n.qualified_name ?? "").toLowerCase();
    if (q && !(id.includes(q) || qn.includes(q))) return false;
    if (opts.kind === "all") return true;
    if (opts.kind === "UNRESOLVED") return isUnresolved(n);
    return String(n.kind ?? "") === opts.kind;
  });
}

/* ----------------------------- search highlight -------------------------- */

/** Split text into match/non-match runs for <mark> highlighting. */
export function highlightRuns(
  text: string,
  query: string,
): Array<{ text: string; hit: boolean }> {
  const q = query.trim();
  if (!q || !text) return [{ text, hit: false }];
  const out: Array<{ text: string; hit: boolean }> = [];
  const hay = text.toLowerCase();
  const needle = q.toLowerCase();
  let i = 0;
  while (i <= text.length) {
    const at = hay.indexOf(needle, i);
    if (at < 0) {
      if (i < text.length) out.push({ text: text.slice(i), hit: false });
      break;
    }
    if (at > i) out.push({ text: text.slice(i, at), hit: false });
    out.push({ text: text.slice(at, at + needle.length), hit: true });
    i = at + needle.length;
  }
  return out.filter((r) => r.text.length > 0);
}
