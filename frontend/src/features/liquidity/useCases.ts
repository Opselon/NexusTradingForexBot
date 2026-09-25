/**
 * Liquidity: application use cases.
 *
 * The toggle is confirm-guarded at the UI and authoritative at the backend:
 * POST /api/liquidity/toggle persists via SettingsService (HOT_RESTRICTED)
 * and returns the new report — which the UI then re-reads (invalidates),
 * so the rendered enabled/disabled state is always the backend's.
 */

import { liquidityApi } from "./api";
import type { LiquidityFeaturesDto, LiquidityStateDto, MslieFeaturesDto, MslieStatusDto } from "./model";

export interface LiquidityReads {
  state: LiquidityStateDto;
  features: LiquidityFeaturesDto;
  mslieStatus: MslieStatusDto;
  mslieFeatures: MslieFeaturesDto;
}

export const liquidityQueries = {
  state: (signal?: AbortSignal) => liquidityApi.state(signal),
  features: (signal?: AbortSignal) => liquidityApi.features(signal),
  mslieStatus: (signal?: AbortSignal) => liquidityApi.mslieStatus(signal),
  mslieFeatures: (signal?: AbortSignal) => liquidityApi.mslieFeatures(signal),
};

export const liquidityUseCases = {
  toggle: (enabled: boolean) => liquidityApi.toggle(enabled),
};
