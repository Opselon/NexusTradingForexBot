/**
 * core/config — the single source of runtime configuration for the platform
 * backbone. No other layer may hardcode a URL, storage key, timeout or flag
 * name (Open/Closed: add a key here, never a string literal at a call site).
 *
 * Habitat rules:
 *  - Production: the static bundle is served BY the engine under `/alt`
 *    (web/server.py StaticFiles) -> API base is same-origin ("" ).
 *  - Development: `vite dev` proxies /api, /health, /app.js and the SSE route
 *    to the engine (vite.config.ts `backendOrigin`) -> still same-origin from
 *    the browser's point of view, so nothing is baked into the bundle.
 *  - Remote/standalone hosting (scripts/serve_alt_ui.py, CI previews) may
 *    inject `window.__NSE_API_BASE__` before the bundle loads; an absolute
 *    base then wins over same-origin.
 */

/** Window-injected overrides the backend/dev harness may set before boot. */
interface InjectedRuntime {
  __NSE_API_BASE__?: string;
  __NSE_UI_FLAGS__?: Record<string, boolean>;
  __NSE_WEB_TOKEN__?: string;
}

function injected(): InjectedRuntime {
  return (globalThis as unknown as { window?: InjectedRuntime }).window ?? {};
}

/** Storage keys — centralized so a rename is one line and never a typo. */
export const STORAGE_KEYS = {
  /** sessionStorage: operator token for this tab only (never localStorage). */
  token: "nse.altui.token",
  /** localStorage: UI preference only (sidebar/density/lang live in stores). */
  lang: "nexus.ui.lang",
  sidebar: "nse.altui.sidebar",
  density: "nse.altui.dense",
} as const;

/** Backend routes the backbone itself depends on (one place, grep-able). */
export const ENDPOINTS = {
  /** Canonical snapshot bootstrap (legacy raw JSON). */
  status: "/api/status",
  /** Realtime SSE stream — NOT a WebSocket (uvicorn boots ws="none"). */
  ticksStream: "/api/ticks/stream",
  /** BUG-267: public cookie-issuing asset used by the one-shot 401 heal. */
  cookieBootstrapAsset: "/app.js",
} as const;

export interface TimeoutBudget {
  /** Ordinary GET/POST through the transport. */
  requestMs: number;
  /** Mutations get more room (backend writes an audit row + acks). */
  mutationMs: number;
  /** Throttle window for the cookie heal (many 401s land at once). */
  cookieHealMs: number;
  /** SSE watchdog: no frame for this long -> force a reconnect. */
  realtimeStaleMs: number;
  /** Grace added on top of the stale window before the watchdog fires. */
  realtimeHeartbeatGraceMs: number;
}

export interface RuntimeConfig {
  /** "" means same-origin. Absolute URL only for remote-injected hosts. */
  apiBase: string;
  /** Query param the backend accepts a token in (SSE cannot send headers). */
  tokenQueryParam: string;
  endpoints: typeof ENDPOINTS;
  storageKeys: typeof STORAGE_KEYS;
  timeouts: TimeoutBudget;
  /** Feature flags (UI-gated build-outs). Read only through `featureFlag()`. */
  flags: Record<string, boolean>;
}

const DEFAULT_FLAGS: Record<string, boolean> = {
  /** Render the registry-driven feature sections in the sidebar. */
  navRegistry: true,
  /** Command palette lists every registered route. */
  paletteAllRoutes: true,
  /** Show the tick-section debug row in the topbar (lane 4 turns it on). */
  realtimeDiagnostics: false,
};

function envFlags(): Record<string, boolean> {
  // Vite exposes VITE_FEATURE_<NAME>=1 at build time; the runtime injection
  // (window.__NSE_UI_FLAGS__) lets the backend flip a flag without a rebuild.
  const meta = (import.meta as unknown as { env?: Record<string, string | undefined> }).env ?? {};
  const out: Record<string, boolean> = {};
  for (const [k, v] of Object.entries(meta)) {
    if (k.startsWith("VITE_FEATURE_") && v !== undefined) {
      out[k.slice("VITE_FEATURE_".length).toLowerCase()] = v === "1" || v === "true";
    }
  }
  return { ...out, ...(injected().__NSE_UI_FLAGS__ ?? {}) };
}

export const runtimeConfig: RuntimeConfig = {
  apiBase: (injected().__NSE_API_BASE__ ?? "").replace(/\/+$/, ""),
  tokenQueryParam: "token",
  endpoints: ENDPOINTS,
  storageKeys: STORAGE_KEYS,
  timeouts: {
    requestMs: 15_000,
    mutationMs: 30_000,
    cookieHealMs: 1_000,
    realtimeStaleMs: 10_000,
    realtimeHeartbeatGraceMs: 15_000,
  },
  flags: { ...DEFAULT_FLAGS, ...envFlags() },
};

/** Join the configured API base with a backend path (absolute URLs pass through). */
export function apiUrl(path: string): string {
  if (/^https?:\/\//i.test(path) || !runtimeConfig.apiBase) return path;
  return `${runtimeConfig.apiBase}${path.startsWith("/") ? path : `/${path}`}`;
}

/** Flag accessor — callers never touch `runtimeConfig.flags` directly. */
export function featureFlag(name: keyof typeof DEFAULT_FLAGS | string): boolean {
  return runtimeConfig.flags[name] === true;
}

/** Build a query string from a sparse param bag (undefined/null dropped). */
export function toQuery(params: Record<string, string | number | boolean | undefined | null>): string {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null || v === "") continue;
    q.set(k, String(v));
  }
  const s = q.toString();
  return s ? `?${s}` : "";
}

/** Dev-only helper: which origin is the proxy pointed at (diagnostics UI). */
export function devProbe(): { apiBase: string; base: string; flags: Record<string, boolean> } {
  const base = (import.meta as unknown as { env?: { BASE_URL?: string } }).env?.BASE_URL ?? "/";
  return { apiBase: runtimeConfig.apiBase || "(same-origin)", base, flags: runtimeConfig.flags };
}
