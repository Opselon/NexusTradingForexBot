/**
 * features/ai-providers/model.ts — TypeScript view of the AI provider ecosystem
 * (ECOSYSTEM-001).
 *
 * These types mirror the backend contract at
 * `src/nexus_scalp/ai_providers/` and the routes in
 * `src/nexus_scalp/web/ai_providers_routes.py`. The backend is the source of
 * truth (Section 61): the UI never derives provider state on its own, it only
 * renders what the API returns.
 *
 * TERMINOLOGY (Section 57): the UI says "AI Recommendation", "Policy Decision",
 * "Risk Decision" and "Execution Result". It never labels an AI as having
 * executed a trade.
 */

/** The six canonical actions a provider may recommend. */
export type AIProviderAction =
  | "HOLD"
  | "CLOSE"
  | "REDUCE"
  | "NO_ACTION"
  | "ADJUST_TP"
  | "ADJUST_SL";

/** Decision modes (Section 13). Exactly what the backend exposes. */
export type DecisionMode =
  | "DISABLED"
  | "INTERNAL_ONLY"
  | "EXTERNAL_ONLY"
  | "HYBRID"
  | "SHADOW"
  | "COMPARISON"
  | "FALLBACK"
  | "DETERMINISTIC_ONLY";

/** Normalized failure category (Section 5). Never collapsed to one string. */
export type ProviderErrorCategory =
  | "AUTH_FAILED"
  | "MODEL_UNAVAILABLE"
  | "INVALID_REQUEST"
  | "RATE_LIMITED"
  | "TIMEOUT"
  | "UPSTREAM_UNAVAILABLE"
  | "NETWORK"
  | "MALFORMED_RESPONSE"
  | "SCHEMA_VIOLATION"
  | "CIRCUIT_OPEN"
  | "UNKNOWN";

/** Circuit-breaker state (Section 31). */
export type CircuitBreakerState = "CLOSED" | "OPEN" | "HALF_OPEN";

export interface ProviderHealth {
  available: boolean;
  authenticated: boolean;
  model_available: boolean;
  latency_ms: number | null;
  last_success: string | null;
  last_failure: string | null;
  consecutive_failures: number;
  rate_limited: boolean;
  circuit_breaker_state: CircuitBreakerState;
  last_error: string | null;
}

/** One provider row (Sections 19, 31). */
export interface ProviderEntry {
  provider_id: string;
  provider_name: string;
  type: string;
  endpoint: string;
  auth_type: string;
  enabled: boolean;
  active: boolean;
  default_model: string | null;
  available_models: string[];
  capabilities: string[];
  timeout: number;
  max_retries: number;
  health_status: string | null;
  last_test: string | null;
  latency_ms: number | null;
  failure_count: number;
  last_error: string | null;
  last_success: string | null;
  rate_limit_state: string | null;
  circuit_breaker_state: CircuitBreakerState | null;
  configuration_version: number;
  /** Present only when a secret is configured; the VALUE never leaves the backend. */
  has_secret?: boolean;
  health?: ProviderHealth;
}

export interface ActivationEnvelope {
  status: string;
  activation: ActivationState;
}

export interface ActivationState {
  primary_provider: string;
  secondary_provider: string | null;
  fallback_provider: string | null;
  decision_mode: DecisionMode;
  shadow_provider: string | null;
  configuration_version: number;
  activated_at: string | null;
}

/**
 * Response of `GET /api/ai-providers` — the provider list plus the CURRENT
 * selection. The backend returns the selection FLAT at top level
 * (`decision_mode`, `active_provider`, …), not under an `activation` key;
 * `/activation` returns the same keys nested under `activation`.
 */
export interface ProviderListResponse {
  status: string;
  providers: ProviderEntry[];
  decision_mode: DecisionMode;
  active_provider: string | null;
  active_model: string | null;
  secondary_provider: string | null;
  fallback_provider: string | null;
  shadow_provider: string | null;
  /** `/activation` returns the same selection under this key. */
  activation?: ActivationState;
  template_version?: string;
  policy_version?: string;
  gate_version?: string;
  contract_version?: string;
}

/** Test Center result (Section 22). A TEST RESULT, never a live decision. */
export interface ProviderTestResult {
  provider_id: string;
  model: string | null;
  passed: boolean;
  stage: string;
  detail: string;
  latency_ms: number;
  raw_response: string | null;
  normalized_response: Record<string, unknown> | null;
  input_tokens: number | null;
  output_tokens: number | null;
  cost_estimate: number | null;
  error: string | null;
  is_test_data: boolean;
}

export interface DecisionEvidenceDto {
  action: AIProviderAction;
  confidence: number;
  p_hold: number;
  p_close: number;
  p_reduce: number;
  expected_remaining_r: number;
  expected_downside_r: number;
  expected_upside_r: number;
  regime_change_probability: number;
  uncertainty: number;
}

export interface PolicyScoresDto {
  scores: Record<string, number>;
  winner: string;
  margin: number;
  expected_hold_value: number;
  expected_close_value: number;
  terms: Record<string, number | string>;
  policy_version: string;
  reason_codes: string[];
}

export interface RiskGateDto {
  allowed: boolean;
  tp_allowed: boolean;
  sl_allowed: boolean;
  sl_kind: "TIGHTEN_SL" | "KEEP_SL" | "WIDEN_SL";
  final_tp: number | null;
  final_sl: number | null;
  rejections: string[];
  warnings: string[];
  gate_version: string;
  risk_change_usd: number;
}

/** One full decision trace (Sections 41, 64). */
export interface DecisionRecord {
  decision_id: string;
  final_action: AIProviderAction;
  policy: PolicyScoresDto;
  risk: RiskGateDto;
  evidence: Record<string, Record<string, unknown>>;
  providers_used: string[];
  providers_failed: string[];
  fallback_used: boolean;
  fallback_reason: string;
  decision_mode: DecisionMode;
  versions: {
    template: string;
    policy: string;
    gate: string;
    decision: string;
    contract: string;
    context: string;
  };
  created_at: string;
  latency_ms: number;
  stage_timings_ms: Record<string, number>;
  /** True only when the decision was made on SIMULATED TEST DATA. */
  is_test_data: boolean;
}

export interface EvaluateResponse {
  decision: DecisionRecord;
  request: Record<string, unknown>;
  is_test_data: boolean;
}

export interface ComparisonEntry {
  provider_id: string;
  model: string | null;
  ok: boolean;
  action: AIProviderAction | null;
  p_hold: number | null;
  p_close: number | null;
  expected_remaining_r: number | null;
  uncertainty: number | null;
  latency_ms: number | null;
  error_category: ProviderErrorCategory | null;
  error: string | null;
}

export interface CompareResponse {
  status: string;
  snapshot_id: string;
  providers: ComparisonEntry[];
  is_test_data: boolean;
}

export interface RestartEntry {
  field: string;
  classification: "HOT_APPLY" | "RESTART_REQUIRED";
  reason: string;
}

export interface ModelInfo {
  id: string;
  name: string;
  context_window: number | null;
  supports_structured_output: boolean;
}

/** Configuration the UI may send (Section 20). Secrets are set separately. */
export interface ProviderConfigRequest {
  provider_name?: string;
  endpoint?: string;
  model?: string;
  timeout?: number;
  max_retries?: number;
  enabled?: boolean;
  secret_name?: string;
}

export interface SwitchRequest {
  primary: string;
  secondary?: string | null;
  fallback?: string | null;
  mode?: DecisionMode;
}

export interface SwitchResponse {
  switched: boolean;
  active_provider: string;
  active_model: string | null;
  decision_mode: DecisionMode;
  activated_at: string;
  configuration_version: number;
  restart_required: boolean;
  preconditions: Array<{ check: string; passed: boolean; detail: string }>;
  warnings: string[];
}
