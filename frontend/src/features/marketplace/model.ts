/**
 * Marketplace model — DTO -> view-model mapping + invariants (pure).
 *
 * Honesty rules ported from the legacy Web/marketplace.js contract:
 *  - a missing/uncomputed score renders NOT_AVAILABLE (never a misleading 0);
 *  - enablement outcomes PENDING / DENIED / granted are distinct backend words;
 *  - lifecycle is echoed verbatim; unknown words stay unknown.
 */

import type { MktEnableMode, MktRepair, MktScoreSnapshot, MktSeed, MktSeedDetail } from "./types";

export const NOT_AVAILABLE = "NOT_AVAILABLE";

/** Backend rule (api_v1/marketplace.py): 1 <= count <= 500, default 25. */
export function validateInstallCount(raw: string): { value: number | null; error: string | null } {
  const t = raw.trim();
  if (t === "") return { value: null, error: "count is required (1–500)" };
  if (!/^\d+$/.test(t)) return { value: null, error: "count must be a whole number" };
  const n = Number(t);
  if (n < 1 || n > 500) return { value: null, error: "count must be between 1 and 500 (backend VALIDATION_ERROR)" };
  return { value: n, error: null };
}

export const ENABLE_MODES: Array<{ id: MktEnableMode; label: string; hint: string }> = [
  { id: "RESEARCH", label: "Research", hint: "run inside the research pipeline only" },
  { id: "PAPER", label: "Paper", hint: "simulated execution (gates still enforced)" },
  { id: "SHADOW", label: "Shadow", hint: "shadow signals, no orders" },
  { id: "LIVE_REQUEST", label: "Live request", hint: "PENDING until governance grants — never silent" },
];

/** Lifecycle -> semantic badge level (unknown words stay neutral). */
export function lifecycleLevel(lc: string | null | undefined): "good" | "warn" | "bad" | "neutral" {
  switch ((lc ?? "").toUpperCase()) {
    case "VALIDATED":
    case "RESEARCH_VALIDATED":
    case "LIVE_ELIGIBLE":
      return "good";
    case "RESEARCH_RUNNING":
    case "RESEARCH_PENDING":
    case "INSTALLED":
    case "LIVE_CANDIDATE":
      return "warn";
    case "REJECTED":
    case "QUARANTINED":
    case "RETIRED":
    case "DISABLED":
      return "bad";
    default:
      return "neutral";
  }
}

export interface SeedVM {
  seed: MktSeed;
  label: string;
  lifecycle: string;
}

export function toSeedVM(seed: MktSeed): SeedVM {
  return {
    seed,
    label: seed.name ? `${seed.name} (${seed.seed_id})` : seed.seed_id,
    lifecycle: String(seed.lifecycle ?? "UNKNOWN").toUpperCase(),
  };
}

/** Factors may arrive as an object (decoded) or a JSON string (raw column). */
export function factorsOf(s: MktScoreSnapshot): Record<string, number> | null {
  if (s.factors && typeof s.factors === "object") {
    const out: Record<string, number> = {};
    for (const [k, v] of Object.entries(s.factors)) {
      if (typeof v === "number" && Number.isFinite(v)) out[k] = v;
    }
    return Object.keys(out).length > 0 ? out : null;
  }
  if (typeof s.factors === "string" && s.factors.trim() !== "") {
    try {
      const parsed: unknown = JSON.parse(s.factors);
      if (parsed && typeof parsed === "object") {
        const out: Record<string, number> = {};
        for (const [k, v] of Object.entries(parsed as Record<string, unknown>)) {
          if (typeof v === "number" && Number.isFinite(v)) out[k] = v;
        }
        return Object.keys(out).length > 0 ? out : null;
      }
    } catch {
      return null;
    }
  }
  return null;
}

export function repairOutcomeOf(r: MktRepair): Record<string, unknown> | null {
  if (r.outcome && typeof r.outcome === "object") return r.outcome;
  if (typeof r.outcome === "string" && r.outcome.trim() !== "") {
    try {
      const parsed: unknown = JSON.parse(r.outcome);
      return parsed && typeof parsed === "object" ? (parsed as Record<string, unknown>) : null;
    } catch {
      return null;
    }
  }
  return null;
}

/** Detail sections that exist on the payload drive the drawer — never placeholders. */
export function detailSections(detail: MktSeedDetail): string[] {
  const out: string[] = [];
  if (detail.dsl) out.push("DSL");
  if (detail.parameter_schema) out.push("parameter schema");
  if (detail.default_parameters) out.push("default parameters");
  if ((detail.lifecycle_events ?? []).length) out.push("lifecycle events");
  if ((detail.enablement ?? []).length) out.push("enablement");
  if ((detail.recent_scores ?? []).length) out.push("recent scores");
  if ((detail.recent_repairs ?? []).length) out.push("recent repairs");
  return out;
}
