/**
 * marketplaceApi — seed marketplace (ALL routes are v1 envelope).
 *
 * Routes per shared/ROUTES.txt (web/api_v1/marketplace.py):
 *  GET  /api/v1/marketplace/seeds                        paginated page shape
 *  GET  /api/v1/marketplace/packs                        {packs,count}
 *  GET  /api/v1/marketplace/rankings                     ranked rows
 *  GET  /api/v1/marketplace/repairs                      repair attempts
 *  GET  /api/v1/marketplace/scores/{seed_id}/history     14-factor history
 *  POST /api/v1/marketplace/packs/{pack_id}/install      body {count?}
 *  POST /api/v1/marketplace/seeds/{seed_id}/enable       body {mode}
 *  POST /api/v1/marketplace/seeds/{seed_id}/disable      no body
 * v1 success = {data,meta} on GET and POST alike — unwrap via v1Send.
 * PENDING/DENIED enable semantics stay backend-authoritative (200/202 vs 403
 * via the error envelope -> ApiError); never grant silently in the client.
 */

import { getV1, sendV1 } from "@/core/transport";
import { toQuery } from "@/core/config";
import type { V1Page } from "@/types/domain";
import type {
  MarketplacePack,
  MarketplaceRankingRow,
  MarketplaceRepair,
  MarketplaceSeed,
  SeedScorePoint,
} from "@/types/features";

const BASE = "/api/v1/marketplace";

/** v1 mutations return the same {data,meta} envelope — unwrap here once. */
async function v1Send<T>(path: string, body?: unknown): Promise<T> {
  const env = await sendV1<{ data: T } & Record<string, unknown>>(path, body);
  return env.data;
}

export interface PackCatalog {
  packs: MarketplacePack[];
  count?: number;
}

export const marketplaceApi = {
  seeds: (
    params: { family?: string; status?: string; q?: string; page?: number; page_size?: number } = {},
    signal?: AbortSignal,
  ): Promise<V1Page<MarketplaceSeed>> =>
    getV1<V1Page<MarketplaceSeed>>(`${BASE}/seeds${toQuery({ ...params })}`, signal),

  packs: (signal?: AbortSignal): Promise<PackCatalog> =>
    getV1<PackCatalog>(`${BASE}/packs`, signal),

  rankings: (
    params: { dimension?: string; profile_id?: string } = {},
    signal?: AbortSignal,
  ): Promise<{ items: MarketplaceRankingRow[]; dimension?: string; profile_id?: string; has_more?: boolean }> =>
    getV1(`${BASE}/rankings${toQuery({ ...params })}`, signal),

  repairs: (params: { seed_id?: string } = {}, signal?: AbortSignal): Promise<{ items: MarketplaceRepair[] }> =>
    getV1<{ items: MarketplaceRepair[] }>(`${BASE}/repairs${toQuery({ ...params })}`, signal),

  scoreHistory: (
    seedId: string,
    params: { page?: number; page_size?: number } = {},
    signal?: AbortSignal,
  ): Promise<V1Page<SeedScorePoint>> =>
    getV1<V1Page<SeedScorePoint>>(`${BASE}/scores/${encodeURIComponent(seedId)}/history${toQuery({ ...params })}`, signal),

  installPack: (packId: string, count?: number): Promise<Record<string, unknown>> =>
    v1Send<Record<string, unknown>>(`${BASE}/packs/${encodeURIComponent(packId)}/install`, count ? { count } : {}),

  enableSeed: (seedId: string, mode: string): Promise<Record<string, unknown>> =>
    v1Send<Record<string, unknown>>(`${BASE}/seeds/${encodeURIComponent(seedId)}/enable`, { mode }),

  disableSeed: (seedId: string): Promise<Record<string, unknown>> =>
    v1Send<Record<string, unknown>>(`${BASE}/seeds/${encodeURIComponent(seedId)}/disable`),
};
