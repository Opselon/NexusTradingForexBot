/**
 * Marketplace use cases — application services over features/marketplace/api.
 *
 * All reads are v1-envelope GETs (getV1 unwraps data, ApiError on {error}).
 * Commands return the backend payload; refusals arrive as thrown ApiErrors
 * (403/409/422), so callers must branch on success/error, never assume.
 */

import { marketplaceApi } from "./api";
import { toSeedVM, type SeedVM } from "./model";
import type { MktEnableMode, MktPack, MktSeedDetail } from "./types";

export interface SeedPage {
  seeds: SeedVM[];
  page: number;
  pageSize: number;
  hasMore: boolean;
}

export const marketplaceQueries = {
  packs: (signal?: AbortSignal): Promise<{ packs: MktPack[]; count: number }> =>
    marketplaceApi.packs(signal).then((res) => ({ packs: res.packs ?? [], count: res.count ?? (res.packs ?? []).length })),

  seeds: async (opts: { family?: string; status?: string; q?: string; page?: number; pageSize?: number }, signal?: AbortSignal): Promise<SeedPage> => {
    const res = await marketplaceApi.seeds(opts, signal);
    return {
      seeds: (res.items ?? []).map(toSeedVM),
      page: res.page ?? 1,
      pageSize: res.page_size ?? (opts.pageSize ?? 100),
      hasMore: !!res.has_more,
    };
  },

  seedDetail: (seedId: string, signal?: AbortSignal): Promise<MktSeedDetail> => marketplaceApi.seedDetail(seedId, signal),

  rankings: (dimension: string, signal?: AbortSignal) =>
    marketplaceApi.rankings(dimension, undefined, signal).then((res) => ({ rows: res.items ?? [], dimension: res.dimension, profileId: res.profile_id })),

  scoreHistory: (seedId: string, signal?: AbortSignal) => marketplaceApi.scoreHistory(seedId, { pageSize: 50 }, signal),

  repairs: (seedId?: string, signal?: AbortSignal) => marketplaceApi.repairs(seedId, signal).then((res) => res.items ?? []),

  runtimeSnapshot: (signal?: AbortSignal) => marketplaceApi.runtimeSnapshot(signal),
};

export const marketplaceCommands = {
  installPack: (packId: string, count: number) => marketplaceApi.installPack(packId, count),
  enableSeed: (seedId: string, mode: MktEnableMode) => marketplaceApi.enableSeed(seedId, mode),
  disableSeed: (seedId: string) => marketplaceApi.disableSeed(seedId),
  runResearch: (seedId: string) => marketplaceApi.runResearch(seedId),
  /** Repair: body {trigger}; an empty trigger is normalized server-side to "repair". */
  repairSeed: (seedId: string, trigger: string) => marketplaceApi.repairSeed(seedId, trigger),
};
