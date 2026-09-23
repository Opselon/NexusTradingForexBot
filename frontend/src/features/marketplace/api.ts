/**
 * features/marketplace/api.ts — typed v1 calls for /api/v1/marketplace/*.
 *
 * Lane rule: the shared src/api/marketplaceApi.ts is being built concurrently
 * by Lane 1, so this feature keeps its own surface over the STABLE exports of
 * @/api/client (getV1/send). All marketplace routes are v1: GETs unwrap the
 * envelope via getV1; POSTs unwrap {data} here so use cases see plain DTOs.
 */

import { getV1, send } from "@/api/client";
import { ApiError, type V1Envelope } from "@/types/api";
import type {
  MktRepairResult,
  MktDisableResult,
  MktEnableMode,
  MktEnableResult,
  MktInstallResult,
  MktPack,
  MktPacksResponse,
  MktRankingsResponse,
  MktRepair,
  MktResearchResult,
  MktScoreSnapshot,
  MktSeed,
  MktSeedDetail,
  MktRuntimeSnapshot,
  V1Page,
} from "./types";

const id = (v: string): string => encodeURIComponent(v);

function qs(params: Record<string, string | number | boolean | undefined>): string {
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== "") sp.set(k, String(v));
  }
  const s = sp.toString();
  return s ? `?${s}` : "";
}

/**
 * POST a v1 route and unwrap its envelope (client throws on {error}).
 *
 * Logic guard: the transport only rejects NON-2xx answers. If a route ever
 * answers HTTP 200 with the safe error envelope {error:{code,message,
 * request_id}} the unwrap above would silently yield `undefined` and the UI
 * would report a bogus success — so the envelope is checked HERE, in the
 * api layer, and rethrown as ApiError (→ mutation onError / ErrorState).
 */
async function postV1<T>(path: string, body?: unknown): Promise<T> {
  const env = await send<V1Envelope<T> & { error?: { code?: string; message?: string; request_id?: string } }>(path, body ?? {});
  const err = env && typeof env === "object" ? env.error : undefined;
  if (err && (typeof err.code === "string" || typeof err.message === "string")) {
    throw new ApiError(
      200,
      typeof err.code === "string" ? err.code : "INTERNAL_ERROR",
      typeof err.message === "string" ? err.message : "Backend reported an error envelope at HTTP 200.",
      typeof err.request_id === "string" ? err.request_id : null,
      false,
    );
  }
  return (env as V1Envelope<T>).data;
}

export const marketplaceApi = {
  packs: (signal?: AbortSignal) => getV1<MktPacksResponse>("/api/v1/marketplace/packs", signal),

  installPack: (packId: string, count: number) =>
    postV1<MktInstallResult>(`/api/v1/marketplace/packs/${id(packId)}/install`, { count }),

  seeds: (opts: { family?: string; status?: string; q?: string; page?: number; pageSize?: number }, signal?: AbortSignal) =>
    getV1<V1Page<MktSeed>>(`/api/v1/marketplace/seeds${qs({ family: opts.family, status: opts.status, q: opts.q, page: opts.page ?? 1, page_size: opts.pageSize ?? 100 })}`, signal),

  seedDetail: (seedId: string, signal?: AbortSignal) =>
    getV1<MktSeedDetail>(`/api/v1/marketplace/seeds/${id(seedId)}`, signal),

  enableSeed: (seedId: string, mode: MktEnableMode) =>
    postV1<MktEnableResult>(`/api/v1/marketplace/seeds/${id(seedId)}/enable`, { mode }),

  disableSeed: (seedId: string) => postV1<MktDisableResult>(`/api/v1/marketplace/seeds/${id(seedId)}/disable`, {}),

  runResearch: (seedId: string) => postV1<MktResearchResult>(`/api/v1/marketplace/seeds/${id(seedId)}/run-research`, {}),

  rankings: (dimension: string, profileId?: string, signal?: AbortSignal) =>
    getV1<MktRankingsResponse>(`/api/v1/marketplace/rankings${qs({ dimension, profile_id: profileId })}`, signal),

  repairs: (seedId?: string, signal?: AbortSignal) =>
    getV1<{ items: MktRepair[] }>(`/api/v1/marketplace/repairs${qs({ seed_id: seedId })}`, signal),

  scoreHistory: (seedId: string, opts?: { page?: number; pageSize?: number }, signal?: AbortSignal) =>
    getV1<V1Page<MktScoreSnapshot>>(`/api/v1/marketplace/scores/${id(seedId)}/history${qs({ page: opts?.page ?? 1, page_size: opts?.pageSize ?? 50 })}`, signal),

  runtimeSnapshot: (signal?: AbortSignal) =>
    getV1<MktRuntimeSnapshot>("/api/v1/marketplace/runtime-snapshot", signal),

  repairSeed: (seedId: string, trigger: string) =>
    postV1<MktRepairResult>(`/api/v1/marketplace/seeds/${id(seedId)}/repair`, { trigger: trigger.trim() || "MANUAL_TRIGGER" }),
};

export type { MktPack };
