/**
 * Marketplace bounded context — DTO contracts for /api/v1/marketplace/*.
 *
 * Source of truth: src/nexus_scalp/web/api_v1/marketplace.py (frozen contract,
 * CHG-0056). Every response arrives in the v1 envelope `{data, meta}`; the
 * paginated shape is the canonical `{items, page, page_size, has_more}`.
 */

export interface V1Page<T> {
  items: T[];
  page: number;
  page_size: number;
  has_more: boolean;
}

/** GET /packs -> { packs, count } */
export interface MktPack {
  id: string;
  name: string;
  family: string;
  description: string;
  installed: boolean;
  seed_count: number;
}

export interface MktPacksResponse {
  packs: MktPack[];
  count: number;
}

/** POST /packs/{id}/install -> service.install_pack result (201) */
export interface MktInstallResult {
  pack_id?: string;
  installed?: number;
  skipped?: number;
  seeds?: number;
  [k: string]: unknown;
}

/** GET /seeds -> page of mk_seeds rows */
export interface MktSeed {
  seed_id: string;
  version?: string;
  pack_id?: string;
  name?: string;
  family?: string;
  author?: string;
  description?: string;
  source?: string;
  license?: string;
  risk_profile?: string;
  lifecycle?: string;
  created_at?: string | null;
  updated_at?: string | null;
  [k: string]: unknown;
}

/** GET /seeds/{id} — row + lifecycle events + enablement + recent scores/repairs */
export interface MktSeedDetail extends MktSeed {
  lifecycle_events?: Array<{
    event_id?: string;
    seed_id?: string;
    from_lifecycle?: string;
    to_lifecycle?: string;
    reason?: string;
    actor?: string;
    created_at?: string;
  }>;
  enablement?: Array<{ seed_id?: string; mode?: string; status?: string; reason?: string; actor?: string; updated_at?: string }>;
  recent_scores?: MktScoreSnapshot[];
  recent_repairs?: MktRepair[];
}

/** POST /seeds/{id}/enable — PENDING/DENIED/OK are distinct backend outcomes */
export interface MktEnableResult {
  status?: string;
  reason?: string;
  seed_id?: string;
  mode?: string;
  [k: string]: unknown;
}

export interface MktDisableResult {
  status?: string;
  reason?: string;
  [k: string]: unknown;
}

export interface MktResearchResult {
  run_id?: string;
  status?: string;
  seed_id?: string;
  [k: string]: unknown;
}

/** GET /rankings -> { items, dimension, profile_id, has_more } */
export interface MktRankingRow {
  seed_id: string;
  family?: string;
  lifecycle?: string;
  total?: number | null;
  verdict?: string | null;
  profile_id?: string;
  scored_at?: string | null;
}

export interface MktRankingsResponse {
  items: MktRankingRow[];
  dimension: string;
  profile_id: string;
  has_more: boolean;
}

/** GET /scores/{seed_id}/history — 14-factor snapshots (factors decoded to obj) */
export interface MktScoreSnapshot {
  snapshot_id?: string;
  seed_id?: string;
  profile_id?: string;
  profile_version?: number;
  total?: number | null;
  verdict?: string;
  factors?: Record<string, number> | string | null;
  created_at?: string | null;
}

/** GET /repairs -> { items } */
export interface MktRepair {
  repair_id: string;
  seed_id?: string;
  parent_seed_id?: string;
  trigger?: string;
  status?: string;
  outcome?: Record<string, unknown> | string | null;
  created_at?: string;
}

/** GET /runtime-snapshot */
export interface MktRuntimeSnapshot {
  version: number;
  enabled_set: string[];
  created_at: string;
  source: string;
}

/** POST /seeds/{id}/repair -> 201 child seed + PENDING repair record */
export interface MktRepairResult {
  repair_id?: string;
  parent_seed_id?: string;
  child_seed_id?: string;
  status?: string;
  trigger?: string;
  [k: string]: unknown;
}

export type MktEnableMode = "RESEARCH" | "PAPER" | "SHADOW" | "LIVE_REQUEST";
