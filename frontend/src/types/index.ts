/**
 * Shared page-level type re-exports. Domain types live in ./domain.ts,
 * transport types in ./api.ts, realtime in ./realtime.ts. Pages import from
 * "@/types" (this file re-exports everything they need).
 */

export type {
  ApiError as ApiErrorType,
  LegacyMutationResult,
  V1Envelope,
  V1ErrorEnvelope,
  V1Meta,
} from "./api";
export * from "./domain";
export * from "./realtime";
