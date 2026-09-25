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
//   1. NSE_API_ORIGIN — explicit override, always wins (any origin).
//   2. NSE_GO_ADDR — where Go actually bound (the launcher honours the same
//      env, e.g. when its free-port search moved off the preferred port).
//   3. The Go convention — one port above the recorded Python web port
//      (engine_boot.py), resolved from the BUG-267 .env record
//      NSE_WEB_ACTUAL_PORT (gitignored) when Python auto-incremented past an
//      occupied port; else the defaults 8080 -> 8087.
//
// recordedBackendPort is thus an input to the Go port convention, not a
// competing origin: with Go up the frontend talks only to Go, and Go forwards
// to Python itself so the dev proxy needs to know only one origin.
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

// The Go API server is the API entrypoint: it reverse-proxies every operation
// to the Python FastAPI process, so the frontend only ever talks to Go.
// Port resolution mirrors the launcher (src/nexus_scalp/web/go_api_bootstrap.py
// resolve_api_addr + src/nexus_scalp/cli/engine_boot.py _boot_go_api_plane):
//   - NSE_GO_ADDR wins when set (host:port or :port);
//   - else Go sits ONE PORT ABOVE the Python web port (default 8080 -> 8087
//     only when Python stayed on its default; the recorded actual port is
//     used when Python auto-incremented, BUG-267);
//   - with a free-port search above that if the port is busy.
const GO_API_DEFAULT_PORT = 8087;

// Dev backend resolution, in priority order:
//   1. NSE_API_ORIGIN — explicit override, always wins (any origin).
//   2. NSE_GO_ADDR — where the Go API server actually bound (host:port). The
//      launcher honours this same env, so a non-default bind (busy port etc.)
//      is exactly where Go is listening.
//   3. The Go convention — one port ABOVE the recorded Python web port
//      (engine_boot.py: "the API plane sits one port above the Python web
//      port"), which is where Go binds when NSE_GO_ADDR is unset. Uses
//      recordedBackendPort() (BUG-267 .env NSE_WEB_ACTUAL_PORT) so a
//      Python port that auto-incremented past an occupied port still maps
//      to the right Go port, falling back to 8087 + 8080.
//   4. http://127.0.0.1:8087 — the Go default -addr (go-api/cmd/nexus-api).
//
// recordedBackendPort is therefore a *input to* the Go convention, not a
// competing origin: with Go up, the frontend talks only to Go.
function resolveBackendOrigin(): string {
  if (process.env.NSE_API_ORIGIN) return process.env.NSE_API_ORIGIN;

  // (2) explicit Go bind override.
  const goAddr = process.env.NSE_GO_ADDR?.trim();
  if (goAddr) return `http://${goAddr.replace(/^[^0-9a-z]+/i, "").replace(/^:\/\//, "")}`;

  // (3) Engine convention: a recorded Python web port means the launcher ran
  //     and Go sits one port ABOVE it. Without a record the engine never ran,
  //     so Go was started standalone and listens on its own default (:8087).
  const recorded = recordedBackendPort();
  return recorded ? `http://127.0.0.1:${recorded + 1}` : `http://127.0.0.1:${GO_API_DEFAULT_PORT}`;
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
