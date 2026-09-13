/**
 * api/client — BACK-COMPAT SHIM.
 *
 * The transport moved to `@/core/transport` (pipeline in @/core/middleware,
 * auth in @/core/auth). This file re-exports the exact same public names so
 * every existing lane import (`@/api/client`, `@/api`) keeps working — the
 * UI_WAVE_SPEC git protocol forbids breaking other lanes' imports mid-wave.
 *
 * New code imports from `@/core` (api modules) or `@/api` (everything else).
 * Nothing may implement logic here anymore.
 */

import { logout, resolveToken } from "@/core/auth";
import { getLegacy, getV1, send } from "@/core/transport";

/** Kept name for the old options bag (some pages typed against it). */
export type RequestOptions = import("@/core/middleware").MiddlewareRequestOptions;

/** @deprecated use `resolveToken()` from `@/core` — kept for back-compat. */
export function getAuthToken(): string | null {
  return resolveToken();
}

/** @deprecated use `logout()` from `@/core` — kept for back-compat. */
export function clearAuthToken(): void {
  logout();
}

export { getV1, getLegacy, send };
export { ApiError } from "@/types/api";
export type { V1Envelope } from "@/types/api";
export { authHeaders, hasAccessToken, login, logout, getAuthState } from "@/core/auth";
export type { AuthState } from "@/core/auth";
export { realtimeClient } from "@/core/realtime";
