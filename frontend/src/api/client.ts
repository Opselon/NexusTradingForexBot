/**
 * Central HTTP transport for the NSE Alternative UI.
 *
 * Contract:
 *  - ALL backend communication goes through here (no scattered fetch()).
 *  - Auth: the backend enforces WEB-AUTH-P0 token auth (Bearer / X-NSE-Token /
 *    ?token=). The token is supplied by the operator at runtime (query param
 *    `?token=` on first load is kept ONLY in sessionStorage so a page refresh
 *    keeps working; it never lands in localStorage or the repo).
 *  - Errors normalize to ApiError (both v1 `{error:{...}}` and legacy
 *    `{error:{code,message,request_id}}` envelopes).
 *  - No retries on mutations; GET retries are handled by TanStack Query.
 */

import { ApiError, type V1Envelope } from "@/types/api";

const TOKEN_STORAGE_KEY = "nse.altui.token";

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
}

async function parseBody(res: Response): Promise<unknown> {
  try {
    return await res.json();
  } catch {
    return null;
  }
}

function extractError(status: number, body: unknown): ApiError {
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
    typeof errObj.request_id === "string" ? errObj.request_id : null,
    status === 503 || status === 504,
  );
}

async function rawRequest<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  requestSeq += 1;
  const rid = `altui_${Date.now().toString(36)}_${requestSeq.toString(36)}`;
  const method = opts.method ?? "GET";

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
      signal: opts.signal,
    });
  } catch (e) {
    if (e instanceof DOMException && e.name === "AbortError") throw e;
    throw new ApiError(0, "NETWORK_ERROR", "Network request failed — backend unreachable.", rid, true);
  }

  const body = await parseBody(res);
  if (!res.ok) {
    throw extractError(res.status, body);
  }
  return body as T;
}

/** GET a v1 envelope and return the unwrapped payload (meta dropped). */
export async function getV1<T>(path: string, signal?: AbortSignal): Promise<T> {
  const env = await rawRequest<V1Envelope<T>>(path, { signal });
  return env.data;
}

/** GET a legacy (raw JSON) endpoint. */
export async function getLegacy<T>(path: string, signal?: AbortSignal): Promise<T> {
  return rawRequest<T>(path, { signal });
}

/** POST/PUT helper returning the raw response body. */
export async function send<T>(path: string, body?: unknown, method: "POST" | "PUT" = "POST"): Promise<T> {
  return rawRequest<T>(path, { method, body: body ?? {} });
}
