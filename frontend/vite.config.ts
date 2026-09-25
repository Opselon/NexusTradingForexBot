import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";

// Nexus Scalp Engine — Control Center UI — Vite configuration.
//
// Architecture contract (DEC-0002 lineage): Node/Vite is a BUILD/DEV tool only.
// The production artifact is a fully static `dist/` bundle served by the
// existing FastAPI process (no Node HTTP server, no extra ports in production).
//
// Production mount: the SAME dist/ is served at `/` (canonical entrypoint,
// CONTRACT #4) and at `/alt` (dual-serve compatibility mount). Both serve
// byte-identical files, and the router basename is resolved at RUNTIME from
// window.location.pathname (src/main.tsx, CONTRACT #2) — so the build emits one
// root-absolute bundle that works under both prefixes without a second
// config source.
//
// Development: `npm run dev` serves the UI at `/` on :5173 and proxies /api,
// /health, /ws and /web to the authoritative NSE backend so cookies/tokens
// and SSE/WebSockets work without CORS games.
//
// The Go API server is the single API entrypoint in front of the Python
// FastAPI app (it reverse-proxies every operation to Python, byte-compatible,
// so /api/v1/* still answers {data,meta} and legacy /api/* still answers raw
// JSON — see core/transport.ts). Dev traffic therefore targets Go by default:
//
//   1. NSE_API_ORIGIN  — explicit override, always wins (any origin).
//   2. Go API origin   — http://127.0.0.1:8087 (Go's default -addr /
//      NSE_GO_ADDR). Go then forwards to Python itself, so the dev proxy only
//      ever needs to know one origin.
//   3. recordedBackendPort — BUG-267 fallback: when the engine auto-incremented
//      past an occupied port (8080 -> 8081) the launcher records the ACTUAL
//      bind in the repo-root .env (NSE_WEB_ACTUAL_PORT, gitignored). Kept for
//      the case where a Python-only engine is running with no Go layer in
//      front of it.
//
// NOTE: the cookie-issuing asset /app.js is still served (Go proxies Python's
// static mounts), so the BUG-267 one-shot 401 heal in core/middleware.ts keeps
// working unchanged.
import { existsSync, readFileSync } from "node:fs";

function recordedBackendPort(): number | null {
  const envPath = path.resolve(__dirname, "..", ".env");
  if (!existsSync(envPath)) return null;
  try {
    const m = readFileSync(envPath, "utf8").match(/^NSE_WEB_ACTUAL_PORT=(\d{1,5})$/m);
    if (!m) return null;
    const p = Number(m[1]);
    return p >= 1 && p <= 65535 ? p : null;
  } catch {
    return null;
  }
}

// The Go API server is the API entrypoint: it listens on :8087 by default
// (flag -addr / env NSE_GO_ADDR) and reverse-proxies every operation to the
// Python FastAPI process, so the frontend only ever talks to Go. The previous
// default (8080) addressed the Python process directly; Go now owns that role.
const GO_API_DEFAULT_PORT = 8087;

// Dev backend resolution, in priority order:
//   1. NSE_API_ORIGIN — explicit override, always wins (any origin).
//   2. The Go API server on :8087 — the DEFAULT backend. Go is the API
//      entrypoint and forwards to Python itself, so a normal `npm run dev`
//      (no .env, no override) hits Go.
//   3. recordedBackendPort — BUG-267 fallback, used only when the Go server is
//      NOT reachable on :8087: when a Python-only engine auto-incremented past
//      an occupied port (8080 -> 8081) the launcher records the ACTUAL bind in
//      the repo-root .env (NSE_WEB_ACTUAL_PORT, gitignored). Fall back to it so
//      dev still points at a live Python backend instead of a dead :8087 (the
//      "React console shows UNAUTHORIZED / nothing loads" dev trap).
function resolveBackendOrigin(): string {
  if (process.env.NSE_API_ORIGIN) return process.env.NSE_API_ORIGIN;
  const goOrigin = `http://127.0.0.1:${GO_API_DEFAULT_PORT}`;
  return isPortOpen(goOrigin) ? goOrigin : `http://127.0.0.1:${recordedBackendPort() ?? GO_API_DEFAULT_PORT}`;
}

const backendOrigin = resolveBackendOrigin();

// WebSocket proxy needs raw TCP (http://); everything else keeps http(s).
const wsTarget = backendOrigin.replace(/^https/, "http");

export default defineConfig({
  plugins: [react()],
  // Root-absolute build (CONTRACT #1): assets are emitted as /assets/... and
  // index.html references them as such. The `/` entrypoint and the `/alt`
  // mount both resolve them; the router prefix itself is resolved at runtime
  // (src/main.tsx, CONTRACT #2). A base of "/alt/" is what made `/alt/alt`
  // style URLs possible.
  base: "/",
  server: {
    port: 5173,
    strictPort: false,
    proxy: {
      "/api": { target: backendOrigin, changeOrigin: true },
      "/health": { target: backendOrigin, changeOrigin: true },
      "/healthz": { target: backendOrigin, changeOrigin: true },
      // BUG-267: cookie bootstrap in dev — the client's one-shot 401 heal
      // fetches /app.js (the engine's Set-Cookie carrier). Without proxying
      // it, vite answers from its own SPA and no cookie ever lands.
      "/app.js": { target: backendOrigin, changeOrigin: true },
      "/api_client.js": { target: backendOrigin, changeOrigin: true },
      "/ws": {
        target: wsTarget,
        changeOrigin: true,
        ws: true,
      },
      "/web": {
        target: wsTarget,
        changeOrigin: true,
        ws: true,
      },
      // Primary realtime transport: the backend's SSE stream (production
      // uvicorn runs with ws="none"; see src/websocket/realtimeSocket.ts).
      "/api/ticks/stream": {
        target: backendOrigin,
        changeOrigin: true,
        // SSE must not be buffered/compressed by the dev proxy.
        configure: (proxy) => {
          proxy.on("proxyRes", (proxyRes) => {
            proxyRes.headers["cache-control"] = "no-cache";
            proxyRes.headers["x-accel-buffering"] = "no";
          });
        },
      },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: false,
    chunkSizeWarningLimit: 1200,
    // Wave 6 (perf): stable vendor chunk — React/Router/Query change far less
    // often than app code, so app deploys re-fetch only the small app entry
    // while the vendor bundle stays cached; it also parallelizes first-load
    // fetches. Same bytes total, fewer re-downloads, no behavior change.
    rollupOptions: {
      output: {
        // Function form: this repo builds on rolldown-vite, which rejects the
        // object shorthand ("Expected Function but received Object").
        manualChunks(id: string) {
          const norm = id.replace(/\\/g, "/");
          if (norm.includes("node_modules/@tanstack/")) return "query-vendor";
          for (const pkg of ["react/", "react-dom/", "scheduler/", "react-router/", "react-router-dom/", "@remix-run/"]) {
            if (norm.includes(`node_modules/${pkg}`)) return "react-vendor";
          }
          // Wave 3 (lane C): chart engine split — the canvas painter + its
          // chart/* helpers churn every wave while the rest of the Dashboard
          // page stays put, so a chart-only edit re-downloads this chunk alone
          // instead of the whole Dashboard bundle. Same bytes on first load,
          // far fewer re-fetched bytes across chart iterations.
          if (norm.includes("/pages/Dashboard/chart/")) return "chart-engine";
          if (norm.includes("/pages/Dashboard/chartPainter.ts")) return "chart-engine";
          // Wave 3 (lane C): every other third-party module lands in one
          // stable `vendor` chunk (checked last — the two named vendor splits
          // above still win), so app-code deploys never re-download vendor
          // bytes out of whichever page chunk imported them first.
          if (norm.includes("node_modules/")) return "vendor";
          return undefined;
        },
      },
    },
  },
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "src"),
    },
  },
});
