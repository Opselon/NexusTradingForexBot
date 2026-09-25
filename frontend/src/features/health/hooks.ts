/**
 * Health: React Query hooks surface (keys namespaced ["health", ...]).
 * Implementations live in ./useCases; ui/ imports only from here.
 */

export {
  HEALTH_KEYS,
  HEALTH_POLL_MS,
  useCapabilities,
  useDebugHealth,
  useForensicsHealth,
  useMt5,
  useNewsHealth,
  useProbe,
  useReadiness,
  useRuntime,
  useSystemHealth,
  useSystemStatus,
  useVersion,
  useWorkers,
} from "./useCases";
