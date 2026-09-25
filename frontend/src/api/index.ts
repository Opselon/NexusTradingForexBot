/**
 * Centralized API barrel. Pages/features import from `@/api` — raw fetch()
 * never appears outside the transport (now @/core/middleware + core/transport).
 *
 * Envelope rule (UI_WAVE_SPEC): `/api/v1/*` routes unwrap {data,meta} via
 * getV1; legacy `/api/*` routes return raw JSON via getLegacy. Each module's
 * docstring records which producer file backs it (shared/ROUTES.txt EDD).
 */

export { ApiError } from "@/types/api";
// Back-compat: getAuthToken/clearAuthToken re-exported from the shim client.
export { getAuthToken, clearAuthToken, getV1, getLegacy, send } from "./client";

// --- existing bounded contexts ---
export { engineApi } from "./engineApi";
export { marketApi } from "./marketApi";
export { tradingApi, SUPPORTED_ACTIONS } from "./tradingApi";
export type { ModifyPositionPayload } from "./tradingApi";
export { positionsApi } from "./positionsApi";
export { riskApi } from "./riskApi";
export { mlApi } from "./mlApi";
export { intelligenceApi } from "./intelligenceApi";
export { auditApi } from "./auditApi";

// --- lane-1 completion: full typed surface for every legacy tab ---
export { indicatorsApi } from "./indicatorsApi";
export type { IndicatorQuery } from "./indicatorsApi";
export { newsApi } from "./newsApi";
export { rulesApi } from "./rulesApi";
export { configApi } from "./configApi";
export { debugApi } from "./debugApi";
export { dbApi, incidentZipUrl } from "./dbApi";
export type { DbRowsQuery } from "./dbApi";
export { marketplaceApi } from "./marketplaceApi";
export { accountingApi } from "./accountingApi";
export { governanceApi } from "./governanceApi";
export { researchApi } from "./researchApi";
export { incidentsApi } from "./incidentsApi";
export { liquidityApi } from "./liquidityApi";
export { commandCenterApi } from "./commandCenterApi";
export { replayApi } from "./replayApi";
