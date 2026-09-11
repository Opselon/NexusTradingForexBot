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

const backendOrigin = process.env.NSE_API_ORIGIN || "http://127.0.0.1:8080";

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
