/**
 * Database: application services.
 *
 * Guarded operations (backup / migrate) require a typed confirmation in the UI
 * and only then hit the backend; while a migration job runs, the page polls
 * /api/db/manage/progress and renders the live bar. Every result is decided by
 * the backend envelope ({success:true} + payload), never by HTTP-200 alone.
 */

import { useMutation, useQuery, useQueryClient, type QueryKey } from "@tanstack/react-query";
import { dbApi, type DbActionEnvelope } from "./api";
import { ApiError } from "@/types/api";
import type { FieldValues } from "@/features/config/validation";
import { useI18n } from "@/stores/i18nStore";
import { pgPayload, validatePgConfig } from "./model";

export const DB_KEYS = {
  status: ["database", "status"] as QueryKey,
  hygiene: ["database", "hygiene"] as QueryKey,
  manageStatus: ["database", "manage-status"] as QueryKey,
  progress: ["database", "progress"] as QueryKey,
  report: ["database", "report"] as QueryKey,
  databases: ["database", "console", "databases"] as QueryKey,
};

export function useDbStatus(paused = false) {
  return useQuery({ queryKey: DB_KEYS.status, queryFn: ({ signal }) => dbApi.status(signal), refetchInterval: paused ? false : 30_000 });
}

export function useDbHygiene(paused = false) {
  return useQuery({ queryKey: DB_KEYS.hygiene, queryFn: ({ signal }) => dbApi.hygiene(signal), refetchInterval: paused ? false : 60_000 });
}

export function useDbManageStatus(paused = false) {
  return useQuery({ queryKey: DB_KEYS.manageStatus, queryFn: ({ signal }) => dbApi.manageStatus(signal), refetchInterval: paused ? false : 30_000 });
}

/** Live progress — only polled while a migration is running (the page flips
 *  `active` from the migrate response until done:true arrives). */
export function useMigrationProgress(active: boolean) {
  return useQuery({
    queryKey: DB_KEYS.progress,
    queryFn: ({ signal }) => dbApi.progress(signal),
    refetchInterval: active ? 2_000 : false,
    enabled: active,
  });
}

export function useLastReport(active: boolean) {
  return useQuery({
    queryKey: DB_KEYS.report,
    queryFn: ({ signal }) => dbApi.report(signal),
    enabled: active,
  });
}

export function useConsoleDatabases(paused = false) {
  return useQuery({ queryKey: DB_KEYS.databases, queryFn: ({ signal }) => dbApi.consoleDatabases(signal), refetchInterval: paused ? false : 60_000 });
}

export function useConsoleTables(database: string | null) {
  return useQuery({
    queryKey: ["database", "console", "tables", database],
    queryFn: ({ signal }) => dbApi.consoleTables(database ?? "", signal),
    enabled: !!database,
  });
}

export function useConsoleRows(database: string | null, table: string | null, limit: number, offset: number) {
  return useQuery({
    queryKey: ["database", "console", "rows", database, table, limit, offset],
    queryFn: ({ signal }) => dbApi.consoleRows(database ?? "", table ?? "", limit, offset, signal),
    enabled: !!database && !!table,
  });
}

/** Column schema for the selected table — enabled only while the operator has
 *  the schema view open (the endpoint existed in api.ts but was never wired). */
export function useConsoleColumns(database: string | null, table: string | null, enabled: boolean) {
  return useQuery({
    queryKey: ["database", "console", "columns", database, table],
    queryFn: ({ signal }) => dbApi.consoleColumns(database ?? "", table ?? "", signal),
    enabled: !!database && !!table && enabled,
  });
}

/* ------------------------------ envelope outcome ------------------------------ */

export interface DbOutcome {
  ok: boolean;
  message: string;
  requestId: string | null;
  body: DbActionEnvelope | null;
}

function normalize(body: DbActionEnvelope, okFallback: string, failFallback: string): DbOutcome {
  const ok = body.success === true;
  const err = body.error;
  const msg = typeof err === "string" ? err : err?.message ?? (ok ? okFallback : failFallback);
  return { ok, message: msg, requestId: typeof err === "object" && err !== null ? err.request_id ?? null : null, body };
}

function fromThrow(e: unknown, fallback: string): DbOutcome {
  if (e instanceof ApiError) return { ok: false, message: `${e.message}`, requestId: e.requestId, body: null };
  return { ok: false, message: e instanceof Error ? e.message : fallback, requestId: null, body: null };
}

/* ------------------------------ mutations ------------------------------ */

export function useSaveDbConfig() {
  const t = useI18n((s) => s.t);
  const queryClient = useQueryClient();
  return useMutation<DbOutcome, Error, FieldValues>({
    mutationFn: async (values) => {
      const errors = validatePgConfig(values, t);
      if (Object.values(errors).some((m) => m.length > 0)) {
        return {
          ok: false,
          message: t("database.msg.save_blocked", "Client validation blocked the save: {errors}", { errors: Object.values(errors).flat().join(" · ") }),
          requestId: null,
          body: null,
        };
      }
      try {
        return normalize(await dbApi.saveConfig(pgPayload(values)), t("database.msg.config_saved", "PostgreSQL configuration persisted (password only into the SecretStore)."), t("database.msg.config_refused", "Backend refused the config save."));
      } catch (e) {
        return fromThrow(e, t("database.msg.config_failed", "Config save failed."));
      }
    },
    onSuccess: (o) => {
      if (o.ok) void queryClient.invalidateQueries({ queryKey: DB_KEYS.manageStatus });
    },
  });
}

export function useTestDbConnection() {
  const t = useI18n((s) => s.t);
  return useMutation<DbOutcome, Error, FieldValues>({
    mutationFn: async (values) => {
      const errors = validatePgConfig(values, t);
      if (Object.values(errors).some((m) => m.length > 0)) {
        return { ok: false, message: t("database.msg.fix_fields", "Fix the highlighted fields first: {errors}", { errors: Object.values(errors).flat().join(" · ") }), requestId: null, body: null };
      }
      try {
        const body = await dbApi.testConnection(pgPayload(values));
        const ok = body.success === true && body.connected === true;
        return normalize(
          body,
          ok
            ? t("database.msg.connected", "Connected — {version}.", { version: body.database_version || t("database.msg.version_unknown", "version unknown") })
            : t("database.msg.conn_failed", "Connection test failed."),
          t("database.msg.conn_failed", "Connection test failed."),
        );
      } catch (e) {
        return fromThrow(e, t("database.msg.conn_failed", "Connection test failed."));
      }
    },
  });
}

export function useSwitchProvider() {
  const t = useI18n((s) => s.t);
  const queryClient = useQueryClient();
  return useMutation<DbOutcome, Error, string>({
    mutationFn: async (provider) => {
      try {
        const body = await dbApi.switchProvider(provider);
        return normalize(body, t("database.msg.provider_switched", "Provider switched to {provider} — applies on next restart; no data was moved.", { provider }), t("database.msg.switch_refused", "Provider switch refused."));
      } catch (e) {
        return fromThrow(e, t("database.msg.switch_failed", "Provider switch failed."));
      }
    },
    onSuccess: (o) => {
      if (o.ok) void queryClient.invalidateQueries({ queryKey: DB_KEYS.manageStatus });
    },
  });
}

export function useMigrationPreview() {
  const t = useI18n((s) => s.t);
  return useMutation<DbOutcome, Error, FieldValues>({
    mutationFn: async (values) => {
      const errors = validatePgConfig(values, t);
      if (Object.values(errors).some((m) => m.length > 0)) {
        return { ok: false, message: t("database.msg.preview_blocked", "Preview blocked by client validation: {errors}", { errors: Object.values(errors).flat().join(" · ") }), requestId: null, body: null };
      }
      try {
        return normalize(await dbApi.preview(pgPayload(values)), t("database.msg.preview_ok", "Preview computed (dry-run; nothing was written)."), t("database.msg.preview_failed", "Preview failed."));
      } catch (e) {
        return fromThrow(e, t("database.msg.preview_failed", "Preview failed."));
      }
    },
  });
}

/** Typed-confirm entry point — the UI only calls this after the operator typed
 *  the confirmation word; `confirm:true` is sent exactly as the backend gate
 *  requires. */
export function useStartMigration() {
  const t = useI18n((s) => s.t);
  const queryClient = useQueryClient();
  return useMutation<DbOutcome, Error, { values: FieldValues; resume: boolean; batchSize: number }>({
    mutationFn: async ({ values, resume, batchSize }) => {
      const errors = validatePgConfig(values, t);
      if (Object.values(errors).some((m) => m.length > 0)) {
        return { ok: false, message: t("database.msg.migration_blocked", "Migration blocked by client validation: {errors}", { errors: Object.values(errors).flat().join(" · ") }), requestId: null, body: null };
      }
      try {
        const body = await dbApi.migrate({ ...pgPayload(values), confirm: true, resume, batch_size: batchSize, validate_checksums: true });
        return normalize(body, t("database.msg.migration_started", "Migration job started — progress below streams live from the backend worker."), t("database.msg.migration_refused", "Migration refused."));
      } catch (e) {
        return fromThrow(e, t("database.msg.migration_failed", "Migration start failed."));
      }
    },
    onMutate: () => {
      // clear a previous finished job's state so the bar restarts honestly
      void queryClient.invalidateQueries({ queryKey: DB_KEYS.progress });
    },
  });
}

export function useDbBackup() {
  const t = useI18n((s) => s.t);
  const queryClient = useQueryClient();
  return useMutation<DbOutcome, Error, void>({
    mutationFn: async () => {
      try {
        const body = await dbApi.backup();
        return normalize(
          body,
          body.backup_path
            ? t("database.msg.backup_written_path", "Backup written → {path}.", { path: body.backup_path })
            : t("database.msg.backup_written", "Backup written."),
          t("database.msg.backup_refused", "Backup refused or failed."),
        );
      } catch (e) {
        return fromThrow(e, t("database.msg.backup_failed", "Backup failed."));
      }
    },
    onSuccess: (o) => {
      if (o.ok) void queryClient.invalidateQueries({ queryKey: DB_KEYS.databases });
    },
  });
}

export function useDbValidate() {
  const t = useI18n((s) => s.t);
  return useMutation<DbOutcome, Error, void>({
    mutationFn: async () => {
      try {
        const body = await dbApi.validate();
        const v = body.validation;
        const label = typeof v === "string" ? v : (v as { status?: string } | undefined)?.status ?? "done";
        return normalize(body, t("database.msg.validation_finished", "Validation finished: {label}.", { label }), t("database.msg.validation_refused", "Validation refused."));
      } catch (e) {
        return fromThrow(e, t("database.msg.validation_failed", "Validation failed."));
      }
    },
  });
}

export function useSqlQuery() {
  const t = useI18n((s) => s.t);
  return useMutation<DbOutcome & { rows?: Array<Record<string, unknown>>; columns?: string[]; truncated?: boolean }, Error, { database: string; sql: string }>({
    mutationFn: async ({ database, sql }) => {
      // Read-only console — the SERVER enforces the statement allow-list; the
      // client refuses nothing except emptiness (its rules can't be stricter
      // than the truth without lying about what the console supports).
      if (sql.trim() === "") {
        return { ok: false, message: t("database.msg.empty_sql", "Empty SQL — nothing sent."), requestId: null, body: null };
      }
      try {
        const body = await dbApi.consoleQuery(database, sql);
        const rowCount = String(body.rows_returned ?? body.rows?.length ?? 0);
        const base = normalize(
          body as DbActionEnvelope,
          body.truncated
            ? t("database.msg.query_ok_trunc", "Query ok — {n} row(s) (truncated at 500).", { n: rowCount })
            : t("database.msg.query_ok", "Query ok — {n} row(s).", { n: rowCount }),
          t("database.msg.query_refused", "Query refused: {detail}", { detail: typeof body.error === "string" ? body.error : t("database.msg.backend_error", "backend error") }),
        );
        return { ...base, rows: body.rows, columns: body.columns, truncated: body.truncated };
      } catch (e) {
        return { ...fromThrow(e, t("database.msg.query_failed", "Query failed.")) };
      }
    },
  });
}

export function useRunQuickSql() {
  const t = useI18n((s) => s.t);
  return useMutation<DbOutcome & { rows?: Array<Record<string, unknown>>; columns?: string[] }, Error, { database: string; table: string; kind: string }>({
    mutationFn: async ({ database, table, kind }) => {
      try {
        const body = await dbApi.consoleQuick(database, table, kind);
        const base = normalize(
          body as DbActionEnvelope,
          t("database.msg.quick_ok", "Quick {kind} ok — {n} row(s).", { kind, n: String(body.rows?.length ?? 0) }),
          t("database.msg.quick_refused", "Quick {kind} refused: {detail}", { kind, detail: typeof body.error === "string" ? body.error : t("database.msg.backend_error", "backend error") }),
        );
        return { ...base, rows: body.rows, columns: body.columns };
      } catch (e) {
        return { ...fromThrow(e, t("database.msg.quick_failed", "Quick query failed.")) };
      }
    },
  });
}

export function useApiKeys(paused = false) {
  return useQuery({
    queryKey: ["database", "console", "apikeys"],
    queryFn: ({ signal }) => dbApi.apiKeys(signal),
    refetchInterval: paused ? false : 60_000,
  });
}

export function useSaveApiKey() {
  const t = useI18n((s) => s.t);
  const queryClient = useQueryClient();
  return useMutation<DbOutcome, Error, { name: string; value: string }>({
    mutationFn: async ({ name, value }) => {
      if (!/^[A-Za-z0-9_.-]{2,}$/.test(name)) {
        return { ok: false, message: t("database.msg.key_name_rule", "Key name must be a simple identifier (≥2 chars, no spaces/slashes) — mirrors the server rule, so no wasted POST."), requestId: null, body: null };
      }
      try {
        return normalize(await dbApi.apiKeySet(name, value), t("database.msg.key_stored", "API key {name} stored in the OS secret store (value never echoed).", { name }), t("database.msg.key_save_refused", "Key save refused."));
      } catch (e) {
        return fromThrow(e, t("database.msg.key_save_failed", "Key save failed."));
      }
    },
    onSuccess: (o) => {
      if (o.ok) void queryClient.invalidateQueries({ queryKey: ["database", "console", "apikeys"] });
    },
  });
}

export function useDeleteApiKey() {
  const t = useI18n((s) => s.t);
  const queryClient = useQueryClient();
  return useMutation<DbOutcome, Error, string>({
    mutationFn: async (name) => {
      try {
        return normalize(await dbApi.apiKeyDelete(name), t("database.msg.key_deleted", "API key {name} deleted.", { name }), t("database.msg.delete_refused", "Delete refused."));
      } catch (e) {
        return fromThrow(e, t("database.msg.delete_failed", "Delete failed."));
      }
    },
    onSuccess: (o) => {
      if (o.ok) void queryClient.invalidateQueries({ queryKey: ["database", "console", "apikeys"] });
    },
  });
}

export function useRefreshConsole() {
  const t = useI18n((s) => s.t);
  const queryClient = useQueryClient();
  return useMutation<DbOutcome, Error, void>({
    mutationFn: async () => {
      try {
        const body = await dbApi.consoleRefresh();
        return normalize(body as DbActionEnvelope, t("database.msg.rescanned", "Database list re-scanned."), t("database.msg.resync_failed", "Resync failed."));
      } catch (e) {
        return fromThrow(e, t("database.msg.resync_failed", "Resync failed."));
      }
    },
    onSuccess: (o) => {
      if (o.ok) void queryClient.invalidateQueries({ queryKey: DB_KEYS.databases });
    },
  });
}
