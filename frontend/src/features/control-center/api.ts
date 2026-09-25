/**
 * Control Center: typed transport surface over @/api/client.
 *
 * Operator evidence routes (operator_routes.py) are READ-ONLY by
 * construction — the module does not even import the engine (INV-002).
 * Engine control used by the operator kill-switch style actions lives on the
 * canonical engine routes (/api/engine/toggle, /api/engine/mode) which are
 * reached through tradingApi-style send() with confirm guards.
 * Calibration: GET /api/operator/calibration (calibration_monitor.py).
 */

import { getLegacy, send } from "@/api/client";
import type {
  CalibrationDto,
  OperatorDecisionsDto,
  OperatorDecisionDetailDto,
  OperatorFunnelDto,
  OperatorNoTradeDto,
  OperatorOrdersDto,
  OperatorSummaryDto,
} from "./model";

const qs = (params: Record<string, string | number | boolean | undefined>): string => {
  const usp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== "") usp.set(k, String(v));
  const s = usp.toString();
  return s ? `?${s}` : "";
};

export const controlCenterApi = {
  summary: (signal?: AbortSignal): Promise<OperatorSummaryDto> => getLegacy<OperatorSummaryDto>("/api/operator/summary", signal),

  decisions: (f: { hours?: number; action?: string; gate?: string; search?: string; limit?: number }, signal?: AbortSignal): Promise<OperatorDecisionsDto> =>
    getLegacy<OperatorDecisionsDto>(`/api/operator/decisions${qs({ ...f, limit: f.limit ?? 100 })}`, signal),

  decisionDetail: (id: number, signal?: AbortSignal): Promise<OperatorDecisionDetailDto> =>
    getLegacy<OperatorDecisionDetailDto>(`/api/operator/decisions/${encodeURIComponent(String(id))}`, signal),

  funnel: (hours: number | undefined, signal?: AbortSignal): Promise<OperatorFunnelDto> =>
    getLegacy<OperatorFunnelDto>(`/api/operator/funnel${qs({ hours })}`, signal),

  noTrade: (hours: number | undefined, limit: number, signal?: AbortSignal): Promise<OperatorNoTradeDto> =>
    getLegacy<OperatorNoTradeDto>(`/api/operator/no-trade${qs({ hours, limit })}`, signal),

  orders: (limit: number, signal?: AbortSignal): Promise<OperatorOrdersDto> =>
    getLegacy<OperatorOrdersDto>(`/api/operator/orders${qs({ limit })}`, signal),

  calibration: (signal?: AbortSignal): Promise<CalibrationDto> => getLegacy<CalibrationDto>("/api/operator/calibration", signal),

  // Engine control (canonical routes; every call confirm-guarded in the UI).
  // The raw legacy body is normalized by controlCenterUseCases below —
  // `send` returns {success, engine_running}/{success, mode}; the shared
  // LegacyMutationResult contract (ok flag) is built here, not upstream.
  toggleEngine: (active: boolean): Promise<EngineToggleDto> => send<EngineToggleDto>("/api/engine/toggle", { active }),

  setMode: (mode: string): Promise<EngineModeDto> => send<EngineModeDto>("/api/engine/mode", { mode }),
};

export interface EngineToggleDto {
  success?: boolean;
  engine_running?: boolean;
  message?: string;
}

export interface EngineModeDto {
  success?: boolean;
  mode?: string;
  engine_running?: boolean;
  runtime_mode?: string;
  persisted?: boolean;
}
