/**
 * Rules — typed transport for the trading-rules bounded context.
 *
 * Endpoints verified in src/nexus_scalp/web/diagnostics_state_routes.py:
 *  GET  /api/rules         — raw list[{rule_name,is_enabled,category,parameters}]
 *  POST /api/rules/toggle  — ToggleRuleRequest -> {success: bool}
 *
 * The feature keeps its own typed surface over the stable `@/api/client`
 * transport (lane-1 owns src/api/* modules — no import races).
 */

import { getLegacy, send } from "@/api/client";

/** Raw row from `AuditRepository.get_trading_rules()` — parameters is a JSON
 *  STRING as stored in trading_rules_config; parse failures stay visible. */
export interface RuleDto {
  rule_name: string;
  is_enabled: boolean | number;
  category: string | null;
  parameters: string | Record<string, unknown> | null;
}

/** Backend answer for POST /api/rules/toggle. HTTP 200 + success:false is a
 *  REFUSAL (safe_error_payload family) — the caller must not assume success. */
export interface RuleToggleResult {
  success?: boolean;
  available?: boolean;
  error?: { code?: string; message?: string; request_id?: string };
  reason?: string;
  message?: string;
}

export interface ToggleRulePayload {
  rule_name: string;
  is_enabled: boolean;
  /** JSON dict to persist alongside the enable flag, or null = leave stored. */
  parameters: Record<string, unknown> | null;
}

export const rulesApi = {
  list: (signal?: AbortSignal): Promise<RuleDto[]> => getLegacy<RuleDto[]>("/api/rules", signal),

  toggle: (payload: ToggleRulePayload): Promise<RuleToggleResult> =>
    send<RuleToggleResult>("/api/rules/toggle", payload),
};
