// Register every scope's i18n messages before any component renders
// (module side effects run in import order — this must stay first).
import "@/lib/i18nMessages";
import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AppShell } from "@/app/AppShell";
import "@/styles/theme.css";

// Global runtime configuration for the API layer.
// The backend origin is same-origin in production (static bundle served by the
// FastAPI process) and proxied same-origin in dev via the Vite proxy — so no
// absolute URL is baked into the bundle.

// Router base (CONTRACT #2 — RUNTIME basename, not build-time):
// the SAME dist is dual-served — the canonical `/` entrypoint and the legacy
// `/alt` StaticFiles mount (web/server.py) both serve these byte-identical
// files, so the prefix cannot be known at build time. Derive it from the
// runtime location: `/alt` exactly or `/alt/...` -> basename "/alt",
// anything else -> "/". import.meta.env.BASE_URL is "/" for this build and
// must NOT drive this decision (it is only a dev diagnostics value now).
function runtimeBasename(): string {
  const p = window.location.pathname;
  return p === "/alt" || p.startsWith("/alt/") ? "/alt" : "/";
}
const ROUTER_BASENAME = runtimeBasename();

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 5_000,
      // Wave 6 (perf): returning to the tab now revalidates ACTIVE queries
      // immediately instead of waiting up to the next refetchInterval tick —
      // strictly fresher on return, unchanged everywhere else (background tabs
      // still never auto-refetch: refetchIntervalInBackground stays false).
      refetchOnWindowFocus: true,
      retry: 1,
    },
  },
});

ReactDOM.createRoot(document.getElementById("root") as HTMLElement).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter basename={ROUTER_BASENAME}>
        <AppShell />
      </BrowserRouter>
    </QueryClientProvider>
  </React.StrictMode>,
);
