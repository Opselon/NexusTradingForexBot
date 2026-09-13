/**
 * Config: application use cases (query/mutation orchestration, backend-
 * authoritative everywhere).
 *
 * Command flow for a settings change (validate-before-apply, hard rule):
 *   1. client validation (features/config/validation.ts) — invalid payloads
 *      are NEVER sent;
 *   2. POST /api/settings/validate {key} for every changed key — server-side
 *      mutability/validity truth (RESTART_REQUIRED / SECRET / READ_ONLY keys
 *      surface inline; a refusal blocks the apply);
 *   3. POST /api/runtime-config/apply with the surviving dotted updates — the
 *      engine's ConfigurationApplyReport decides success;
 *   4. on any accepted/refused result the related queries are invalidated so
 *      the UI re-reads the backend (refetch-on-result, never assume).
 */

import { useMutation, useQuery, useQueryClient, type QueryKey } from "@tanstack/react-query";
import { configApi, type ConfigDto } from "./api";
import {
  backendMessage,
  hasErrors,
  serverErrorsToFieldErrors,
  validateFields,
  type FieldErrors,
  type FieldValues,
} from "./validation";
import { runtimeConfigSpecs } from "./model";

export const CONFIG_QUERY_KEY: QueryKey = ["config", "form"];
export const RUNTIME_CONFIG_KEY: QueryKey = ["config", "runtime-effective"];
export const RUNTIME_DIAG_KEY: QueryKey = ["config", "runtime-diagnostics"];
export const SETTINGS_KEY: QueryKey = ["config", "settings-snapshot"];
export const TELEGRAM_STATUS_KEY: QueryKey = ["config", "telegram-status"];
export const ENGINE_MODE_KEY: QueryKey = ["config", "engine-mode"];

/* ------------------------------------------------------------------ */
/* Queries                                                             */
/* ------------------------------------------------------------------ */

export function useConfigFormQuery(paused = false) {
  return useQuery({
    queryKey: CONFIG_QUERY_KEY,
    queryFn: ({ signal }) => configApi.get(signal),
    refetchInterval: paused ? false : 60_000,
  });
}

export function useRuntimeEffectiveQuery(paused = false) {
  return useQuery({
    queryKey: RUNTIME_CONFIG_KEY,
    queryFn: ({ signal }) => configApi.runtimeEffective(signal),
    refetchInterval: paused ? false : 30_000,
  });
}

export function useRuntimeDiagnosticsQuery(paused = false) {
  return useQuery({
    queryKey: RUNTIME_DIAG_KEY,
    queryFn: ({ signal }) => configApi.runtimeDiagnostics(signal),
    refetchInterval: paused ? false : 30_000,
  });
}

export function useSettingsSnapshotQuery(paused = false) {
  return useQuery({
    queryKey: SETTINGS_KEY,
    queryFn: ({ signal }) => configApi.settings(signal),
    refetchInterval: paused ? false : 60_000,
  });
}

export function useTelegramStatusQuery(paused = false) {
  return useQuery({
    queryKey: TELEGRAM_STATUS_KEY,
    queryFn: ({ signal }) => configApi.telegramStatus(signal),
    refetchInterval: paused ? false : 30_000,
  });
}

export function useRuntimeModeQuery(paused = false) {
  return useQuery({
    queryKey: ENGINE_MODE_KEY,
    queryFn: ({ signal }) => configApi.runtimeMode(signal),
    refetchInterval: paused ? false : 15_000,
  });
}

/* ------------------------------------------------------------------ */
/* Result normalization                                                */
/* ------------------------------------------------------------------ */

export interface CommandOutcome {
  ok: boolean;
  message: string;
  requestId: string | null;
  /** Per-field errors folded back onto the form (client-visible inline). */
  fieldErrors?: FieldErrors;
  raw?: unknown;
}

function outcomeFrom(body: unknown, okMessage: string, failFallback: string, fieldErrors?: FieldErrors): CommandOutcome {
  const ok = (() => {
    if (!body || typeof body !== "object") return false;
    const b = body as Record<string, unknown>;
    if ("success" in b) return b.success === true;
    if ("ok" in b) return b.ok === true;
    return !b.error;
  })();
  const detail = body as { detail?: unknown; error?: unknown } | null;
  const serverErrors = ok ? undefined : serverErrorsToFieldErrors(detail ?? {});
  return {
    ok,
    message: ok ? okMessage : backendMessage(body, failFallback),
    requestId:
      (detail?.error && typeof detail.error === "object"
        ? (detail.error as { request_id?: string }).request_id ?? null
        : null) ?? null,
    fieldErrors: fieldErrors ?? serverErrors,
    raw: body,
  };
}

/* ------------------------------------------------------------------ */
/* Runtime-config apply (validate-before-apply)                        */
/* ------------------------------------------------------------------ */

export interface ApplySteps {
  /** Client validation problems (payload NOT sent when non-empty). */
  clientErrors: FieldErrors;
  /** Server /api/settings/validate refusals keyed by setting key. */
  serverErrors: FieldErrors;
  /** Keys the server marked RESTART_REQUIRED (apply blocked for them). */
  restartRequired: string[];
  outcome: CommandOutcome | null;
  sent: boolean;
}

/**
 * Run the full gate chain for `changes` (dotted key -> proposed value), then
 * apply what survives. Returns per-step evidence so the page can render every
 * refusal inline. Sends NOTHING when client validation fails.
 */
export async function validateAndApplyChanges(
  changes: FieldValues,
): Promise<ApplySteps> {
  const specs = runtimeConfigSpecs();
  const clientErrors = validateFields(specs, changes);
  if (hasErrors(clientErrors)) {
    return { clientErrors, serverErrors: {}, restartRequired: [], outcome: null, sent: false };
  }

  const serverErrors: FieldErrors = {};
  const restartRequired: string[] = [];
  const sendable: FieldValues = {};
  for (const key of Object.keys(changes)) {
    try {
      const v = await configApi.validateSetting(key);
      if (!v || v.valid === false) {
        serverErrors[key] = [v?.error?.message ?? `backend marked "${key}" invalid`];
        continue;
      }
      const mut = String(v.mutability ?? "").toUpperCase();
      if (mut === "READ_ONLY") {
        serverErrors[key] = ["backend mutability READ_ONLY — cannot be changed at runtime"];
        continue;
      }
      if (mut === "RESTART_REQUIRED") restartRequired.push(key);
      sendable[key] = changes[key];
    } catch (e) {
      serverErrors[key] = [e instanceof Error ? e.message : "server validate call failed"];
    }
  }

  const blocked = Object.keys(serverErrors).length > 0;
  if (blocked || restartRequired.length > 0 || Object.keys(sendable).length === 0) {
    return {
      clientErrors,
      serverErrors,
      restartRequired,
      sent: false,
      outcome: blocked
        ? { ok: false, message: "Server refused one or more keys — nothing was applied (atomic gate).", requestId: null }
        : restartRequired.length > 0
          ? { ok: false, message: `Keys ${restartRequired.join(", ")} need a restart — apply blocked for the whole batch.`, requestId: null }
          : { ok: false, message: "No sendable changes after validation.", requestId: null },
    };
  }

  try {
    const report = await configApi.applyRuntime(sendable);
    return {
      clientErrors,
      serverErrors,
      restartRequired,
      sent: true,
      outcome: outcomeFrom(
        report,
        report.runtime_applied
          ? `Applied at runtime — configuration v${report.configuration_version}${report.persisted ? " (persisted)" : ""}.`
          : `Saved but NOT applied: ${report.reason || "engine offline"}.`,
        "Backend refused the apply.",
      ),
    };
  } catch (e) {
    const body = (e as { code?: string; message?: string; requestId?: string } | null) ?? null;
    return {
      clientErrors,
      serverErrors,
      restartRequired,
      sent: true,
      outcome: {
        ok: false,
        message: body?.message ?? "Apply request failed.",
        requestId: body?.requestId ?? null,
        fieldErrors: serverErrorsToFieldErrors({ detail: body?.message ?? "" }),
      },
    };
  }
}

export function useApplyRuntimeConfig() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (changes: FieldValues) => validateAndApplyChanges(changes),
    onSettled: (steps) => {
      // Refetch-on-result: the versioned store is the authority after any
      // settled attempt (accepted OR refused — a partial apply must re-read).
      if (steps?.sent) {
        void queryClient.invalidateQueries({ queryKey: RUNTIME_CONFIG_KEY });
        void queryClient.invalidateQueries({ queryKey: RUNTIME_DIAG_KEY });
        void queryClient.invalidateQueries({ queryKey: CONFIG_QUERY_KEY });
        void queryClient.invalidateQueries({ queryKey: SETTINGS_KEY });
      }
    },
  });
}

/* ------------------------------------------------------------------ */
/* Engine mode + model swap + telegram                                 */
/* ------------------------------------------------------------------ */

export function useSetEngineMode() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (mode: string): Promise<CommandOutcome> => {
      try {
        const body = await configApi.setEngineMode(mode);
        return outcomeFrom(body, `Mode switch accepted: ${body.mode ?? mode} (persisted=${String(body.persisted === true)}).`, "Backend refused the mode switch.");
      } catch (e) {
        return {
          ok: false,
          message: e instanceof Error ? e.message : "Mode switch failed.",
          requestId: (e as { requestId?: string } | null)?.requestId ?? null,
        };
      }
    },
    onSettled: (outcome) => {
      if (outcome?.ok) {
        void queryClient.invalidateQueries({ queryKey: ENGINE_MODE_KEY });
        void queryClient.invalidateQueries({ queryKey: RUNTIME_CONFIG_KEY });
      }
    },
  });
}

export function useModelSwap() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (artifact: string): Promise<CommandOutcome> => {
      try {
        const body = await configApi.modelSwap(artifact);
        const ok = body.success === true || String(body.status ?? "").toUpperCase() === "SWAPPED";
        return {
          ok,
          message: ok
            ? `Model hot-swap completed for ${artifact}${body.correlation_id ? ` (${body.correlation_id})` : ""}.`
            : backendMessage(body, "Model swap refused."),
          requestId: body.correlation_id ?? null,
        };
      } catch (e) {
        return {
          ok: false,
          message: e instanceof Error ? e.message : "Model swap failed.",
          requestId: (e as { requestId?: string } | null)?.requestId ?? null,
        };
      }
    },
    onSettled: (outcome) => {
      if (outcome?.ok) void queryClient.invalidateQueries({ queryKey: RUNTIME_CONFIG_KEY });
    },
  });
}

export function useSaveTelegram() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (payload: { enabled: boolean; bot_token: string; admin_id: string }): Promise<CommandOutcome> => {
      try {
        const body = await configApi.saveTelegram(payload);
        return outcomeFrom(body, `Telegram settings saved (${body.correlation_id ?? "no correlation id"}).`, "Backend refused the telegram save.");
      } catch (e) {
        return {
          ok: false,
          message: e instanceof Error ? e.message : "Telegram save failed.",
          requestId: (e as { requestId?: string } | null)?.requestId ?? null,
        };
      }
    },
    onSettled: (outcome) => {
      if (outcome?.ok) {
        void queryClient.invalidateQueries({ queryKey: TELEGRAM_STATUS_KEY });
        void queryClient.invalidateQueries({ queryKey: SETTINGS_KEY });
      }
    },
  });
}

export function useTestTelegram() {
  return useMutation({
    mutationFn: async (): Promise<CommandOutcome> => {
      try {
        const body = await configApi.testTelegram();
        return outcomeFrom(
          body,
          `Delivery confirmed — message_id ${String(body.message_id ?? "?")} (correlation ${body.correlation_id ?? "—"}).`,
          "Telegram test delivery failed.",
        );
      } catch (e) {
        return {
          ok: false,
          message: e instanceof Error ? e.message : "Telegram test failed.",
          requestId: (e as { requestId?: string } | null)?.requestId ?? null,
        };
      }
    },
  });
}

/** Re-export the raw DTO type for pages that need it. */
export type { ConfigDto };
