/**
 * riskApi — risk + guardian state.
 *
 *  GET /api/v1/risk/status   — last proposal risk_checks + engine risk config
 *  GET /api/v1/risk/summary  — aggregate exposure + margin metrics
 *  GET /api/debug/state      — runtime_risk_state / kill switch / halt reason
 *                              (debug_snapshot._risk_section — real backend
 *                              state, not client-side heuristics)
 */

import { getV1, getLegacy } from "./client";
import type { V1RiskStatus, V1RiskSummary, RuntimeRiskState } from "@/types/domain";

interface DebugStateShape {
  available?: boolean;
  reason?: string;
  risk?: RuntimeRiskState;
}

export const riskApi = {
  status: (signal?: AbortSignal): Promise<V1RiskStatus> => getV1<V1RiskStatus>("/api/v1/risk/status", signal),

  summary: (signal?: AbortSignal): Promise<V1RiskSummary> => getV1<V1RiskSummary>("/api/v1/risk/summary", signal),

  runtimeRiskState: (signal?: AbortSignal): Promise<RuntimeRiskState | null> =>
    getLegacy<DebugStateShape>("/api/debug/state", signal).then(
      (r) => (r.available && r.risk ? r.risk : null),
    ),
};
