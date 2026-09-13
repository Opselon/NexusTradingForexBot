/**
 * core/auth — first-class authentication layer for the platform backbone.
 *
 * Responsibilities (extracted from the old ad-hoc token logic in
 * src/api/client.ts so UI, transport and realtime share ONE source of truth):
 *  - token resolution with a fixed precedence:
 *      1. window-injected (__NSE_WEB_TOKEN__ — SPA host boot handoff)
 *      2. ?token= query param on first load (consumed + URL scrubbed once)
 *      3. sessionStorage (per-tab, written by the ?token= path or login())
 *      4. cookie-only mode -> null (HttpOnly bootstrap cookie rides on
 *         `credentials:"same-origin"`; no token exists client-side)
 *  - explicit login()/logout() with an observable auth state
 *  - publishing auth events on the core event bus so any layer (banners,
 *    realtime, query cache invalidation) can react without import cycles.
 *
 * BUG-267 rule: cookie is the zero-config path; an explicit token always
 * wins (header auth) because operator-launched scripts predate the cookie.
 */

import { runtimeConfig, STORAGE_KEYS, ENDPOINTS } from "./config";
import { coreEvents } from "./events";

/** What the auth layer reports; UI renders state transitions from this. */
export interface AuthState {
  /** Resolved bearer token, or null in cookie-only mode. */
  token: string | null;
  /** True when a token is present (header auth path). */
  hasToken: boolean;
  /** True when the cookie bootstrap asset was reachable at least once. */
  cookieBootstrapped: boolean;
  /** Last 401 seen (post-heal) — drives the auth banner in AppShell. */
  lastUnauthorizedAt: number | null;
}

let cookieBootstrapped = false;
let lastUnauthorizedAt: number | null = null;
let queryParamConsumed = false;

function readSessionToken(): string | null {
  try {
    return sessionStorage.getItem(STORAGE_KEYS.token);
  } catch {
    return null;
  }
}

function writeSessionToken(token: string): void {
  try {
    sessionStorage.setItem(STORAGE_KEYS.token, token);
  } catch {
    /* private mode / disabled storage — token stays in-memory for this boot */
    memoryToken = token;
  }
}

/** Fallback when sessionStorage is unavailable (never persisted). */
let memoryToken: string | null = null;

/**
 * Resolve the active token, applying the documented precedence. Idempotent:
 * the ?token= consumption (persist + URL scrub) happens exactly once per load.
 */
export function resolveToken(): string | null {
  // 1. Explicitly injected at boot by the SPA host.
  const w = (globalThis as unknown as { window?: { __NSE_WEB_TOKEN__?: string } }).window;
  if (w?.__NSE_WEB_TOKEN__) return w.__NSE_WEB_TOKEN__;

  // 2. Query param on first load (?token= — the SSE/operator-friendly path).
  if (!queryParamConsumed) {
    queryParamConsumed = true;
    try {
      const loc = (globalThis as unknown as { window?: Window }).window?.location;
      if (loc) {
        const qp = new URLSearchParams(loc.search).get(runtimeConfig.tokenQueryParam);
        if (qp) {
          writeSessionToken(qp);
          const url = new URL(loc.href);
          url.searchParams.delete(runtimeConfig.tokenQueryParam);
          window.history.replaceState(null, "", url.toString());
          return qp;
        }
      }
    } catch {
      /* non-browser host (tests) — fall through */
    }
  }

  // 3. Persisted for this tab session only.
  return readSessionToken() ?? memoryToken;
}

/** Current full auth state (cheap; safe to call during render). */
export function getAuthState(): AuthState {
  const token = resolveToken();
  return { token, hasToken: token !== null, cookieBootstrapped, lastUnauthorizedAt };
}

/** Convenience for banners/indicators — true when header auth is active. */
export function hasAccessToken(): boolean {
  return resolveToken() !== null;
}

/** Header bag for the auth transports the backend accepts (BUG-267). */
export function authHeaders(): Record<string, string> {
  const token = resolveToken();
  if (!token) return {};
  return { Authorization: `Bearer ${token}`, "X-NSE-Token": token };
}

/** Token as a query param (SSE / EventSource cannot send headers). */
export function authTokenQuery(): string {
  const token = resolveToken();
  return token ? `${runtimeConfig.tokenQueryParam}=${encodeURIComponent(token)}` : "";
}

/** Explicit operator login (command palette / settings page path). */
export function login(token: string): void {
  const trimmed = token.trim();
  if (!trimmed) return;
  writeSessionToken(trimmed);
  lastUnauthorizedAt = null;
  coreEvents.publish("auth:changed", { hasToken: true, source: "login" });
}

/** Explicit logout: drops the tab token; the HttpOnly cookie needs server-side clear. */
export function logout(): void {
  try {
    sessionStorage.removeItem(STORAGE_KEYS.token);
  } catch {
    /* ignore */
  }
  memoryToken = null;
  coreEvents.publish("auth:changed", { hasToken: false, source: "logout" });
}

/** Back-compat name kept for existing callers (api/client re-export). */
export function clearAuthToken(): void {
  logout();
}

/** Transport hooks — called by core/middleware, not by feature code. */
export function noteCookieBootstrap(ok: boolean): void {
  if (ok && !cookieBootstrapped) {
    cookieBootstrapped = true;
    coreEvents.publish("auth:changed", { hasToken: hasAccessToken(), source: "cookie-bootstrap" });
  }
}

export function noteUnauthorized(): void {
  lastUnauthorizedAt = Date.now();
  coreEvents.publish("auth:expired", { at: lastUnauthorizedAt });
}

/** Re-derive the cookie by fetching a public cookie-issuing asset (BUG-267). */
export async function refreshBootstrapCookie(): Promise<boolean> {
  try {
    const r = await fetch(ENDPOINTS.cookieBootstrapAsset, {
      method: "GET",
      credentials: "same-origin",
      cache: "no-store",
    });
    noteCookieBootstrap(r.ok);
    return r.ok;
  } catch {
    return false;
  }
}
