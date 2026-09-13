/**
 * rulesApi — trading-rule inventory + enable/disable (legacy raw JSON).
 *
 * Routes: GET /api/rules (list[dict] from AuditRepository.get_trading_rules),
 * POST /api/rules/toggle ({rule_name, is_enabled, parameters?} -> {success}).
 * Backend refreshes the rule-matrix cache on a successful toggle — treat the
 * list as authoritative and re-fetch after a mutation.
 */

import { getLegacy, send } from "@/core/transport";
import type { RuleTogglePayload, RuleToggleResult, TradingRuleRow } from "@/types/features";

export const rulesApi = {
  list: (signal?: AbortSignal): Promise<TradingRuleRow[]> =>
    getLegacy<TradingRuleRow[]>("/api/rules", signal),

  toggle: (payload: RuleTogglePayload): Promise<RuleToggleResult> =>
    send<RuleToggleResult>("/api/rules/toggle", payload),
};
