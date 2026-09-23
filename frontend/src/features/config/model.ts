/**
 * Config: DTO -> form VO mappers + the typed FieldSpec tables (pure).
 *
 * The whole settings surface is spec-driven: `runtimeConfigSpecs()` is the
 * single source for label/kind/bounds/mutability, `configBaseline()` flattens
 * the GET /api/config answer into editable values, and `dottedPayload()`
 * turns validated changes into the `{ "section.key": value }` map that
 * POST /api/runtime-config/apply consumes. Invalid payloads can never be
 * built here — the caller must pass validation first (see useCases).
 */

import {
  MUTABILITY,
  type FieldSpec,
  type FieldValues,
  type Mutability,
} from "@/features/config/validation";
import type { ConfigDto } from "./api";

/* Execution modes — mirrors ExecutionMode (domain/enums.py) and the
 * backend v1 _VALID_MODES set. SIMULATION is a legacy alias handled by the
 * server (BUG-148); we only send canonical values. */
export const EXECUTION_MODES = ["BACKTEST", "REPLAY", "PAPER", "SHADOW", "LIVE"] as const;

/** Conservative mirror of the server's allowed-transition matrix
 * (api_v1/runtime.py _ALLOWED_TRANSITIONS). The SERVER remains authoritative;
 * this only stops obviously-illegal clicks before the wire (EDD: 2026-09 main). */
export const MODE_TRANSITIONS: Record<string, readonly string[]> = {
  BACKTEST: ["REPLAY", "PAPER", "SHADOW", "LIVE"],
  REPLAY: ["PAPER", "SHADOW"],
  PAPER: ["LIVE", "SHADOW", "REPLAY"],
  SHADOW: ["LIVE", "PAPER"],
  LIVE: ["PAPER", "SHADOW"],
};

export function allowedModes(current: string | null | undefined): readonly string[] {
  if (!current) return EXECUTION_MODES;
  const direct = MODE_TRANSITIONS[current.toUpperCase()];
  if (!direct) return EXECUTION_MODES;
  return Array.from(new Set([current.toUpperCase(), ...direct]));
}

export interface ModeCheck {
  ok: boolean;
  errors: string[];
  warnings: string[];
}

/** Pure client-side transition verdict (mirrors v1 /mode/preview semantics). */
export function checkModeTransition(current: string | null | undefined, proposed: string): ModeCheck {
  const errors: string[] = [];
  const warnings: string[] = [];
  const p = proposed.trim().toUpperCase();
  if (!EXECUTION_MODES.includes(p as (typeof EXECUTION_MODES)[number])) {
    errors.push(`mode must be one of ${EXECUTION_MODES.join(", ")}`);
    return { ok: false, errors, warnings };
  }
  const c = (current ?? "").toUpperCase();
  if (c && p === c) warnings.push("proposed mode equals current mode (no-op)");
  else if (c && !allowedModes(c).includes(p)) errors.push(`transition ${c} -> ${p} is not allowed`);
  if (p === "LIVE") warnings.push("LIVE places real orders with the broker — capital at risk");
  return { ok: errors.length === 0, errors, warnings };
}

/* ------------------------------------------------------------------ */
/* Runtime-config form specs (dotted keys = apply payload keys)        */
/* ------------------------------------------------------------------ */

export interface SpecWithMutability extends FieldSpec {
  mutability: Mutability;
  section: string;
}

const S = (
  key: string,
  label: string,
  kind: FieldSpec["kind"],
  mutability: Mutability,
  extra: Partial<FieldSpec> = {},
): SpecWithMutability => ({
  key,
  label,
  kind,
  mutability,
  section: key.split(".")[0] ?? "",
  required: true,
  ...extra,
});

export function runtimeConfigSpecs(): SpecWithMutability[] {
  return [
    S("execution.symbol", "Symbol", "string", MUTABILITY.RESTART_REQUIRED, {
      pattern: "^[A-Z0-9]{2,16}$",
      patternMessage: "symbol looks wrong (expected an uppercase instrument code like XAUUSD)",
    }),
    S("execution.timeframe", "Timeframe", "enum", MUTABILITY.RESTART_REQUIRED, {
      options: ["M1", "M5", "M15", "M30", "H1", "H4", "D1"],
    }),
    S("execution.magic_number", "Magic number", "integer", MUTABILITY.RESTART_REQUIRED, { min: 0, max: 2_147_483_647 }),
    S("execution.max_slippage_points", "Max slippage (points)", "integer", MUTABILITY.HOT_RESTRICTED, { min: 0, max: 10_000 }),
    S("risk.max_account_drawdown_pct", "Max account drawdown %", "number", MUTABILITY.HOT_RESTRICTED, { min: 0.1, max: 100 }),
    S("risk.risk_per_trade_pct", "Risk per trade %", "number", MUTABILITY.HOT_RESTRICTED, { min: 0.01, max: 100 }),
    S("risk.max_concurrent_positions", "Max concurrent positions", "integer", MUTABILITY.HOT_RESTRICTED, { min: 0, max: 500 }),
    S("risk.max_spread_points", "Max spread (points)", "integer", MUTABILITY.HOT_RESTRICTED, { min: 0, max: 100_000 }),
    S("risk.max_allowed_lots", "Max allowed lots", "number", MUTABILITY.HOT_RESTRICTED, { min: 0.01, max: 1000 }),
    S("risk.enforce_stop_loss", "Enforce stop-loss", "boolean", MUTABILITY.HOT_RESTRICTED),
    S("model.confidence_threshold", "Confidence threshold", "number", MUTABILITY.HOT_RESTRICTED, { min: 0, max: 1 }),
    S("model.model_artifact_path", "Model artifact path", "path", MUTABILITY.RESTART_REQUIRED, {
      pattern: "\\.(pt|onnx|joblib|pkl)$",
      patternMessage: "artifact must be a .pt / .onnx / .joblib / .pkl file",
    }),
  ];
}

/** Flatten GET /api/config into dotted editable values (empty for missing). */
export function configBaseline(cfg: ConfigDto): FieldValues {
  const e = cfg.execution ?? {};
  const r = cfg.risk ?? {};
  const m = cfg.model ?? {};
  return {
    "execution.symbol": e.symbol ?? "",
    "execution.timeframe": e.timeframe ?? "M1",
    "execution.magic_number": e.magic_number ?? 0,
    "execution.max_slippage_points": e.max_slippage_points ?? 0,
    "risk.max_account_drawdown_pct": r.max_account_drawdown_pct ?? 0,
    "risk.risk_per_trade_pct": r.risk_per_trade_pct ?? 0,
    "risk.max_concurrent_positions": r.max_concurrent_positions ?? 0,
    "risk.max_spread_points": r.max_spread_points ?? 0,
    "risk.max_allowed_lots": r.max_allowed_lots ?? 0,
    "risk.enforce_stop_loss": r.enforce_stop_loss ?? true,
    "model.confidence_threshold": m.confidence_threshold ?? 0,
    "model.model_artifact_path": m.model_artifact_path ?? "",
  };
}

/** Section names for the form's grouping headers. */
export function specSections(specs: readonly SpecWithMutability[]): string[] {
  return Array.from(new Set(specs.map((s) => s.section)));
}
