/**
 * Settings — typed transport for the platform-settings bounded context.
 *
 * Endpoints verified in src/nexus_scalp/web/diagnostics_state_routes.py:
 *  POST /api/engine/mode                  — {mode} -> {success,mode,engine_running,runtime_mode,persisted}
 *                                            (server enforces the transition matrix: 422 on an illegal move)
 *  GET  /api/v1/runtime/mode              — {mode,effective_mode,engine_attached,replaying} (v1 envelope)
 *  POST /api/v1/runtime/mode/preview      — transition check + impact preview (never applies;
 *                                            200 for valid AND invalid proposals — verdicts are DATA;
 *                                            only a syntactically unknown mode 422s with the v1 error envelope)
 *  GET  /api/config                       — authoritative runtime snapshot (AppConfig dump, ALL secrets masked)
 *  GET  /api/runtime-config               — effective store snapshot + diagnostics
 *  GET  /api/runtime-config/diagnostics   — persistent vs runtime version truth
 *  POST /api/runtime-config/apply         — {updates:{dotted.key:value}} -> ConfigurationApplyReport
 *  POST /api/runtime-config/model-swap    — {model_artifact_path} -> hot swap result
 *  GET  /api/settings                     — {success,state,db_path,settings,recent_audit}
 *  GET  /api/settings/telegram/status     — masked telegram status + worker health
 *  POST /api/settings/telegram            — {enabled,bot_token,admin_id} -> {success,correlation_id,status}
 *  POST /api/settings/validate            — {key, value?} -> {key,mutability,valid,errors,checked}
 *                                            value present = apply-path dry-run verdict; key-only = mutability truth
 *  POST /api/telegram/test                — real delivery, final worker verdict
 *
 * Feature-local typed surface over the stable `@/api/client` transport
 * (lane 1 owns src/api/* modules — no cross-lane import races).
 */

import { getLegacy, getV1, send } from "@/api/client";

/* ------------------------------ engine mode ------------------------------ */

export interface RuntimeModeV1 {
  mode: string | null;
  effective_mode: string | null;
  engine_attached: boolean;
  replaying: boolean | null;
}

export interface EngineModeResult {
  success?: boolean;
  mode?: string;
  engine_running?: boolean;
  runtime_mode?: string;
  persisted?: boolean;
  error?: { code?: string; message?: string; request_id?: string };
  detail?: unknown;
}

/**
 * TASK-CFGUI-001: real POST /api/v1/runtime/mode/preview envelope (v1
 * {data: ...} unwrapped). The SERVER owns the transition matrix; every
 * verdict is DATA — a legal move AND an illegal move both answer 200; only
 * an unknown mode name 422s with the error envelope (apiErrorFromResponse).
 * Replaces the flat ModeValidationV1 + /mode/validate pair: that route
 * answers an ILLEGAL move with HTTP 422 + {data:{valid:false}}, which
 * apiErrorFromResponse cannot read — the verdicts never reached the UI.
 */
export interface ModePreviewV1 {
  validation: {
    valid: boolean;
    current_mode: string | null;
    proposed_mode: string;
    errors: string[];
    warnings: string[];
  };
  impact: {
    proposed_mode: string;
    touches: string[];
    matrix: Record<string, boolean>;
    api_mutations_unlocked: unknown[];
  };
  /** Present and false only when validation failed (server says: do not apply). */
  applies?: boolean;
}

/* ------------------------------ runtime config ------------------------------ */

/** GET /api/config — AppConfig dump (sections the form owns are typed; the
 *  rest is passed through untouched). */
export interface ConfigDto {
  execution?: {
    symbol?: string | null;
    timeframe?: string | null;
    mode?: string | null;
    magic_number?: number | null;
    max_slippage_points?: number | null;
    effective_scope?: string | null;
    enabled_symbols?: string[] | null;
    [key: string]: unknown;
  };
  risk?: {
    max_account_drawdown_pct?: number | null;
    risk_per_trade_pct?: number | null;
    max_concurrent_positions?: number | null;
    max_spread_points?: number | null;
    max_allowed_lots?: number | null;
    enforce_stop_loss?: boolean | null;
    [key: string]: unknown;
  };
  model?: {
    confidence_threshold?: number | null;
    model_artifact_path?: string | null;
    feature_schema_version?: string | null;
    [key: string]: unknown;
  };
  telegram?: {
    enabled?: boolean | null;
    bot_token?: string | null;
    admin_id?: string | null;
    [key: string]: unknown;
  };
  configuration_version?: number;
  runtime_applied?: boolean;
  [key: string]: unknown;
}

export interface RuntimeConfigEffective {
  success: boolean;
  reason?: string;
  configuration_version?: number;
  runtime_applied?: boolean;
  source?: string;
  updated_at?: string;
  effective?: Record<string, unknown>;
  diagnostics?: Record<string, unknown>;
  secret_masked?: { telegram_token?: string };
}

export interface RuntimeConfigDiagnostics {
  success: boolean;
  persistent_version: number | null;
  runtime_version: number | null;
  live_yaml_version: number | null;
  live_yaml_hash: string;
  live_yaml_exists: boolean;
  last_apply_status: string;
  last_apply_error: string;
  mismatch: boolean;
}

/** ConfigurationApplyReport.to_dict() + runtime_version (route adds it). */
export interface ApplyReport {
  success: boolean;
  persisted: boolean;
  runtime_applied: boolean;
  configuration_version: number;
  correlation_id: string;
  reason?: string;
  requested_fields?: string[];
  previous_version?: number;
  runtime_version?: number;
  error?: { code?: string; message?: string; request_id?: string };
  detail?: unknown;
}

export interface ModelSwapResult {
  success?: boolean;
  status?: string;
  reason?: string;
  correlation_id?: string;
  artifact_path?: string;
  [key: string]: unknown;
}

/* ------------------------------ settings editor ------------------------------ */

export interface SettingRow {
  value: unknown;
  source?: string;
  version?: number;
  mutability?: string;
}

export interface SettingsSnapshotDto {
  success: boolean;
  state?: string;
  db_path?: string;
  settings: Record<string, SettingRow | Record<string, unknown>> & {
    telegram?: Record<string, unknown>;
  };
  recent_audit?: Array<Record<string, unknown>>;
}

export interface ValidateSettingResult {
  /** Legacy envelope field — the route itself answers with the fields below. */
  success?: boolean;
  key: string;
  mutability: string;
  valid: boolean;
  /** TASK-CFGUI-001: backend reason list when valid is false (value dry-run). */
  errors?: string[];
  /** TASK-CFGUI-001: true only when a proposed value was actually dry-run. */
  checked?: boolean;
  error?: { code?: string; message?: string; request_id?: string };
}

/* ------------------------------ telegram ------------------------------ */

export interface TelegramStatusDto {
  success?: boolean;
  enabled?: boolean;
  configured?: boolean;
  token_present?: boolean;
  token_length?: number;
  masked_token?: string;
  token_status?: string;
  admin_id_present?: boolean;
  admin_id_shape_valid?: boolean;
  source?: string;
  state?: string;
  worker?: Record<string, unknown>;
  error?: { code?: string; message?: string; request_id?: string };
}

export interface TelegramSaveResult {
  success?: boolean;
  correlation_id?: string;
  status?: TelegramStatusDto;
  error?: { code?: string; message?: string; request_id?: string };
  message?: string;
}

export interface TelegramTestResult {
  success?: boolean;
  message_id?: number | string | null;
  correlation_id?: string | null;
  notification_id?: string | null;
  error?: { code?: string; message?: string; request_id?: string };
  message?: string;
}

export const configApi = {
  runtimeMode: (signal?: AbortSignal): Promise<RuntimeModeV1> =>
    getV1<RuntimeModeV1>("/api/v1/runtime/mode", signal),

  /**
   * TASK-CFGUI-001: server-side transition check + impact preview before the
   * typed confirm. 200 for valid AND invalid proposals; verdicts are data
   * (validation.valid/errors/warnings + impact.touches). The UI's matrix in
   * model.ts is only a fast pre-filter — this is the server truth.
   */
  previewModeTransition: (mode: string): Promise<ModePreviewV1> =>
    send<{ data: ModePreviewV1 }>("/api/v1/runtime/mode/preview", { mode }).then((env) => env.data),

  setEngineMode: (mode: string): Promise<EngineModeResult> => send<EngineModeResult>("/api/engine/mode", { mode }),

  get: (signal?: AbortSignal): Promise<ConfigDto> => getLegacy<ConfigDto>("/api/config", signal),

  runtimeEffective: (signal?: AbortSignal): Promise<RuntimeConfigEffective> =>
    getLegacy<RuntimeConfigEffective>("/api/runtime-config", signal),

  runtimeDiagnostics: (signal?: AbortSignal): Promise<RuntimeConfigDiagnostics> =>
    getLegacy<RuntimeConfigDiagnostics>("/api/runtime-config/diagnostics", signal),

  applyRuntime: (updates: Record<string, unknown>): Promise<ApplyReport> =>
    send<ApplyReport>("/api/runtime-config/apply", { updates, source: "WEB_UI" }),

  modelSwap: (modelArtifactPath: string): Promise<ModelSwapResult> =>
    send<ModelSwapResult>("/api/runtime-config/model-swap", { model_artifact_path: modelArtifactPath }),

  settings: (signal?: AbortSignal): Promise<SettingsSnapshotDto> => getLegacy<SettingsSnapshotDto>("/api/settings", signal),

  /**
   * TASK-CFGUI-001: the apply-path dry-run. WITH a value the server builds
   * and validates the proposed configuration, answering {valid, errors[],
   * checked:true}; key-only keeps the legacy {mutability} answer
   * (checked:false) for probes that never send a value.
   */
  validateSetting: (key: string, value?: unknown): Promise<ValidateSettingResult> =>
    send<ValidateSettingResult>("/api/settings/validate", value === undefined ? { key } : { key, value }),

  telegramStatus: (signal?: AbortSignal): Promise<TelegramStatusDto> =>
    getLegacy<TelegramStatusDto>("/api/settings/telegram/status", signal),

  saveTelegram: (payload: { enabled: boolean; bot_token: string; admin_id: string }): Promise<TelegramSaveResult> =>
    send<TelegramSaveResult>("/api/settings/telegram", payload),

  testTelegram: (): Promise<TelegramTestResult> => send<TelegramTestResult>("/api/telegram/test", {}),
};
