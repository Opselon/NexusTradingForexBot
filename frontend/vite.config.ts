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
// /health, /ws and /web to the authoritative NSE backend (default 127.0.0.1:8080,
// override with NSE_API_ORIGIN) so cookies/tokens and SSE/WebSockets work
// without CORS games. The backend stays the single source of truth.
//
// BUG-267: when the engine auto-incremented past an occupied port (8080 ->
// 8081), the launcher records the ACTUAL bind in the repo-root .env
// (NSE_WEB_ACTUAL_PORT, gitignored). Read it so `npm run dev` proxies to the
// engine that is actually running instead of a dead :8080 (the "React
// console shows UNAUTHORIZED / nothing loads" dev trap). Explicit
// NSE_API_ORIGIN still wins.
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

const backendOrigin =
  process.env.NSE_API_ORIGIN || `http://127.0.0.1:${recordedBackendPort() ?? 8080}`;

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
