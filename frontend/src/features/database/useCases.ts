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
  const queryClient = useQueryClient();
  return useMutation<DbOutcome, Error, FieldValues>({
    mutationFn: async (values) => {
      const errors = validatePgConfig(values);
      if (Object.values(errors).some((m) => m.length > 0)) {
        return {
          ok: false,
          message: `Client validation blocked the save: ${Object.values(errors).flat().join(" · ")}`,
          requestId: null,
          body: null,
        };
      }
      try {
        return normalize(await dbApi.saveConfig(pgPayload(values)), "PostgreSQL configuration persisted (password only into the SecretStore).", "Backend refused the config save.");
      } catch (e) {
        return fromThrow(e, "Config save failed.");
      }
    },
    onSuccess: (o) => {
      if (o.ok) void queryClient.invalidateQueries({ queryKey: DB_KEYS.manageStatus });
    },
  });
}

export function useTestDbConnection() {
  return useMutation<DbOutcome, Error, FieldValues>({
    mutationFn: async (values) => {
      const errors = validatePgConfig(values);
      if (Object.values(errors).some((m) => m.length > 0)) {
        return { ok: false, message: `Fix the highlighted fields first: ${Object.values(errors).flat().join(" · ")}`, requestId: null, body: null };
      }
      try {
        const body = await dbApi.testConnection(pgPayload(values));
        const ok = body.success === true && body.connected === true;
        return normalize(body, ok ? `Connected — ${body.database_version || "version unknown"}.` : "Connection test failed.", "Connection test failed.");
      } catch (e) {
        return fromThrow(e, "Connection test failed.");
      }
    },
  });
}

export function useSwitchProvider() {
  const queryClient = useQueryClient();
  return useMutation<DbOutcome, Error, string>({
    mutationFn: async (provider) => {
      try {
        const body = await dbApi.switchProvider(provider);
        return normalize(body, `Provider switched to ${provider} — applies on next restart; no data was moved.`, "Provider switch refused.");
      } catch (e) {
        return fromThrow(e, "Provider switch failed.");
      }
    },
    onSuccess: (o) => {
      if (o.ok) void queryClient.invalidateQueries({ queryKey: DB_KEYS.manageStatus });
    },
  });
}

export function useMigrationPreview() {
  return useMutation<DbOutcome, Error, FieldValues>({
    mutationFn: async (values) => {
      const errors = validatePgConfig(values);
      if (Object.values(errors).some((m) => m.length > 0)) {
        return { ok: false, message: `Preview blocked by client validation: ${Object.values(errors).flat().join(" · ")}`, requestId: null, body: null };
      }
      try {
        return normalize(await dbApi.preview(pgPayload(values)), "Preview computed (dry-run; nothing was written).", "Preview failed.");
      } catch (e) {
        return fromThrow(e, "Preview failed.");
      }
    },
  });
}

/** Typed-confirm entry point — the UI only calls this after the operator typed
 *  the confirmation word; `confirm:true` is sent exactly as the backend gate
 *  requires. */
export function useStartMigration() {
  const queryClient = useQueryClient();
  return useMutation<DbOutcome, Error, { values: FieldValues; resume: boolean; batchSize: number }>({
    mutationFn: async ({ values, resume, batchSize }) => {
      const errors = validatePgConfig(values);
      if (Object.values(errors).some((m) => m.length > 0)) {
        return { ok: false, message: `Migration blocked by client validation: ${Object.values(errors).flat().join(" · ")}`, requestId: null, body: null };
      }
      try {
        const body = await dbApi.migrate({ ...pgPayload(values), confirm: true, resume, batch_size: batchSize, validate_checksums: true });
        return normalize(body, "Migration job started — progress below streams live from the backend worker.", "Migration refused.");
      } catch (e) {
        return fromThrow(e, "Migration start failed.");
      }
    },
    onMutate: () => {
      // clear a previous finished job's state so the bar restarts honestly
      void queryClient.invalidateQueries({ queryKey: DB_KEYS.progress });
    },
  });
}

export function useDbBackup() {
  const queryClient = useQueryClient();
  return useMutation<DbOutcome, Error, void>({
    mutationFn: async () => {
      try {
        const body = await dbApi.backup();
        return normalize(body, `Backup written${body.backup_path ? ` → ${body.backup_path}` : ""}.`, "Backup refused or failed.");
      } catch (e) {
        return fromThrow(e, "Backup failed.");
      }
    },
    onSuccess: (o) => {
      if (o.ok) void queryClient.invalidateQueries({ queryKey: DB_KEYS.databases });
    },
  });
}

export function useDbValidate() {
  return useMutation<DbOutcome, Error, void>({
    mutationFn: async () => {
      try {
        const body = await dbApi.validate();
        const v = body.validation;
        const label = typeof v === "string" ? v : (v as { status?: string } | undefined)?.status ?? "done";
        return normalize(body, `Validation finished: ${label}.`, "Validation refused.");
      } catch (e) {
        return fromThrow(e, "Validation failed.");
      }
    },
  });
}

export function useSqlQuery() {
  return useMutation<DbOutcome & { rows?: Array<Record<string, unknown>>; columns?: string[]; truncated?: boolean }, Error, { database: string; sql: string }>({
    mutationFn: async ({ database, sql }) => {
      // Read-only console — the SERVER enforces the statement allow-list; the
      // client refuses nothing except emptiness (its rules can't be stricter
      // than the truth without lying about what the console supports).
      if (sql.trim() === "") {
        return { ok: false, message: "Empty SQL — nothing sent.", requestId: null, body: null };
      }
      try {
        const body = await dbApi.consoleQuery(database, sql);
        const base = normalize(body as DbActionEnvelope, `Query ok — ${String(body.rows_returned ?? body.rows?.length ?? 0)} row(s)${body.truncated ? " (truncated at 500)" : ""}.`, `Query refused: ${typeof body.error === "string" ? body.error : "backend error"}`);
        return { ...base, rows: body.rows, columns: body.columns, truncated: body.truncated };
      } catch (e) {
        return { ...fromThrow(e, "Query failed.") };
      }
    },
  });
}

export function useRunQuickSql() {
  return useMutation<DbOutcome & { rows?: Array<Record<string, unknown>>; columns?: string[] }, Error, { database: string; table: string; kind: string }>({
    mutationFn: async ({ database, table, kind }) => {
      try {
        const body = await dbApi.consoleQuick(database, table, kind);
        const base = normalize(body as DbActionEnvelope, `Quick ${kind} ok — ${String(body.rows?.length ?? 0)} row(s).`, `Quick ${kind} refused: ${typeof body.error === "string" ? body.error : "backend error"}`);
        return { ...base, rows: body.rows, columns: body.columns };
      } catch (e) {
        return { ...fromThrow(e, "Quick query failed.") };
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
  const queryClient = useQueryClient();
  return useMutation<DbOutcome, Error, { name: string; value: string }>({
    mutationFn: async ({ name, value }) => {
      if (!/^[A-Za-z0-9_.-]{2,}$/.test(name)) {
        return { ok: false, message: "Key name must be a simple identifier (≥2 chars, no spaces/slashes) — mirrors the server rule, so no wasted POST.", requestId: null, body: null };
      }
      try {
        return normalize(await dbApi.apiKeySet(name, value), `API key "${name}" stored in the OS secret store (value never echoed).`, "Key save refused.");
      } catch (e) {
        return fromThrow(e, "Key save failed.");
      }
    },
    onSuccess: (o) => {
      if (o.ok) void queryClient.invalidateQueries({ queryKey: ["database", "console", "apikeys"] });
    },
  });
}

export function useDeleteApiKey() {
  const queryClient = useQueryClient();
  return useMutation<DbOutcome, Error, string>({
    mutationFn: async (name) => {
      try {
        return normalize(await dbApi.apiKeyDelete(name), `API key "${name}" deleted.`, "Delete refused.");
      } catch (e) {
        return fromThrow(e, "Delete failed.");
      }
    },
    onSuccess: (o) => {
      if (o.ok) void queryClient.invalidateQueries({ queryKey: ["database", "console", "apikeys"] });
    },
  });
}

export function useRefreshConsole() {
  const queryClient = useQueryClient();
  return useMutation<DbOutcome, Error, void>({
    mutationFn: async () => {
      try {
        const body = await dbApi.consoleRefresh();
        return normalize(body as DbActionEnvelope, "Database list re-scanned.", "Resync failed.");
      } catch (e) {
        return fromThrow(e, "Resync failed.");
      }
    },
    onSuccess: (o) => {
      if (o.ok) void queryClient.invalidateQueries({ queryKey: DB_KEYS.databases });
    },
  });
}
