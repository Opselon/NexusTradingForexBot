/**
 * engineApi — engine lifecycle + canonical snapshot + MT5 status.
 *
 * Endpoints (verified in src/nexus_scalp/web/):
 *  GET  /api/status            — canonical get_system_state() snapshot
 *  GET  /api/mt5/status        — real broker connection/account/positions
 *  POST /api/engine/toggle     — {active: bool} start/stop the engine loop
 *  POST /api/engine/mode       — {mode: "PAPER"|"LIVE"|...} mode switch
 *  GET  /api/v1/system/status  — health verdict + version + mode
 */

import { getLegacy, getV1, send } from "./client";
import type { EngineSnapshot, MT5Status } from "@/types/domain";
import type { LegacyMutationResult } from "@/types/api";

export interface SystemStatusV1 {
  health_verdict: string;
  critical_failures: string[];
  version: { product: string | null; version: string | null; commit: string | null; channel: string | null };
  runtime: { engine_attached: boolean; engine_running: boolean; mode: string | null; freshness_overall: string | null };
  checks_count: number;
}

export const engineApi = {
  snapshot: (signal?: AbortSignal): Promise<EngineSnapshot> => getLegacy<EngineSnapshot>("/api/status", signal),

  mt5Status: (signal?: AbortSignal): Promise<MT5Status> => getLegacy<MT5Status>("/api/mt5/status", signal),

  systemStatus: (signal?: AbortSignal): Promise<SystemStatusV1> =>
    getV1<SystemStatusV1>("/api/v1/system/status", signal),

  toggleEngine: (active: boolean): Promise<LegacyMutationResult> =>
    send<LegacyMutationResult>("/api/engine/toggle", { active }),

  setMode: (mode: string): Promise<LegacyMutationResult> =>
    send<LegacyMutationResult>("/api/engine/mode", { mode }),
};
