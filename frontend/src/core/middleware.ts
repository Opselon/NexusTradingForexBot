/**
 * core/middleware — the request middleware pipeline (onion model, koa-style).
 *
 * Every HTTP call the UI makes flows through `executeRequest()` -> composed
 * middlewares -> the terminal fetch. The pipeline is OPEN for extension and
 * CLOSED for modification: `useMiddleware(mw)` appends a middleware (future
 * rate-limit, cache, retry, telemetry) without touching any built-in.
 *
 * Built-ins (in execution order):
 *  1. requestId        — X-Request-ID stamped on the way in, requestId on ctx.
 *  2. authHeader       — Bearer + X-NSE-Token from core/auth (cookie rides
 *                        anyway via credentials:"same-origin" — BUG-267).
 *  3. timeout          — AbortSignal.timeout merged with the caller signal.
 *  4. errorNormalize   — non-2xx + network failures -> ApiError (both the
 *                        v1 `{error:{...}}` and legacy safe-error envelopes).
 *  5. cookieHeal       — one-shot 401 heal on GETs: re-fetch the public
 *                        cookie-issuing asset, retry once, then give up.
 */

import { ApiError } from "@/types/api";
import { runtimeConfig } from "./config";
import { authHeaders, noteUnauthorized, refreshBootstrapCookie } from "./auth";
import { coreEvents } from "./events";

export type HttpMethod = "GET" | "POST" | "PUT" | "DELETE" | "PATCH";

/** Mutable context threaded through the pipeline; plugins may extend `meta`. */
export interface MiddlewareContext {
  /** Final request path (already absolute if runtimeConfig.apiBase is set). */
  path: string;
  method: HttpMethod;
  body: unknown;
  headers: Record<string, string>;
  /** Correlation id minted by the requestId middleware (echoed to ApiError). */
  requestId: string;
  /** Caller-provided cancellation (TanStack Query signal). */
  signal?: AbortSignal;
  /** Effective timeout; middlewares may raise it (mutations). */
  timeoutMs: number;
  /** Whether the one-shot 401 cookie heal is still permitted for this call. */
  allowHeal: boolean;
  /** Scratchpad for plug-in middlewares (cache entry, rate tokens, ...). */
  meta: Record<string, unknown>;
  /** Filled by the terminal handler. */
  response: Response | null;
}

export type Next = () => Promise<void>;
export type Middleware = (ctx: MiddlewareContext, next: Next) => Promise<void>;

let seq = 0;
function mintRequestId(): string {
  seq += 1;
  return `altui_${Date.now().toString(36)}_${seq.toString(36)}`;
}

// ---------------------------------------------------------------- built-ins

const requestIdMiddleware: Middleware = async (ctx, next) => {
  ctx.headers["X-Request-ID"] = ctx.requestId;
  // perf: no post-next work in this layer — tail-return the chain promise
  // (same execution order and error propagation, one fewer await resumption).
  return next();
};

const authHeaderMiddleware: Middleware = async (ctx, next) => {
  Object.assign(ctx.headers, authHeaders());
  await next();
};

const timeoutMiddleware: Middleware = async (ctx, next) => {
  const timeout = AbortSignal.timeout(ctx.timeoutMs);
  if (ctx.signal) {
    ctx.meta["abort"] = mergeSignals(ctx.signal, timeout);
  } else {
    ctx.meta["abort"] = timeout;
  }
  // perf: no post-next work in this layer — tail-return the chain promise
  // (same execution order and error propagation, one fewer await resumption).
  return next();
};

/** Normalize any failure (bad status / network / timeout) into ApiError. */
export const errorNormalizeMiddleware: Middleware = async (ctx, next) => {
  try {
    await next();
  } catch (e) {
    if (e instanceof DOMException && (e.name === "AbortError" || e.name === "TimeoutError")) {
      if (e.name === "TimeoutError") {
        throw new ApiError(0, "TIMEOUT", `Request timed out after ${ctx.timeoutMs}ms.`, ctx.requestId, true);
      }
      throw e; // caller-initiated abort — let TanStack Query handle it
    }
    if (e instanceof ApiError) throw e;
    throw new ApiError(0, "NETWORK_ERROR", "Network request failed — backend unreachable.", ctx.requestId, true);
  }
  const res = ctx.response;
  if (!res) return; // a plug-in (cache) answered without a Response
  if (!res.ok) throw apiErrorFromResponse(res.status, await safeJson(res), ctx.requestId);
};

/** BUG-267 one-shot cookie heal (GET only; mutations NEVER re-run). */
const cookieHealMiddleware: Middleware = async (ctx, next) => {
  try {
    await next();
  } catch (e) {
    if (
      e instanceof ApiError &&
      e.status === 401 &&
      ctx.allowHeal &&
      ctx.method === "GET" &&
      (await refreshBootstrapCookie())
    ) {
      ctx.allowHeal = false;
      ctx.response = null;
      coreEvents.publish("transport:healed", { path: ctx.path });
      await next();
      return;
    }
    if (e instanceof ApiError && e.status === 401) noteUnauthorized();
    throw e;
  }
};

// ------------------------------------------------------------------ helpers

async function safeJson(res: Response): Promise<unknown> {
  try {
    return await res.json();
  } catch {
    return null;
  }
}

/** Map BOTH envelope families ({error:{code,message,request_id}} legacy and
 *  v1 {error:{...}}) plus FastAPI {detail} onto the single ApiError VO. */
export function apiErrorFromResponse(status: number, body: unknown, requestId: string): ApiError {
  const errObj =
    body && typeof body === "object" && "error" in body
      ? ((body as { error: Record<string, unknown> }).error ?? {})
      : {};
  const code = typeof errObj.code === "string" ? errObj.code : status === 401 ? "UNAUTHORIZED" : "INTERNAL_ERROR";
  let message = typeof errObj.message === "string" ? errObj.message : "";
  if (!message && body && typeof body === "object" && "detail" in body) {
    const detail = (body as { detail: unknown }).detail;
    message = typeof detail === "string" ? detail : JSON.stringify(detail);
  }
  if (!message) {
    message =
      status === 401
        ? "Missing or invalid web auth token."
        : status === 0 || status === 502
          ? "Backend unreachable."
          : `Request failed (HTTP ${status}).`;
  }
  return new ApiError(
    status,
    code,
    message,
    typeof errObj.request_id === "string" ? errObj.request_id : requestId,
    status === 503 || status === 504,
  );
}

function mergeSignals(a: AbortSignal, b: AbortSignal): AbortSignal {
  const ctrl = new AbortController();
  const abort = (src: AbortSignal) => () => ctrl.abort(src.reason);
  if (a.aborted) return a;
  if (b.aborted) return b;
  a.addEventListener("abort", abort(a), { once: true });
  b.addEventListener("abort", abort(b), { once: true });
  return ctrl.signal;
}

// ----------------------------------------------------------------- pipeline

const builtins: Middleware[] = [
  requestIdMiddleware,
  authHeaderMiddleware,
  timeoutMiddleware,
  errorNormalizeMiddleware,
  cookieHealMiddleware,
];

const extensions: Middleware[] = [];

/**
 * Register an additional middleware (appended after builtins, before the
 * terminal fetch). Returns an unregister handle — keeps the pipeline
 * extensible without exporting a mutable array.
 */
export function useMiddleware(mw: Middleware): () => void {
  extensions.push(mw);
  return () => {
    const i = extensions.indexOf(mw);
    if (i >= 0) extensions.splice(i, 1);
  };
}

/**
 * Compose an ordered middleware list around a terminal handler into a runner
 * that takes a context. Koajs onion semantics: each layer awaits `next()`;
 * calling `next()` twice in one layer is a programming error.
 */
export function compose(middlewares: Middleware[], terminal: Next): (ctx: MiddlewareContext) => Promise<void> {
  return (ctx) => {
    let index = -1;
    const dispatch = async (i: number): Promise<void> => {
      if (i <= index) throw new Error("[core/middleware] next() called twice in one middleware");
      index = i;
      const mw = middlewares[i];
      if (!mw) return terminal();
      await mw(ctx, () => dispatch(i + 1));
    };
    return dispatch(0);
  };
}

/** Caller options (what `request()` accepts from api/ modules). */
export interface MiddlewareRequestOptions {
  method?: HttpMethod;
  body?: unknown;
  signal?: AbortSignal;
  headers?: Record<string, string>;
  timeoutMs?: number;
}

function chain(): Middleware[] {
  return [...builtins, ...extensions];
}

/**
 * Run one request through the composed pipeline. The terminal middleware is
 * the actual fetch; error/normalization/heal middlewares wrap it.
 */
export async function executeRequest(path: string, opts: MiddlewareRequestOptions = {}): Promise<MiddlewareContext> {
  const ctx: MiddlewareContext = {
    path: /^https?:\/\//i.test(path) ? path : `${runtimeConfig.apiBase}${path}`,
    method: opts.method ?? "GET",
    body: opts.body,
    headers: { ...(opts.headers ?? {}) },
    requestId: mintRequestId(),
    signal: opts.signal,
    timeoutMs: opts.timeoutMs ?? (opts.method && opts.method !== "GET" ? runtimeConfig.timeouts.mutationMs : runtimeConfig.timeouts.requestMs),
    allowHeal: true,
    meta: {},
    response: null,
  };

  const run = compose(chain(), async () => {
    // terminal: the real fetch
    const res = await fetch(ctx.path, {
      method: ctx.method,
      headers: {
        ...ctx.headers,
        ...(ctx.body !== undefined ? { "Content-Type": "application/json" } : {}),
      },
      body: ctx.body !== undefined ? JSON.stringify(ctx.body) : undefined,
      // BUG-267: pin the credential ride so a future refactor cannot
      // silently drop the HttpOnly bootstrap cookie.
      credentials: "same-origin",
      signal: (ctx.meta["abort"] as AbortSignal | undefined) ?? ctx.signal,
    });
    ctx.response = res;
  });
  await run(ctx);
  return ctx;
}

/** Current effective pipeline (introspection for the debug page). */
export function describePipeline(): string[] {
  return ["requestId", "authHeader", "timeout", "errorNormalize", "cookieHeal", ...extensions.map((_, i) => `extension[${i}]`)];
}
