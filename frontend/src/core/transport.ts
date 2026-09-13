/**
 * core/transport — the typed request façade every `api/` module calls.
 *
 * This is the platform's ONLY network entry point for REST. It owns nothing
 * itself: auth, headers, request-ids, timeouts, error normalization and the
 * BUG-267 one-shot 401 cookie heal all live in `core/middleware` (the
 * pipeline), and this file just unwraps envelopes and returns bodies.
 *
 * Envelope contract (shared/ROUTES.txt + web/api_v1/common.py):
 *  - `/api/v1/*`   -> `{data, meta}`: use getV1() (returns `data`, meta dropped).
 *  - legacy `/api/*` -> raw JSON: use getLegacy().
 *  - mutations     -> send() (POST/PUT); heal never retries them.
 *
 * Feature/UI layers MUST NOT import this file (dependency rule) — they go
 * through useCases -> api -> core.
 */

import type { V1Envelope } from "@/types/api";
import { executeRequest, type HttpMethod, type MiddlewareRequestOptions } from "./middleware";

export type { MiddlewareRequestOptions } from "./middleware";
export { runtimeConfig, apiUrl, featureFlag, toQuery } from "./config";
export {
  compose,
  executeRequest,
  useMiddleware,
  describePipeline,
  type Middleware,
  type MiddlewareContext,
} from "./middleware";
export * as auth from "./auth";
export { authHeaders, getAuthState, hasAccessToken, login, logout, resolveToken } from "./auth";
export { coreEvents, onCore, type CoreEventMap, type CoreTopic } from "./events";

async function requestRaw<T>(path: string, opts: MiddlewareRequestOptions = {}): Promise<T> {
  const ctx = await executeRequest(path, opts);
  const res = ctx.response;
  if (!res) throw new Error(`[core/transport] no response captured for ${path}`);
  let body: unknown = null;
  try {
    body = await res.json();
  } catch {
    body = null; // 204/empty body — callers type it as unknown/null
  }
  return body as T;
}

/** GET a v1 envelope and return the unwrapped payload (meta dropped). */
export async function getV1<T>(path: string, signal?: AbortSignal): Promise<T> {
  const env = await requestRaw<V1Envelope<T>>(path, { signal });
  return env.data;
}

/** GET a legacy (raw JSON) endpoint. */
export async function getLegacy<T>(path: string, signal?: AbortSignal): Promise<T> {
  return requestRaw<T>(path, { signal });
}

/** POST/PUT helper returning the raw response body (mutations: no heal retry). */
export async function send<T>(path: string, body?: unknown, method: "POST" | "PUT" = "POST"): Promise<T> {
  return requestRaw<T>(path, { method, body: body ?? {} });
}

/** POST to a v1 route, returning the FULL envelope ({data,meta}) — v1
 *  mutations also wrap success (web/api_v1/common.py ok()). Callers that
 *  only want the payload unwrap `env.data` themselves (see api/marketplace). */
export async function sendV1<E = unknown>(path: string, body?: unknown, method: "POST" | "PUT" = "POST"): Promise<E> {
  return requestRaw<E>(path, { method, body: body ?? {} });
}

/** DELETE helper (backend has few; db console api-key revoke). */
export async function drop<T>(path: string): Promise<T> {
  return requestRaw<T>(path, { method: "DELETE" as HttpMethod });
}

/** Escape hatch for non-JSON (zip/report downloads) returning the raw Response. */
export async function raw(path: string, opts: MiddlewareRequestOptions = {}): Promise<Response> {
  const ctx = await executeRequest(path, opts);
  if (!ctx.response) throw new Error(`[core/transport] no response captured for ${path}`);
  return ctx.response;
}
