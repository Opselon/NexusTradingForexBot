/**
 * Config: React Query hooks surface (keys namespaced ["config", ...]).
 * Implementations live in ./useCases; ui/ imports only from here.
 */

export {
  CONFIG_QUERY_KEY,
  ENGINE_MODE_KEY,
  RUNTIME_CONFIG_KEY,
  RUNTIME_DIAG_KEY,
  SETTINGS_KEY,
  TELEGRAM_STATUS_KEY,
  useApplyRuntimeConfig,
  useConfigFormQuery,
  useModelSwap,
  useRuntimeDiagnosticsQuery,
  useRuntimeEffectiveQuery,
  useRuntimeModeQuery,
  useSaveTelegram,
  useSetEngineMode,
  useSettingsSnapshotQuery,
  useTelegramStatusQuery,
  useTestTelegram,
} from "./useCases";
export type { ApplySteps, CommandOutcome } from "./useCases";
