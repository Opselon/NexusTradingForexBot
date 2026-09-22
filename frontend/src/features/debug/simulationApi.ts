/**
 * Simulation & observability — legacy "Interactive Live Simulation" +
 * telegram worker telemetry parity.
 *
 *  - POST /api/simulation/tick        — inject a mock tick into the engine
 *  - GET  /api/observability/stats    — notifier queue + truthful worker health
 *
 * Server: src/nexus_scalp/web/server.py (@app decorators).
 *
 * SAFETY (forensic hardening, server-side): the tick endpoint is a NO-OP
 * unless execution mode is explicitly SIMULATION/PAPER — synthetic prices
 * can never masquerade as production telemetry. The UI surfaces the
 * server's own refusal message verbatim rather than implying success.
 */

import { getLegacy, send } from "@/api/client";

export type SimulationTickType = "BUY_PRESSURE" | "SELL_PRESSURE" | "VOLATILE_SWEEP";

export interface SimulationTickResponse {
  success: boolean;
  message?: string;
  [k: string]: unknown;
}

/** /api/observability/stats -> notifier.health_state() (BUG-072: truthful). */
export interface TelegramWorkerHealth {
  status?: string;
  sent_count?: number;
  failed_count?: number;
  retry_count?: number;
  failure_category?: string;
  last_success?: string;
  [k: string]: unknown;
}

export interface ObservabilityStatsResponse {
  tg_enabled: boolean;
  tg_queue: number;
  telegram: TelegramWorkerHealth;
  [k: string]: unknown;
}

export const simulationApi = {
  /** POST /api/simulation/tick — the server decides if injection is allowed. */
  injectTick: (type: SimulationTickType): Promise<SimulationTickResponse> =>
    send<SimulationTickResponse>("/api/simulation/tick", { type }),
};

export const observabilityApi = {
  /** GET /api/observability/stats — queue depth + live worker telemetry. */
  stats: (signal?: AbortSignal): Promise<ObservabilityStatsResponse> =>
    getLegacy<ObservabilityStatsResponse>("/api/observability/stats", signal),
};
