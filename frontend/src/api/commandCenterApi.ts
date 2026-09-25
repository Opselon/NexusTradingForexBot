/**
 * commandCenterApi — fleet command center (legacy raw JSON).
 *
 * Routes (command_center_integration.py) — read-only surfaces; the console
 * NEVER commands a strategy directly (backend-authoritative, UI_WAVE_SPEC):
 *  GET /api/command-center/overview | /fleet | /spatial
 *  GET /api/command-center/inspector/{strategy_id}
 *  GET /api/command-center/timeline/{strategy_id}
 *  GET /api/command-center/execution-safety/{strategy_id}
 *  GET /api/command-center/timemachine/bounds | /timemachine/frame
 * Payloads are free-form backend dicts — narrow in the feature model layer.
 */

import { getLegacy } from "@/core/transport";
import { toQuery } from "@/core/config";
import type { CommandCenterFleet, CommandCenterOverview } from "@/types/features";

const C = "/api/command-center";

export const commandCenterApi = {
  overview: (signal?: AbortSignal): Promise<CommandCenterOverview> =>
    getLegacy<CommandCenterOverview>(`${C}/overview`, signal),

  fleet: (signal?: AbortSignal): Promise<CommandCenterFleet> =>
    getLegacy<CommandCenterFleet>(`${C}/fleet`, signal),

  spatial: (signal?: AbortSignal): Promise<Record<string, unknown>> =>
    getLegacy<Record<string, unknown>>(`${C}/spatial`, signal),

  inspector: (strategyId: string, signal?: AbortSignal): Promise<Record<string, unknown>> =>
    getLegacy<Record<string, unknown>>(`${C}/inspector/${encodeURIComponent(strategyId)}`, signal),

  timeline: (strategyId: string, signal?: AbortSignal): Promise<Record<string, unknown>> =>
    getLegacy<Record<string, unknown>>(`${C}/timeline/${encodeURIComponent(strategyId)}`, signal),

  executionSafety: (strategyId: string, signal?: AbortSignal): Promise<Record<string, unknown>> =>
    getLegacy<Record<string, unknown>>(`${C}/execution-safety/${encodeURIComponent(strategyId)}`, signal),

  timeMachineBounds: (signal?: AbortSignal): Promise<Record<string, unknown>> =>
    getLegacy<Record<string, unknown>>(`${C}/timemachine/bounds`, signal),

  timeMachineFrame: (params: { t?: string | number; strategy_id?: string } = {}, signal?: AbortSignal): Promise<Record<string, unknown>> =>
    getLegacy<Record<string, unknown>>(`${C}/timemachine/frame${toQuery({ ...params })}`, signal),
};
