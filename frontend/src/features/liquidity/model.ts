/**
 * Liquidity: DTO contracts + VO mappers (pure).
 *
 * Canonical payloads (news_liquidity_mslie_routes.py -> LiquidityGovernor):
 *  - state: enabled/available/status/calculation_status/source_status/
 *    causal_state/source/latency_ms/age_sec/last_update/features/model_compatibility
 *  - features: schema_id/dimension/timestamp/features{name->value}/pools[]
 *  - mslie status: engine_status/market_context/liquidity_map[]/last_sweep
 * Two orthogonal dimensions are kept separate per BUG-110 (calculation vs
 * source) — collapsing them into one "healthy" boolean is forbidden.
 */

export type Row = Record<string, unknown>;

export const str = (v: unknown): string | null => (typeof v === "string" && v ? v : typeof v === "number" ? String(v) : null);
export const num = (v: unknown): number | null =>
  typeof v === "number" && Number.isFinite(v) ? v : typeof v === "string" && v !== "" && Number.isFinite(Number(v)) ? Number(v) : null;
export const bool = (v: unknown): boolean | null => (typeof v === "boolean" ? v : null);
export const obj = (v: unknown): Row => (v && typeof v === "object" && !Array.isArray(v) ? (v as Row) : {});
export const arr = (v: unknown): Row[] => (Array.isArray(v) ? (v as Row[]) : []);

export interface LiquidityStateDto {
  success?: boolean;
  enabled?: boolean;
  available?: boolean;
  status?: string;
  calculation_status?: string;
  source_status?: string;
  causal_state?: string;
  source?: string;
  reason?: string;
  latency_ms?: number | null;
  age_sec?: number | null;
  last_update?: string | null;
  snapshot_timestamp?: string | null;
  error?: string | null;
  error_at?: string | null;
  schema?: Row;
  features?: Record<string, number>;
  feature_count?: number;
  feature_names?: string[];
  pools?: Row[];
  model_compatibility?: Row;
  algorithm_version?: string;
  state_revision?: number;
}

export interface LiquidityFeaturesDto {
  success?: boolean;
  schema_id?: string;
  dimension?: number;
  timestamp?: string | null;
  source?: string;
  available?: boolean;
  reason?: string;
  features?: Record<string, number>;
  pools?: Row[];
  feature_availability?: string;
}

export interface LiquidityToggleDto extends LiquidityStateDto {}

export interface MslieStatusDto {
  success?: boolean;
  available?: boolean;
  status?: string;
  reason?: string;
  engine_status?: Row;
  market_context?: Row | null;
  liquidity_map?: Row[];
  last_sweep?: Row | null;
  feature_vector?: Row | null;
  algorithm_version?: string;
}

export interface MslieFeaturesDto {
  success?: boolean;
  available?: boolean;
  reason?: string;
  vector?: Row | null;
}

/** Feature value row for the ten-value table (index/source come with value). */
export interface FeatureRowVo {
  name: string;
  value: number | null;
}

export function featureRows(features: Record<string, number> | undefined, order: string[] | undefined): FeatureRowVo[] {
  const names = order && order.length > 0 ? order : Object.keys(features ?? {});
  return names.map((n) => ({ name: n, value: num(obj(features)[n]) }));
}

/** Liquidity map zone -> heat row (probability_as_target drives the heat). */
export interface ZoneRowVo {
  price: number | null;
  side: string | null;
  timeframe: string | null;
  strength: number | null;
  tests: number | null;
  probability: number | null;
  rank: string | null;
  distance: number | null;
}

export function toZoneRow(row: Row): ZoneRowVo {
  return {
    price: num(row.price),
    side: str(row.side),
    timeframe: str(row.timeframe),
    strength: num(row.strength_score),
    tests: num(row.number_of_tests),
    probability: num(row.probability_as_target),
    rank: str(row.rank),
    distance: num(row.distance_from_price),
  };
}

export function toggleVerdict(res: LiquidityToggleDto): { ok: boolean; message: string } {
  if (bool(res.success) === false) {
    const err = obj(res.error);
    return { ok: false, message: str(err.code) ?? str(res.error) ?? str(res.reason) ?? "Backend refused the toggle." };
  }
  return {
    ok: true,
    message: `Liquidity Intelligence now ${res.enabled ? "ENABLED" : "DISABLED"} (source: ${res.source ?? "—"}, ${res.algorithm_version ?? "version not reported"})`,
  };
}
