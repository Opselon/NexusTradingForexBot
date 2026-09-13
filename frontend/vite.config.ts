import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";

// NSE Alternative UI — Vite configuration.
//
// Architecture contract (DEC-0002 lineage): Node/Vite is a BUILD/DEV tool only.
// The production artifact is a fully static `dist/` bundle served by the
// existing FastAPI process (no Node HTTP server, no extra ports in production).
//
// Development: `npm run dev` serves the UI on :5173 and proxies /api, /health,
// /ws and /web to the authoritative NSE backend (default 127.0.0.1:8080,
// override with NSE_API_ORIGIN) so cookies/tokens and SSE/WebSockets work
// without CORS games. The backend stays the single source of truth.
//
// BUG-266: when the engine auto-incremented past an occupied port (8080 ->
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
  base: "/alt/",
  server: {
    port: 5173,
    strictPort: false,
    proxy: {
      "/api": { target: backendOrigin, changeOrigin: true },
      "/health": { target: backendOrigin, changeOrigin: true },
      "/healthz": { target: backendOrigin, changeOrigin: true },
      // BUG-266: cookie bootstrap in dev — the client's one-shot 401 heal
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
  },
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "src"),
    },
  },
});
