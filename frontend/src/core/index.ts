/**
 * core/ barrel — the platform backbone's public surface.
 *
 * Import from `@/core` in api/ modules and hooks/. Feature UI never imports
 * core (dependency rule: components/hooks -> useCases -> api -> core).
 */

export * from "./config";
export * from "./events";
export {
  executeRequest,
  useMiddleware,
  describePipeline,
  compose,
  apiErrorFromResponse,
  type Middleware,
  type MiddlewareContext,
  type MiddlewareRequestOptions,
  type HttpMethod,
} from "./middleware";
export {
  getV1,
  getLegacy,
  send,
  drop,
  raw,
} from "./transport";
export * as auth from "./auth";
export {
  resolveToken,
  getAuthState,
  hasAccessToken,
  authHeaders,
  authTokenQuery,
  login,
  logout,
  clearAuthToken,
  refreshBootstrapCookie,
  noteCookieBootstrap,
  noteUnauthorized,
  type AuthState,
} from "./auth";
export {
  realtimeClient,
  NseRealtimeClient,
  emptySnapshot,
} from "./realtime";
