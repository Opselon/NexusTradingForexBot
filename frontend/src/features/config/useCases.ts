/**
 * Config: application use cases (query/mutation orchestration, backend-
 * authoritative everywhere).
 *
 * Command flow for a settings change (validate-before-apply, hard rule):
 *   1. client validation (features/config/validation.ts) — invalid payloads
 *      are NEVER sent;
 *   2. POST /api/settings/validate {key, value} for every changed key — the
 *      server dry-runs the PROPOSED VALUE (real payload, not just a label)
 *      and answers mutability truth (invalid / RESTART_REQUIRED / SECRET /
 *      READ_ONLY keys surface inline; a refusal blocks the apply);
 *   3. POST /api/runtime-config/apply with the surviving HOT updates —
 *      RESTART_REQUIRED keys are excluded from the payload and named in the
 *      report (never silently dropped), the engine's ConfigurationApplyReport
 *      decides success;
 *   4. on any accepted/refused result the related queries are invalidated so
 *      the UI re-reads the backend (refetch-on-result, never assume).
 */

import { useMutation, useQuery, useQueryClient, type QueryKey } from "@tanstack/react-query";
import { configApi, type ConfigDto, type ModePreviewV1 } from "./api";
import {
  DEFAULT_RULES,
  backendMessage,
  hasErrors,
  identityT,
  serverErrorsToFieldErrors,
  validateFields,
  type FieldErrors,
  type FieldValues,
  type Translate,
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

function outcomeFrom(body: unknown, okMessage: string, failFallback: string, t: Translate, fieldErrors?: FieldErrors): CommandOutcome {
  const ok = (() => {
    if (!body || typeof body !== "object") return false;
    const b = body as Record<string, unknown>;
    if ("success" in b) return b.success === true;
    if ("ok" in b) return b.ok === true;
    return !b.error;
  })();
  const detail = body as { detail?: unknown; error?: unknown } | null;
  const serverErrors = ok ? undefined : serverErrorsToFieldErrors(detail ?? {}, t);
  return {
    ok,
    message: ok ? okMessage : backendMessage(body, failFallback),
    requestId: (() => {
      const err = detail?.error;
      if (!err || typeof err !== "object") return null;
      return (err as { request_id?: string }).request_id ?? null;
    })(),
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
  t: Translate = identityT,
): Promise<ApplySteps> {
  const specs = runtimeConfigSpecs(t);
  const clientErrors = validateFields(specs, changes, DEFAULT_RULES, t);
  if (hasErrors(clientErrors)) {
    return { clientErrors, serverErrors: {}, restartRequired: [], outcome: null, sent: false };
  }

  const serverErrors: FieldErrors = {};
  const restartRequired: string[] = [];
  const sendable: FieldValues = {};
  for (const key of Object.keys(changes)) {
    try {
      // TASK-CFGUI-001: send the PROPOSED VALUE so the backend dry-runs the
      // real payload (it previously answered valid:true without ever seeing
      // a value — a round-tripped lie the apply path then discovered late).
      const v = await configApi.validateSetting(key, changes[key]);
      if (!v || v.valid === false) {
        serverErrors[key] =
          Array.isArray(v?.errors) && v.errors.length > 0
            ? v.errors
            : [v?.error?.message ?? t("config.apply.marked_invalid", "backend marked \"{key}\" invalid", { key })];
        continue;
      }
      const mut = String(v.mutability ?? "").toUpperCase();
      if (mut === "READ_ONLY") {
        serverErrors[key] = [t("config.apply.read_only", "backend mutability READ_ONLY — cannot be changed at runtime")];
        continue;
      }
      if (mut === "RESTART_REQUIRED") {
        // TASK-CFGUI-001: restart-bound keys are EXCLUDED from the hot-apply
        // payload (they stay local edits and are named in the report below) —
        // never silently dropped, never shipped through a gate meant for
        // hot-reloadable keys.
        restartRequired.push(key);
        continue;
      }
      sendable[key] = changes[key];
    } catch (e) {
      serverErrors[key] = [e instanceof Error ? e.message : t("config.apply.validate_failed", "server validate call failed")];
    }
  }

  const blocked = Object.keys(serverErrors).length > 0;
  if (blocked || Object.keys(sendable).length === 0) {
    return {
      clientErrors,
      serverErrors,
      restartRequired,
      sent: false,
      outcome: blocked
        ? { ok: false, message: t("config.apply.server_refused", "Server refused one or more keys — nothing was applied (atomic gate)."), requestId: null }
        : restartRequired.length > 0
          ? {
              ok: false,
              message: t("config.apply.restart_excluded", "Needs restart — not applied: {keys}. These stay as local edits; the hot apply gate never receives restart-bound keys.", {
                keys: restartRequired.join(", "),
              }),
              requestId: null,
            }
          : { ok: false, message: t("config.apply.no_sendable", "No sendable changes after validation."), requestId: null },
    };
  }

  try {
    const report = await configApi.applyRuntime(sendable);
    const restartNote =
      restartRequired.length > 0
        ? ` ${t("config.apply.not_sent_restart", "Not sent (restart required, still local edits): {keys}.", { keys: restartRequired.join(", ") })}`
        : "";
    return {
      clientErrors,
      serverErrors,
      restartRequired,
      sent: true,
      outcome: outcomeFrom(
        report,
        (report.runtime_applied
          ? `${t("config.apply.applied_runtime", "Applied at runtime — configuration v{v}", { v: report.configuration_version })}${
              report.persisted ? ` (${t("config.apply.persisted", "persisted")})` : ""
            }.`
          : t("config.apply.saved_not_applied", "Saved but NOT applied: {reason}.", {
              reason: report.reason || t("config.apply.engine_offline_reason", "engine offline"),
            })) + restartNote,
        t("config.apply.refused_backend", "Backend refused the apply."),
        t,
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
        message: body?.message ?? t("config.apply.request_failed", "Apply request failed."),
        requestId: body?.requestId ?? null,
        fieldErrors: serverErrorsToFieldErrors({ detail: body?.message ?? "" }, t),
      },
    };
  }
}

export function useApplyRuntimeConfig(t: Translate = identityT) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (changes: FieldValues) => validateAndApplyChanges(changes, t),
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

/**
 * TASK-CFGUI-001: server-side preview for the mode-switch confirm modal —
 * POST /api/v1/runtime/mode/preview (200 for valid AND invalid proposals;
 * the verdict is the response data, never an HTTP guess).
 */
export function useModePreview() {
  return useMutation({
    mutationFn: (mode: string): Promise<ModePreviewV1> => configApi.previewModeTransition(mode),
  });
}

export function useSetEngineMode(t: Translate = identityT) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (mode: string): Promise<CommandOutcome> => {
      try {
        const body = await configApi.setEngineMode(mode);
        return outcomeFrom(
          body,
          t("config.mode.accepted", "Mode switch accepted: {mode} (persisted={persisted}).", {
            mode: body.mode ?? mode,
            persisted: String(body.persisted === true),
          }),
          t("config.mode.refused", "Backend refused the mode switch."),
          t,
        );
      } catch (e) {
        return {
          ok: false,
          message: e instanceof Error ? e.message : t("config.mode.failed", "Mode switch failed."),
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

export function useModelSwap(t: Translate = identityT) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (artifact: string): Promise<CommandOutcome> => {
      try {
        const body = await configApi.modelSwap(artifact);
        const ok = body.success === true || String(body.status ?? "").toUpperCase() === "SWAPPED";
        return {
          ok,
          message: ok
            ? `${t("config.swap.completed", "Model hot-swap completed for {artifact}", { artifact })}${body.correlation_id ? ` (${body.correlation_id})` : ""}.`
            : backendMessage(body, t("config.swap.refused", "Model swap refused.")),
          requestId: body.correlation_id ?? null,
        };
      } catch (e) {
        return {
          ok: false,
          message: e instanceof Error ? e.message : t("config.swap.failed", "Model swap failed."),
          requestId: (e as { requestId?: string } | null)?.requestId ?? null,
        };
      }
    },
    onSettled: (outcome) => {
      if (outcome?.ok) void queryClient.invalidateQueries({ queryKey: RUNTIME_CONFIG_KEY });
    },
  });
}

export function useSaveTelegram(t: Translate = identityT) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (payload: { enabled: boolean; bot_token: string; admin_id: string }): Promise<CommandOutcome> => {
      try {
        const body = await configApi.saveTelegram(payload);
        return outcomeFrom(
          body,
          `${t("config.tg.saved", "Telegram settings saved")} (${body.correlation_id ?? t("config.tg.no_corr", "no correlation id")}).`,
          t("config.tg.refused_save", "Backend refused the telegram save."),
          t,
        );
      } catch (e) {
        return {
          ok: false,
          message: e instanceof Error ? e.message : t("config.tg.save_failed", "Telegram save failed."),
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

export function useTestTelegram(t: Translate = identityT) {
  return useMutation({
    mutationFn: async (): Promise<CommandOutcome> => {
      try {
        const body = await configApi.testTelegram();
        return outcomeFrom(
          body,
          t("config.tg.delivery_ok", "Delivery confirmed — message_id {id} (correlation {c}).", {
            id: String(body.message_id ?? "?"),
            c: body.correlation_id ?? "—",
          }),
          t("config.tg.delivery_failed", "Telegram test delivery failed."),
          t,
        );
      } catch (e) {
        return {
          ok: false,
          message: e instanceof Error ? e.message : t("config.tg.test_failed", "Telegram test failed."),
          requestId: (e as { requestId?: string } | null)?.requestId ?? null,
        };
      }
    },
  });
}

/** Re-export DTO types for pages that need them. */
export type { ConfigDto, ModePreviewV1 };
