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

// Router base: the production bundle is mounted under /alt (web/server.py
// StaticFiles), while `vite dev` serves at "/". Vite rewrites import.meta.env
// .BASE_URL at build time from vite.config `base`, so this stays correct in
// both habitats without a second config source.
const ROUTER_BASENAME = import.meta.env.BASE_URL.replace(/\/+$/, "") || "/";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 5_000,
      refetchOnWindowFocus: false,
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
