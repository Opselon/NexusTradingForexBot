/**
 * Liquidity: typed transport surface over @/api/client.
 *
 * Routes verified in src/nexus_scalp/web/news_liquidity_mslie_routes.py:
 *   GET  /api/liquidity/state     (governor report — canonical status)
 *   GET  /api/liquidity/features  (ten real values + schema/pools)
 *   POST /api/liquidity/toggle    {enabled: bool} -> new report
 *   GET  /api/mslie/status        (market-structure engine debug status)
 *   GET  /api/mslie/features      (MarketIntelligenceFeatureVectorV1)
 */

import { getLegacy, send } from "@/api/client";
import type { LiquidityFeaturesDto, LiquidityStateDto, LiquidityToggleDto, MslieFeaturesDto, MslieStatusDto } from "./model";

export const liquidityApi = {
  state: (signal?: AbortSignal): Promise<LiquidityStateDto> => getLegacy<LiquidityStateDto>("/api/liquidity/state", signal),

  features: (signal?: AbortSignal): Promise<LiquidityFeaturesDto> => getLegacy<LiquidityFeaturesDto>("/api/liquidity/features", signal),

  mslieStatus: (signal?: AbortSignal): Promise<MslieStatusDto> => getLegacy<MslieStatusDto>("/api/mslie/status", signal),

  mslieFeatures: (signal?: AbortSignal): Promise<MslieFeaturesDto> => getLegacy<MslieFeaturesDto>("/api/mslie/features", signal),

  toggle: (enabled: boolean): Promise<LiquidityToggleDto> => send<LiquidityToggleDto>("/api/liquidity/toggle", { enabled }),
};
