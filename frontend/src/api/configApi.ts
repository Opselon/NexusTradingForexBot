/**
 * configApi — configuration, runtime-config, settings, telegram (legacy raw).
 *
 * Routes (diagnostics_state_routes.py unless noted):
 *  GET  /api/config                       authoritative runtime config dump
 *  POST /api/config                       save (telegram secrets go to the
 *                                         secure store, never plaintext YAML)
 *  GET  /api/runtime-config               live runtime config snapshot
 *  GET  /api/runtime-config/diagnostics   drift/diagnostics view
 *  POST /api/runtime-config/apply         apply staged runtime changes
 *  POST /api/runtime-config/model-swap    hot-swap the serving model
 *  GET  /api/settings                     settings envelope
 *  POST /api/settings/validate            validate a candidate settings blob
 *  GET  /api/settings/telegram/status      telegram notifier status (masked)
 *  POST /api/settings/telegram            telegram settings write
 *  POST /api/telegram/test                send a test notification
 *  GET  /api/algo/config                  algo params (server.py)
 *  PUT  /api/algo/config                  algo params write
 * BUG-072: bot_token arrives masked — never display it as editable plaintext.
 */

import { getLegacy, send } from "@/core/transport";
import type {
  AlgoConfig,
  RuntimeApplyResult,
  RuntimeConfigSnapshot,
  RuntimeDiagnostics,
  SettingsEnvelope,
  TelegramSettingsPayload,
  TelegramStatus,
} from "@/types/features";
import type { LegacyMutationResult } from "@/types/api";

export const configApi = {
  // ------------------------------------------------------------ app config
  config: (signal?: AbortSignal): Promise<Record<string, unknown>> =>
    getLegacy<Record<string, unknown>>("/api/config", signal),

  saveConfig: (rawConfig: Record<string, unknown>): Promise<Record<string, unknown>> =>
    send<Record<string, unknown>>("/api/config", rawConfig),

  // ---------------------------------------------------------- runtime config
  runtimeConfig: (signal?: AbortSignal): Promise<RuntimeConfigSnapshot> =>
    getLegacy<RuntimeConfigSnapshot>("/api/runtime-config", signal),

  runtimeDiagnostics: (signal?: AbortSignal): Promise<RuntimeDiagnostics> =>
    getLegacy<RuntimeDiagnostics>("/api/runtime-config/diagnostics", signal),

  runtimeApply: (payload: Record<string, unknown>): Promise<RuntimeApplyResult> =>
    send<RuntimeApplyResult>("/api/runtime-config/apply", payload),

  runtimeModelSwap: (payload: Record<string, unknown>): Promise<RuntimeApplyResult> =>
    send<RuntimeApplyResult>("/api/runtime-config/model-swap", payload),

  // -------------------------------------------------------------- settings
  settings: (signal?: AbortSignal): Promise<SettingsEnvelope> =>
    getLegacy<SettingsEnvelope>("/api/settings", signal),

  validateSettings: (payload: Record<string, unknown>): Promise<Record<string, unknown>> =>
    send<Record<string, unknown>>("/api/settings/validate", payload),

  telegramStatus: (signal?: AbortSignal): Promise<TelegramStatus> =>
    getLegacy<TelegramStatus>("/api/settings/telegram/status", signal),

  saveTelegram: (payload: TelegramSettingsPayload): Promise<LegacyMutationResult> =>
    send<LegacyMutationResult>("/api/settings/telegram", payload),

  testTelegram: (payload: Record<string, unknown> = {}): Promise<LegacyMutationResult> =>
    send<LegacyMutationResult>("/api/telegram/test", payload),

  // ----------------------------------------------------------- algo config
  algoConfig: (signal?: AbortSignal): Promise<AlgoConfig> =>
    getLegacy<AlgoConfig>("/api/algo/config", signal),

  saveAlgoConfig: (payload: AlgoConfig): Promise<LegacyMutationResult> =>
    send<LegacyMutationResult>("/api/algo/config", payload, "PUT"),
};
