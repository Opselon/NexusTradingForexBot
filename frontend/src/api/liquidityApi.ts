/**
 * liquidityApi — liquidity + MSLIE context (legacy raw JSON).
 *
 * Routes (news_liquidity_mslie_routes.py):
 *  GET  /api/liquidity/state | /api/liquidity/features
 *  POST /api/liquidity/toggle   {enabled} -> mutation result
 *  GET  /api/mslie/status | /api/mslie/features
 */

import { getLegacy, send } from "@/core/transport";
import type { LiquidityFeatures, LiquidityState, MslieStatus } from "@/types/features";
import type { LegacyMutationResult } from "@/types/api";

export const liquidityApi = {
  state: (signal?: AbortSignal): Promise<LiquidityState> =>
    getLegacy<LiquidityState>("/api/liquidity/state", signal),

  features: (signal?: AbortSignal): Promise<LiquidityFeatures> =>
    getLegacy<LiquidityFeatures>("/api/liquidity/features", signal),

  toggle: (enabled: boolean): Promise<LegacyMutationResult> =>
    send<LegacyMutationResult>("/api/liquidity/toggle", { enabled }),

  mslieStatus: (signal?: AbortSignal): Promise<MslieStatus> =>
    getLegacy<MslieStatus>("/api/mslie/status", signal),

  mslieFeatures: (signal?: AbortSignal): Promise<Record<string, unknown>> =>
    getLegacy<Record<string, unknown>>("/api/mslie/features", signal),
};
