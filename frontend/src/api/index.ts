/**
 * Centralized API barrel. Pages import from `@/api` — raw fetch() never
 * appears outside src/api/client.ts.
 */

export { ApiError } from "@/types/api";
export {
  getAuthToken,
  clearAuthToken,
  request,
  getV1,
  getLegacy,
  send,
  normalizeErrorEnvelope,
  DEFAULT_READ_TIMEOUT_MS,
  DEFAULT_MUTATION_TIMEOUT_MS,
} from "./client";
export type { RequestOptions } from "./client";
export { engineApi } from "./engineApi";
export { marketApi } from "./marketApi";
export { tradingApi, SUPPORTED_ACTIONS } from "./tradingApi";
export type { ModifyPositionPayload } from "./tradingApi";
export { positionsApi } from "./positionsApi";
export { riskApi } from "./riskApi";
export { mlApi } from "./mlApi";
export { intelligenceApi } from "./intelligenceApi";
export { auditApi } from "./auditApi";
