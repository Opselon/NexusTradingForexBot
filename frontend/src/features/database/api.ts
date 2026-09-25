/**
 * Database — typed transport for the persistence bounded context.
 *
 * Endpoints verified in src/nexus_scalp/web/{diagnostics_state_routes,debug_research_routes,db_console}.py:
 *  GET  /api/db/status                per-domain schema/migration state
 *  GET  /api/db/hygiene               hygiene worker status + plans + quarantine
 *  GET  /api/db/manage/status         active provider + domain health + pg config (no password)
 *  POST /api/db/manage/config         persist PG config (password routed to SecretStore)
 *  POST /api/db/manage/provider       flip active provider (restart applies; data untouched)
 *  POST /api/db/manage/test-connection ping PG before migrating (never persists)
 *  POST /api/db/manage/preview        dry-run migration preview
 *  POST /api/db/manage/migrate        start background migration {confirm:true,...}
 *  GET  /api/db/manage/progress       live progress poll while a job runs
 *  GET  /api/db/manage/report         last migration report
 *  GET  /api/db/manage/validate       validate last migration
 *  POST /api/db/manage/backup         WAL-consistent SQLite backup (guarded)
 *  Console: databases/refresh/tables/columns/rows/quick/query + apikeys CRUD
 *
 * Feature-local surface over the stable `@/api/client` transport.
 */

import { getAuthToken, getLegacy, send } from "@/api/client";

/** DELETE is not exposed by the stable client surface (transport belongs to
 *  lane 1). This feature needs exactly one DELETE (/api/db/console/apikey/*),
 *  implemented here with the SAME auth headers the transport attaches
 *  (getAuthToken from @/api/client — no secrets invented, cookie rides via
 *  credentials:same-origin). Not used for anything else. */
async function del<T>(path: string): Promise<T> {
  const headers: Record<string, string> = { "X-Request-ID": `altui_del_${Date.now().toString(36)}` };
  const token = getAuthToken();
  if (token) {
    headers["Authorization"] = `Bearer ${token}`;
    headers["X-NSE-Token"] = token;
  }
  const res = await fetch(path, { method: "DELETE", headers, credentials: "same-origin" });
  const body = await res.json().catch(() => null);
  if (!res.ok) throw new Error((body as { error?: { message?: string } } | null)?.error?.message ?? `DELETE ${path} failed (HTTP ${res.status}).`);
  return body as T;
}

/* ------------------------------ status ------------------------------ */

export interface DbDomainStatus {
  schema_version?: number;
  expected_version?: number;
  migration_state?: string;
  pending_count?: number;
  integrity?: string;
  last_migration?: Record<string, unknown>;
  tamper_detected?: boolean;
  error?: string;
}

export interface DbStatus {
  available: boolean;
  databases: Record<string, DbDomainStatus>;
  error?: { code?: string; message?: string; request_id?: string };
}

export interface DbHygiene {
  status: Record<string, unknown>;
  plans: Record<string, unknown>;
  runtime?: Record<string, unknown>;
  quarantine?: Record<string, unknown>;
}

export interface PgConfig {
  host?: string;
  port?: number | string;
  database?: string;
  username?: string;
  ssl_mode?: string;
  [key: string]: unknown;
}

export interface SqliteEvidence {
  name: string;
  file: string;
  path?: string;
  bytes: number;
  age_seconds: number;
  mtime_utc?: string;
  active: boolean;
}

/** Measured configured-vs-effective provider (backend `_provider_truth`,
 *  2026-09-23): `mismatch=true` is the "badge says postgresql, data comes
 *  from sqlite" state. Absent on an engine that has not been restarted
 *  with the new code — the UI then derives a narrower claim client-side. */
export interface ProviderTruthPayload {
  configured: string;
  effective: string;
  mismatch: boolean;
  pg_reachable: boolean | null;
  pg_target: string;
  pg_error: string;
  evidence: SqliteEvidence[];
  note: string;
  measured_at?: string;
}

export interface DbManageStatus {
  success: boolean;
  provider?: string;
  supported_providers?: string[];
  overall?: string;
  domains?: Record<string, unknown>;
  postgres?: PgConfig | null;
  password_set?: boolean;
  /** Is psycopg importable in the server env (added 2026-09-23: the active
   *  provider can be postgresql while the driver package is absent). */
  postgresql_driver_available?: boolean;
  /** Backend-computed guidance for a broken provider state — rendered
   *  verbatim, never re-derived on the client. */
  hints?: string[];
  /** Measured configured-vs-effective provider; see ProviderTruthPayload. */
  provider_truth?: ProviderTruthPayload;
  /** Advanced knobs reported by GET /api/db/manage/status (wave
   *  db-provider-pro, contract §3.3). Absent on older builds — the UI
   *  renders UNAVAILABLE, never a fabricated default. */
  options?: { [key: string]: unknown } | null;
  /** DEFAULT_DB_FILES domain list (additive key; `domains` stays the
   *  per-domain health snapshot dict). */
  domain_db_names?: string[];
  error?: { code?: string; message?: string; request_id?: string };
}

/* ------------------------------ migration ------------------------------ */

export interface MigrationPreview {
  source?: string;
  destination?: string;
  tables?: string[];
  table_details?: Record<string, unknown>;
  rows?: number;
  estimated_volume_bytes?: number;
  issues?: string[];
  warnings?: string[];
}

export interface MigrationReport {
  status?: string;
  source?: string;
  destination?: string;
  tables_migrated?: number;
  rows_migrated?: number;
  rows_failed?: number;
  duration_ms?: number;
  validation?: string;
  provider_switch_ready?: boolean;
  per_table?: Record<string, unknown>;
  errors?: string[];
  warnings?: string[];
}

export interface MigrationProgress {
  success: boolean;
  done: boolean;
  progress: number;
  current_table?: string;
  rows_copied?: number;
  total_rows?: number;
  report?: MigrationReport | null;
  error?: { code?: string; message?: string };
}

export interface DbActionEnvelope {
  success?: boolean;
  report?: MigrationReport | null;
  started?: boolean;
  job?: string;
  backup_path?: string;
  validation?: MigrationReport | "NOT_CONFIGURED" | string;
  provider?: string;
  restart_required?: boolean;
  password_set?: boolean;
  connected?: boolean;
  database_version?: string;
  preview?: MigrationPreview;
  error?: { code?: string; message?: string; request_id?: string } | string;
  detail?: unknown;
  message?: string;
}

/* ------------------------------ console ------------------------------ */

export interface ConsoleDatabase {
  name: string;
  provider: string;
  database: string;
  server: string;
  path: string;
  size_bytes: number | null;
  status: string;
  table_count?: number | null;
  description?: string;
  /** Backend's single next action when this database cannot be opened
   *  (psycopg missing / server down are different faults, different fixes). */
  hint?: string;
  [key: string]: unknown;
}

export interface ConsoleFailure {
  /** Stable machine code (PG_DRIVER_MISSING / DB_UNREACHABLE / DB_CONSOLE_ERROR). */
  code?: string;
  /** The one next action, when the backend can name it. */
  hint?: string;
}

export interface ConsoleTables extends ConsoleFailure {
  success: boolean;
  database?: string;
  provider?: string;
  tables?: Array<{ name: string; rows: number | null }>;
  error?: string;
}

export interface ConsoleRows extends ConsoleFailure {
  success: boolean;
  database?: string;
  table?: string;
  columns?: string[];
  rows?: Array<Record<string, unknown>>;
  limit?: number;
  offset?: number;
  error?: string;
}

export interface ConsoleQueryResult extends ConsoleFailure {
  success: boolean;
  database?: string;
  provider?: string;
  columns?: string[];
  rows?: Array<Record<string, unknown>>;
  truncated?: boolean;
  rows_returned?: number;
  /** Row cap the backend applied (present on successful query answers). */
  cap?: number;
  error?: string;
}

/** One column from /api/db/console/columns (portable normalized shape). */
export interface ConsoleColumn {
  name: string;
  type: string;
  notnull: boolean;
  pk: boolean;
  default?: unknown;
}

export interface ApiKeyRow {
  name: string;
  masked: string;
  set: boolean;
}

export const dbApi = {
  status: (signal?: AbortSignal): Promise<DbStatus> => getLegacy<DbStatus>("/api/db/status", signal),
  hygiene: (signal?: AbortSignal): Promise<DbHygiene> => getLegacy<DbHygiene>("/api/db/hygiene", signal),
  manageStatus: (signal?: AbortSignal): Promise<DbManageStatus> => getLegacy<DbManageStatus>("/api/db/manage/status", signal),

  saveConfig: (payload: PgConfig & { password?: string; confirm_password?: string }): Promise<DbActionEnvelope> =>
    send<DbActionEnvelope>("/api/db/manage/config", payload),
  switchProvider: (provider: string): Promise<DbActionEnvelope> => send<DbActionEnvelope>("/api/db/manage/provider", { provider }),
  testConnection: (payload: PgConfig & { password?: string }): Promise<DbActionEnvelope> =>
    send<DbActionEnvelope>("/api/db/manage/test-connection", payload),
  preview: (payload: PgConfig & { sqlite_path?: string; password?: string }): Promise<DbActionEnvelope> =>
    send<DbActionEnvelope>("/api/db/manage/preview", payload),
  migrate: (payload: PgConfig & { confirm: boolean; resume?: boolean; batch_size?: number; validate_checksums?: boolean; sqlite_path?: string; password?: string; dry_run?: boolean }): Promise<DbActionEnvelope> =>
    send<DbActionEnvelope>("/api/db/manage/migrate", payload),
  progress: (signal?: AbortSignal): Promise<MigrationProgress> => getLegacy<MigrationProgress>("/api/db/manage/progress", signal),
  report: (signal?: AbortSignal): Promise<DbActionEnvelope> => getLegacy<DbActionEnvelope>("/api/db/manage/report", signal),
  validate: (signal?: AbortSignal): Promise<DbActionEnvelope> => getLegacy<DbActionEnvelope>("/api/db/manage/validate", signal),
  backup: (): Promise<DbActionEnvelope> => send<DbActionEnvelope>("/api/db/manage/backup", {}),

  consoleDatabases: (signal?: AbortSignal): Promise<{ success: boolean; databases: ConsoleDatabase[]; error?: string }> =>
    getLegacy("/api/db/console/databases", signal),
  consoleRefresh: (): Promise<{ success: boolean; databases: ConsoleDatabase[]; resynced?: boolean; error?: string }> =>
    send("/api/db/console/refresh", {}),
  consoleTables: (database: string, signal?: AbortSignal): Promise<ConsoleTables> =>
    getLegacy<ConsoleTables>(`/api/db/console/tables?database=${encodeURIComponent(database)}`, signal),
  consoleColumns: (database: string, table: string, signal?: AbortSignal): Promise<{ success: boolean; columns?: ConsoleColumn[]; error?: string } & ConsoleFailure> =>
    getLegacy(`/api/db/console/columns?database=${encodeURIComponent(database)}&table=${encodeURIComponent(table)}`, signal),
  consoleRows: (database: string, table: string, limit = 100, offset = 0, signal?: AbortSignal): Promise<ConsoleRows> =>
    getLegacy<ConsoleRows>(
      `/api/db/console/rows?database=${encodeURIComponent(database)}&table=${encodeURIComponent(table)}&limit=${limit}&offset=${offset}`,
      signal,
    ),
  consoleQuick: (database: string, table: string, kind: string, signal?: AbortSignal): Promise<ConsoleQueryResult> =>
    getLegacy<ConsoleQueryResult>(
      `/api/db/console/quick?database=${encodeURIComponent(database)}&table=${encodeURIComponent(table)}&kind=${encodeURIComponent(kind)}`,
      signal,
    ),
  consoleQuery: (database: string, sql: string): Promise<ConsoleQueryResult> =>
    send<ConsoleQueryResult>("/api/db/console/query", { database, sql }),
  apiKeys: (signal?: AbortSignal): Promise<{ success: boolean; apikeys?: ApiKeyRow[]; error?: string }> =>
    getLegacy("/api/db/console/apikeys", signal),
  apiKeySet: (name: string, value: string): Promise<DbActionEnvelope> =>
    send<DbActionEnvelope>("/api/db/console/apikey", { name, value }),
  apiKeyDelete: (name: string): Promise<DbActionEnvelope> => del<DbActionEnvelope>(`/api/db/console/apikey/${encodeURIComponent(name)}`),
};
