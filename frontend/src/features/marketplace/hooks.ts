/**
 * Marketplace hooks — TanStack Query bindings over the marketplace use cases.
 *
 * Keys are namespaced ["marketplace", …]. Mutations invalidate the whole
 * namespace because installs/repairs change packs, seeds, rankings and the
 * runtime snapshot together; the refetch is the source of truth.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { marketplaceCommands, marketplaceQueries } from "./useCases";
import type { MktEnableMode } from "./types";

export const mktKeys = {
  all: ["marketplace"] as const,
  packs: () => ["marketplace", "packs"] as const,
  seeds: (page: number, family: string, status: string, q: string) => ["marketplace", "seeds", page, family, status, q] as const,
  seedDetail: (seedId: string) => ["marketplace", "seed", seedId] as const,
  rankings: (dimension: string) => ["marketplace", "rankings", dimension] as const,
  scoreHistory: (seedId: string) => ["marketplace", "scores", seedId] as const,
  repairs: (seedId: string) => ["marketplace", "repairs", seedId] as const,
  snapshot: () => ["marketplace", "runtime-snapshot"] as const,
};

export function useMktPacks() {
  return useQuery({ queryKey: mktKeys.packs(), queryFn: ({ signal }) => marketplaceQueries.packs(signal), retry: 1 });
}

export function useMktSeeds(opts: { page: number; family: string; status: string; q: string }) {
  return useQuery({
    queryKey: mktKeys.seeds(opts.page, opts.family, opts.status, opts.q),
    queryFn: ({ signal }) =>
      marketplaceQueries.seeds({ page: opts.page, pageSize: 100, family: opts.family || undefined, status: opts.status || undefined, q: opts.q || undefined }, signal),
    retry: 1,
  });
}

export function useMktSeedDetail(seedId: string | null) {
  return useQuery({
    queryKey: mktKeys.seedDetail(seedId ?? "-"),
    queryFn: ({ signal }) => marketplaceQueries.seedDetail(seedId as string, signal),
    enabled: seedId !== null,
    retry: 1,
  });
}

export function useMktRankings(dimension: string) {
  return useQuery({ queryKey: mktKeys.rankings(dimension), queryFn: ({ signal }) => marketplaceQueries.rankings(dimension, signal), retry: 1 });
}

export function useMktScoreHistory(seedId: string | null) {
  return useQuery({
    queryKey: mktKeys.scoreHistory(seedId ?? "-"),
    queryFn: ({ signal }) => marketplaceQueries.scoreHistory(seedId as string, signal),
    enabled: seedId !== null,
    retry: 1,
  });
}

export function useMktRepairs(seedId: string) {
  return useQuery({
    queryKey: mktKeys.repairs(seedId || "all"),
    queryFn: ({ signal }) => marketplaceQueries.repairs(seedId || undefined, signal),
    retry: 1,
  });
}

export function useMktSnapshot() {
  return useQuery({ queryKey: mktKeys.snapshot(), queryFn: ({ signal }) => marketplaceQueries.runtimeSnapshot(signal), retry: 1 });
}

function useInvalidateAll<TVars, TRes>(fn: (vars: TVars) => Promise<TRes>) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: () => void client.invalidateQueries({ queryKey: mktKeys.all }),
  });
}

export const useInstallPack = () => useInvalidateAll((v: { packId: string; count: number }) => marketplaceCommands.installPack(v.packId, v.count));
export const useEnableSeed = () => useInvalidateAll((v: { seedId: string; mode: MktEnableMode }) => marketplaceCommands.enableSeed(v.seedId, v.mode));
export const useDisableSeed = () => useInvalidateAll((seedId: string) => marketplaceCommands.disableSeed(seedId));
export const useRunResearch = () => useInvalidateAll((seedId: string) => marketplaceCommands.runResearch(seedId));
export const useRepairSeed = () => useInvalidateAll((v: { seedId: string; trigger: string }) => marketplaceCommands.repairSeed(v.seedId, v.trigger));
