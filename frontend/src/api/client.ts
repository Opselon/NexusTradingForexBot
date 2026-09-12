/**
 * Central HTTP transport for the NSE Alternative UI.
 *
 * Contract:
 *  - ALL backend communication goes through here (no scattered fetch()).
 *    The realtime stream (the long-lived browser stream route owned by
 *    src/websocket/realtimeSocket.ts) is deliberately NOT served by this
 *    module: this file never creates one and never applies a timeout/abort
 *    to a streaming connection.
 *  - Auth: the backend enforces WEB-AUTH-P0 token auth (Bearer / X-NSE-Token /
 *    ?token=). The token is supplied by the operator at runtime (query param
 *    `?token=` on first load is kept ONLY in sessionStorage so a page refresh
 *    keeps working; it never lands in localStorage or the repo).
 *    PINNED BEHAVIOR: the ?token= capture -> sessionStorage -> URL-scrub flow
 *    in resolveToken() is a shipped operator contract (deep-link bootstrap);
 *    do not "improve" it without a change-control entry.
 *  - Errors normalize to ApiError (v1 `{error:{code,message,details,
 *    request_id,retryable}}`, legacy `{available,success,error:{code,message,
 *    request_id}}`, bare `{error:"<str>"}`, and non-JSON/HTML failure bodies).
 *  - requestId fidelity: every ApiError carries the most trustworthy id
 *    available, in this precedence order (backend facts beat client guesses):
 *      1. envelope `error.request_id` (the id the backend logged),
 *      2. the echoed `X-Request-ID` response header (web/errors.py
 *         attach_request_id_middleware reuses the incoming client header, so
 *         this equals the id below whenever the response came from the NSE
 *         server),
 *      3. the client correlation id this module generated (also sent as the
 *         `X-Request-ID` request header — the backend accepts it for
 *         correlation), so even pure network failures / timeouts surface a
 *         typed requestId operators can grep server logs for.
 *  - Per-request timeout via AbortSignal.timeout when the runtime has it;
 *    otherwise a manual AbortController + setTimeout fallback. A
 *    caller-supplied signal abort ALWAYS wins (react-query cancellation must
 *    keep surfacing a plain AbortError, never a fabricated ApiError).
 *  - No retries on mutations; GET retries are handled by TanStack Query.
 *    This module never re-dispatches a request.
 *  - Query-string conventions (backend wire contract, snake_case): callers
 *    build query strings with URLSearchParams using the EXACT snake_case keys
 *    the backend routes declare (page, page_size, limit, offset, status,
 *    severity, event_type, symbol). camelCase never reaches the wire; react-
 *    query cache keys (kebab-case + filter values) stay UI-side only.
 */

import { ApiError, type V1Envelope } from "@/types/api";

const TOKEN_STORAGE_KEY = "nse.altui.token";

/** Abort budget for reads. Generous vs. the backend's own request budgets. */
export const DEFAULT_READ_TIMEOUT_MS = 15_000;
/** Abort budget for commands — longer; a slow engine toggle is still ONE command. */
export const DEFAULT_MUTATION_TIMEOUT_MS = 30_000;

function resolveToken(): string | null {
  // 1. Explicitly set at boot (SPA shell can inject before bundle loads).
  const w = window as unknown as { __NSE_WEB_TOKEN__?: string };
  if (w.__NSE_WEB_TOKEN__) return w.__NSE_WEB_TOKEN__;
  // 2. Query param on first load (?token= — the backend's SSE-friendly path).
  const qp = new URLSearchParams(window.location.search).get("token");
  if (qp) {
    try {
      sessionStorage.setItem(TOKEN_STORAGE_KEY, qp);
      // Clean the URL so the token is not re-shared via copy/paste.
      const url = new URL(window.location.href);
      url.searchParams.delete("token");
      window.history.replaceState(null, "", url.toString());
    } catch {
      /* sessionStorage unavailable — keep in-memory only */
    }
    return qp;
  }
  // 3. Persisted for this tab session only.
  try {
    return sessionStorage.getItem(TOKEN_STORAGE_KEY);
  } catch {
    return null;
  }
}

export function getAuthToken(): string | null {
  return resolveToken();
}

export function clearAuthToken(): void {
  try {
    sessionStorage.removeItem(TOKEN_STORAGE_KEY);
  } catch {
    /* ignore */
  }
}

function authHeaders(): Record<string, string> {
  const token = resolveToken();
  if (!token) return {};
  return { Authorization: `Bearer ${token}`, "X-NSE-Token": token };
}

let requestSeq = 0;

export interface RequestOptions {
  method?: "GET" | "POST" | "PUT" | "DELETE";
  body?: unknown;
  signal?: AbortSignal;
  /** Additional headers (rare). */
  headers?: Record<string, string>;
  /**
   * Per-request abort budget in ms. Defaults: reads 15s, commands 30s.
   * Non-finite / <= 0 values fall back to the default (a request is NEVER
   * left unbounded — that is exactly what this hardening pass fixes).
   */
  timeoutMs?: number;
}

async function parseBody(res: Response): Promise<unknown> {
  // 204/205/empty bodies reject inside res.json() — that is a SHAPE fact, not
  // an error: resolve to null so callers normalize instead of crashing.
  try {
    return await res.json();
  } catch {
    return null;
  }
}

/**
 * PURE response-shape normalizer: (status, body, requestId) -> ApiError.
 *
 * Erasable-TS only (this whole module is, so Node can import it directly
 * under type stripping — see tests/js/pro_api_client.test.mjs). Never
 * fabricates structure: bodies that are not trustworthy JSON (HTML error
 * pages, plain text, arrays) surface the honest HTTP status message only.
 */
export function normalizeErrorEnvelope(
  status: number,
  body: unknown,
  requestId?: string | null,
): ApiError {
  let code: string | null = null;
  let message = "";
  let rid: string | null = null;
  let retryable: boolean | null = null;

  if (body !== null && typeof body === "object" && !Array.isArray(body)) {
    const rec = body as Record<string, unknown>;
    const err = rec.error;
    if (err !== null && typeof err === "object" && !Array.isArray(err)) {
      // v1 envelope AND the safe legacy envelope share this shape.
      const e = err as Record<string, unknown>;
      if (typeof e.code === "string" && e.code) code = e.code;
      if (typeof e.message === "string") message = e.message;
      if (typeof e.request_id === "string" && e.request_id) rid = e.request_id;
      if (typeof e.retryable === "boolean") retryable = e.retryable;
    } else if (typeof err === "string" && err.trim()) {
      // Pre-hardening legacy shape: {"error": "<text>"} (web/errors.py notes
      // it was replaced by the object envelope — still normalized, not dropped).
      message = err.trim();
    }
    if (!message && rec.detail !== undefined) {
      // FastAPI default validation/HTTPException shape.
      const detail = rec.detail;
      message = typeof detail === "string" ? detail : JSON.stringify(detail);
    }
  }
  // else: null (empty/undecodable body), string (HTML page), array — no
  // trustworthy structure; the generic status-driven message below is used.

  if (!code) {
    code = status === 401 ? "UNAUTHORIZED" : status === 0 ? "NETWORK_ERROR" : "INTERNAL_ERROR";
  }
  if (!message) {
    message =
      status === 401
        ? "Missing or invalid web auth token."
        : status === 0 || status === 502
          ? "Backend unreachable."
          : status === 504
            ? "Request timed out at the backend."
            : `Request failed (HTTP ${status}).`;
  }
  return new ApiError(
    status,
    code,
    message,
    rid ?? requestId ?? null,
    // Envelope `retryable` is the backend's own verdict and wins; the status
    // heuristic (503/504) is only the fallback for envelope-less failures.
    retryable ?? (status === 503 || status === 504),
  );
}

function nativeAbortTimeoutAvailable(): boolean {
  return typeof AbortSignal === "function" && typeof AbortSignal.timeout === "function";
}

interface TimeoutWatch {
  controller: AbortController;
  /** True once the LOCAL budget (not the caller) aborted the request. */
  timedOut: () => boolean;
  dispose: () => void;
}

/**
 * Feature-guarded per-request abort budget:
 *  - AbortSignal.timeout where the runtime has it (never leaks a timer),
 *  - manual AbortController + setTimeout fallback otherwise.
 * `disabled` (stream routes) arms NO timer: the controller only aborts when
 * the caller wires a signal to it.
 */
function armTimeout(disabled: boolean, timeoutMs: number): TimeoutWatch {
  const controller = new AbortController();
  let fired = false;
  let timer: ReturnType<typeof setTimeout> | undefined;
  if (disabled) {
    return {
      controller,
      timedOut: () => false,
      dispose: () => {},
    };
  }
  if (nativeAbortTimeoutAvailable()) {
    const sig = AbortSignal.timeout(timeoutMs);
    const onTimeout = () => {
      fired = true;
      controller.abort(sig.reason);
    };
    if (sig.aborted) onTimeout();
    else sig.addEventListener("abort", onTimeout, { once: true });
  } else {
    timer = setTimeout(() => {
      fired = true;
      controller.abort();
    }, timeoutMs);
    // Node (test) only: never keep the event loop alive for a watchdog.
    (timer as unknown as { unref?: () => void }).unref?.();
  }
  return {
    controller,
    timedOut: () => fired,
    dispose: () => {
      if (timer !== undefined) clearTimeout(timer);
    },
  };
}

type EnvelopeMode = "raw" | "v1";

/**
 * Stream routes owned by the realtime client (src/websocket/realtimeSocket.ts
 * drives them with a long-lived browser stream connection). They are
 * PINNED OUT of any timeout/abort budget: a per-request deadline on a stream
 * kills a healthy live feed. This wrapper refuses to arm a timeout for them
 * even if a future caller routes a stream path through it.
 */
const STREAM_PATHS: readonly string[] = ["/api/ticks/stream"];

export function isStreamPath(path: string): boolean {
  const clean = (path || "").split("?")[0] ?? "";
  return STREAM_PATHS.some((s) => clean === s || clean.endsWith(s));
}

async function rawRequest<T>(path: string, opts: RequestOptions, envelope: EnvelopeMode): Promise<T> {
  requestSeq += 1;
  const rid = `altui_${Date.now().toString(36)}_${requestSeq.toString(36)}`;
  const method = opts.method ?? "GET";
  let timeoutMs = opts.timeoutMs ?? (method === "GET" ? DEFAULT_READ_TIMEOUT_MS : DEFAULT_MUTATION_TIMEOUT_MS);
  if (!Number.isFinite(timeoutMs) || timeoutMs <= 0) {
    timeoutMs = method === "GET" ? DEFAULT_READ_TIMEOUT_MS : DEFAULT_MUTATION_TIMEOUT_MS;
  }
  // Streams: caller cancellation only, never a local budget.
  const stream = isStreamPath(path);

  const watch = armTimeout(stream, timeoutMs);
  const onCallerAbort = () => watch.controller.abort(opts.signal?.reason);
  if (opts.signal) {
    if (opts.signal.aborted) onCallerAbort();
    else opts.signal.addEventListener("abort", onCallerAbort, { once: true });
  }

  let res: Response;
  try {
    res = await fetch(path, {
      method,
      headers: {
        "X-Request-ID": rid,
        ...authHeaders(),
        ...(opts.body !== undefined ? { "Content-Type": "application/json" } : {}),
        ...(opts.headers ?? {}),
      },
      body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
      signal: watch.controller.signal,
    });
  } catch (e) {
    // Caller cancellation wins: preserve the plain AbortError react-query
    // expects (unmounted queries must NOT look like backend failures).
    if (opts.signal?.aborted) throw e;
    if (watch.timedOut()) {
      throw new ApiError(0, "TIMEOUT", `Request timed out after ${timeoutMs} ms.`, rid, true);
    }
    throw new ApiError(0, "NETWORK_ERROR", "Network request failed — backend unreachable.", rid, true);
  } finally {
    watch.dispose();
    opts.signal?.removeEventListener("abort", onCallerAbort);
  }

  // Backend-correlation id precedence for THIS response: envelope body id is
  // applied inside normalizeErrorEnvelope; the header echoes the middleware
  // id (equals our `rid` because we sent it as X-Request-ID and the backend
  // reuses it — web/errors.py:47).
  const headerRid = res.headers.get("X-Request-ID")?.trim() || rid;

  const body = await parseBody(res);
  if (!res.ok) {
    throw normalizeErrorEnvelope(res.status, body, headerRid);
  }
  if (envelope === "v1") {
    // A 200 without {data,...} is a contract violation, not a value: surface
    // it as a typed error instead of returning `undefined` into the pages.
    if (body === null || typeof body !== "object" || Array.isArray(body) || !("data" in body)) {
      throw new ApiError(
        res.status,
        "INVALID_RESPONSE",
        `Backend success response is not a v1 {data, meta} envelope (HTTP ${res.status}).`,
        headerRid,
        false,
      );
    }
    return (body as V1Envelope<T>).data;
  }
  return body as T;
}

/**
 * Low-level escape hatch (transport tests + future callers needing explicit
 * method/timeout control). Pages must keep using the api modules, which own
 * endpoint strings and query construction.
 */
export async function request<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  return rawRequest<T>(path, opts, "raw");
}

/** GET a v1 envelope and return the unwrapped payload (meta dropped). */
export async function getV1<T>(path: string, signal?: AbortSignal): Promise<T> {
  return rawRequest<T>(path, { signal }, "v1");
}

/** GET a legacy (raw JSON) endpoint. */
export async function getLegacy<T>(path: string, signal?: AbortSignal): Promise<T> {
  return rawRequest<T>(path, { signal }, "raw");
}

/** POST/PUT helper returning the raw response body. NEVER auto-retried. */
export async function send<T>(path: string, body?: unknown, method: "POST" | "PUT" = "POST"): Promise<T> {
  return rawRequest<T>(path, { method, body: body ?? {} }, "raw");
}
