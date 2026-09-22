/**
 * Rules: DTO -> domain VO mappers + pure filter/sort logic (unit-testable).
 *
 * Invariants enforced HERE (not in components):
 *  - `parameters` arrives as a JSON string from SQLite; a broken string must
 *    surface as a visible parse error on the row, never as a silently empty
 *    parameter set (backend-authoritative display).
 *  - enablement is boolean-coerced from the stored int; unknown -> false with
 *    the raw value kept for display.
 *  - Filtering/sorting is pure and total: unknown columns keep input order.
 */

import { toFiniteNumber, type FieldErrors, type FieldSpec, validateFields } from "@/features/config/validation";
import { useI18n } from "@/stores/i18nStore";
import type { RuleDto } from "./api";

export type RuleParamKind = "number" | "boolean" | "string";

export interface RuleParamVO {
  key: string;
  /** Display value (stringified; booleans as "true"/"false"). */
  value: string;
  kind: RuleParamKind;
  /** True when the key looks threshold-shaped (min/max/pct/limit/threshold). */
  threshold: boolean;
}

export interface RuleVO {
  name: string;
  enabled: boolean;
  rawEnabled: unknown;
  category: string;
  params: RuleParamVO[];
  /** Set when the stored parameters JSON could not be parsed. */
  paramsError: string | null;
}

const THRESHOLDISH = /(threshold|min|max|limit|pct|percent|buffer|window|lookback|ratio)/i;

export function isThresholdKey(key: string): boolean {
  return THRESHOLDISH.test(key);
}

function paramFromEntry(key: string, value: unknown): RuleParamVO {
  if (typeof value === "boolean") {
    return { key, value: value ? "true" : "false", kind: "boolean", threshold: false };
  }
  if (typeof value === "number" && Number.isFinite(value)) {
    return { key, value: String(value), kind: "number", threshold: isThresholdKey(key) };
  }
  return { key, value: value === null || value === undefined ? "—" : String(value), kind: "string", threshold: isThresholdKey(key) };
}

export function mapRuleDto(dto: RuleDto): RuleVO {
  let params: RuleParamVO[] = [];
  let paramsError: string | null = null;
  const raw = dto.parameters;
  if (typeof raw === "string" && raw.trim() !== "") {
    try {
      const parsed: unknown = JSON.parse(raw);
      if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
        params = Object.entries(parsed as Record<string, unknown>).map(([k, v]) => paramFromEntry(k, v));
      } else {
        paramsError = useI18n.getState().t("rules.model.not_object", "stored parameters are not a JSON object");
      }
    } catch (e) {
      paramsError = e instanceof Error ? e.message : useI18n.getState().t("rules.model.unparseable", "unparseable JSON");
    }
  } else if (raw && typeof raw === "object") {
    params = Object.entries(raw).map(([k, v]) => paramFromEntry(k, v));
  }
  return {
    name: String(dto.rule_name ?? ""),
    enabled: dto.is_enabled === true || dto.is_enabled === 1,
    rawEnabled: dto.is_enabled,
    category: String(dto.category ?? useI18n.getState().t("rules.model.uncategorized", "UNCATEGORIZED")),
    params,
    paramsError,
  };
}

/** Parameters of a rule as editable FieldSpecs — one per stored entry, kinds
 *  inherited from the ORIGINAL backend value (a number never becomes text). */
export function paramSpecs(rule: RuleVO): FieldSpec[] {
  return rule.params.map((p) => {
    const spec: FieldSpec = {
      key: p.key,
      label: p.key,
      kind: p.kind === "number" ? "number" : p.kind === "boolean" ? "boolean" : "string",
      required: true,
    };
    if (p.kind === "number") {
      const n = toFiniteNumber(p.value);
      // Guard against nonsense, not against the operator's intent: thresholds
      // are finite and non-negative for this engine's rule set.
      if (n !== null && n >= 0) spec.min = 0;
    }
    return spec;
  });
}

/** Validate proposed parameter edits. Invalid payloads are NEVER sent. */
export function validateParamEdits(rule: RuleVO, proposed: Record<string, string | boolean>): FieldErrors {
  const values: Record<string, string | boolean> = {};
  for (const spec of paramSpecs(rule)) {
    const v = proposed[spec.key];
    values[spec.key] = v === undefined ? (rule.params.find((p) => p.key === spec.key)?.value ?? "") : v;
  }
  return validateFields(paramSpecs(rule), values);
}

/** Coerce validated edits back to the JSON shape the backend stores. */
export function paramPayload(
  rule: RuleVO,
  proposed: Record<string, string | boolean>,
): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const p of rule.params) {
    const raw = proposed[p.key];
    if (p.kind === "boolean") out[p.key] = raw === true || raw === "true";
    else if (p.kind === "number") out[p.key] = toFiniteNumber(typeof raw === "string" ? raw : null) ?? 0;
    else out[p.key] = raw === undefined ? p.value : String(raw);
  }
  return out;
}

/* ------------------------------------------------------------------ */
/* Search / filter / sort (pure)                                       */
/* ------------------------------------------------------------------ */

export type RuleSortKey = "name" | "category" | "status";
export interface RuleSort {
  key: RuleSortKey;
  dir: "asc" | "desc";
}
export type RuleStatusFilter = "all" | "enabled" | "disabled";

export function filterRules(
  rules: readonly RuleVO[],
  opts: { query: string; category: string; status: RuleStatusFilter },
): RuleVO[] {
  const q = opts.query.trim().toLowerCase();
  return rules.filter((r) => {
    if (opts.category !== "all" && r.category !== opts.category) return false;
    if (opts.status === "enabled" && !r.enabled) return false;
    if (opts.status === "disabled" && r.enabled) return false;
    if (q === "") return true;
    if (r.name.toLowerCase().includes(q) || r.category.toLowerCase().includes(q)) return true;
    return r.params.some((p) => p.key.toLowerCase().includes(q) || p.value.toLowerCase().includes(q));
  });
}

export function sortRules(rules: readonly RuleVO[], sort: RuleSort): RuleVO[] {
  const factor = sort.dir === "asc" ? 1 : -1;
  return [...rules].sort((a, b) => {
    const av = sort.key === "status" ? (a.enabled ? "1" : "0") : sort.key === "category" ? a.category : a.name;
    const bv = sort.key === "status" ? (b.enabled ? "1" : "0") : sort.key === "category" ? b.category : b.name;
    return av.localeCompare(bv) * factor;
  });
}

export function ruleCategories(rules: readonly RuleVO[]): string[] {
  return Array.from(new Set(rules.map((r) => r.category))).sort();
}
