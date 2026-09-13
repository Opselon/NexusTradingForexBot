/**
 * Debug: React hooks surface bound to useCases (keys namespaced ["debug", ...]).
 * Implementations live in ./useCases (application layer owns transport
 * binding); ui/ imports only from here.
 */

export {
  useCompareQuery,
  useDebugFeaturesQuery,
  useDebugFreshnessQuery,
  useDebugHealthQuery,
  useDebugStateQuery,
  useIpcTelemetryQuery,
  useModelTest,
  useResearchRead,
  useSnapshotDetail,
  useSnapshotsQuery,
  useTraceQuery,
} from "./useCases";
export type { ModelTestOutcome } from "./useCases";
