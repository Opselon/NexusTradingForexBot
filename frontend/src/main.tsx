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
      <BrowserRouter>
        <AppShell />
      </BrowserRouter>
    </QueryClientProvider>
  </React.StrictMode>,
);
