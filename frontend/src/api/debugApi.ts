/**
 * debugApi — developer/diagnostic surface (legacy raw JSON).
 *
 * Routes (debug_research_routes.py): GET /api/debug/health,
 * GET /api/debug/features, POST /api/debug/model-test,
 * GET /api/forensics/health, GET /api/forensics/deploy-gate (also exposed
 * through governanceApi for the governance feature's grouping).
 */

import { getLegacy, send } from "@/core/transport";
import type { DebugFeatures, DebugHealth, DeployGateResult, ForensicsHealth, ModelTestPayload } from "@/types/features";

export const debugApi = {
  health: (signal?: AbortSignal): Promise<DebugHealth> =>
    getLegacy<DebugHealth>("/api/debug/health", signal),

  features: (signal?: AbortSignal): Promise<DebugFeatures> =>
    getLegacy<DebugFeatures>("/api/debug/features", signal),

  modelTest: (payload: ModelTestPayload): Promise<Record<string, unknown>> =>
    send<Record<string, unknown>>("/api/debug/model-test", payload),

  forensicsHealth: (signal?: AbortSignal): Promise<ForensicsHealth> =>
    getLegacy<ForensicsHealth>("/api/forensics/health", signal),

  deployGate: (signal?: AbortSignal): Promise<DeployGateResult> =>
    getLegacy<DeployGateResult>("/api/forensics/deploy-gate", signal),
};
