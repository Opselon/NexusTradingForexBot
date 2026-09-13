/**
 * Factory: DTO contracts + VO mappers (pure).
 *
 * All /api/factory/* answers wrap payloads in {available:true|false, reason}.
 * When the strategy factory is not mounted the routes answer
 * {available:false, reason:"FACTORY_UNAVAILABLE"} — the UI renders that
 * honestly per section (EmptyState 'no backend endpoint' / unavailable),
 * never a placeholder dataset.
 */

export type Row = Record<string, unknown>;

export const str = (v: unknown): string | null => (typeof v === "string" && v ? v : typeof v === "number" ? String(v) : null);
export const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);
export const bool = (v: unknown): boolean | null => (typeof v === "boolean" ? v : null);
export const obj = (v: unknown): Row => (v && typeof v === "object" && !Array.isArray(v) ? (v as Row) : {});
export const arr = (v: unknown): Row[] => (Array.isArray(v) ? (v as Row[]) : []);

export interface FactoryStatusDto {
  available?: boolean;
  reason?: string;
  loop?: Row & { state?: string; current_generation?: string; kill_requested?: boolean };
  generations?: Row[];
  provider_usage?: Row;
  config?: Row;
  provider?: Row & { available?: boolean; model?: string; base_url?: string; prompt_version?: string; usage?: Row };
  worker?: Row;
}

export interface FactoryGenerationsDto {
  available?: boolean;
  reason?: string;
  generations?: Row[];
}

export interface FactoryGenerationDto {
  available?: boolean;
  reason?: string;
  generation?: Row;
  candidates?: Row[];
}

export interface FactoryCandidatesDto {
  available?: boolean;
  reason?: string;
  candidates?: Row[];
}

export interface FactoryBenchmarksDto {
  available?: boolean;
  reason?: string;
  benchmarks?: Row[];
}

export interface FactoryEventsDto {
  available?: boolean;
  reason?: string;
  events?: Row[];
}

export interface FactoryFailuresDto {
  available?: boolean;
  reason?: string;
  failures?: Row[];
}

export interface FactoryRankingDto {
  available?: boolean;
  reason?: string;
  dimension?: string;
  ranked?: Row[];
}

export interface FactoryMemoryDto {
  available?: boolean;
  reason?: string;
  memory?: Row;
}

export interface FactoryLlmConfigDto {
  available?: boolean;
  reason?: string;
  status?: Row & { api_key_present?: boolean; base_url?: string; model?: string; temperature?: number };
  provider?: Row;
}

export interface FactoryProviderHealthDto {
  available?: boolean;
  reason?: string;
  state?: string;
  degraded?: boolean;
  [key: string]: unknown;
}

export interface FactoryCommandDto {
  available?: boolean;
  reason?: string;
  [key: string]: unknown;
}

/** Generation row -> compact table VO. */
export interface GenerationVo {
  id: string;
  number: number | null;
  mode: string;
  state: string;
  size: number | null;
  createdAt: string | null;
  raw: Row;
}

export function toGenerationVo(row: Row): GenerationVo {
  return {
    id: str(row.generation_id) ?? str(row.id) ?? "—",
    number: num(row.number),
    mode: str(row.mode) ?? "—",
    state: str(row.state) ?? str(row.status) ?? "UNKNOWN",
    size: num(row.size) ?? num(row.candidate_count),
    createdAt: str(row.created_at) ?? str(row.started_at),
    raw: row,
  };
}

export function factoryVerdict(res: FactoryCommandDto): { ok: boolean; message: string } {
  if (bool(res.available) === false) {
    return { ok: false, message: str(res.reason) ?? "Backend refused: factory unavailable." };
  }
  const err = obj(res.error);
  if (err.code) return { ok: false, message: str(err.message) ?? str(err.code)! };
  return { ok: true, message: str(res.status) ?? str(res.state) ?? "Backend accepted the command." };
}
