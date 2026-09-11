/**
 * App shell: routing, providers, top status bar, banners.
 *
 * Server state baseline comes from TanStack Query (`/api/status` snapshot);
 * the WebSocket layer merges live updates on top. LIVE/PAPER and every
 * authoritative value render from backend data only.
 */

import { useEffect, useState } from "react";
import { NavLink, Route, Routes } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { engineApi } from "@/api/engineApi";
import { useRealtimeSnapshot } from "@/hooks/useRealtimeSnapshot";
import { isFeedStale } from "@/components/ConnectionIndicator";
import { ModeIndicator } from "@/components/ModeIndicator";
import { ConnectionIndicator } from "@/components/ConnectionIndicator";
import { useUiStore } from "@/stores/uiStore";
import { getAuthToken } from "@/api/client";
import { ApiError } from "@/types/api";
import { ErrorState, LoadingState } from "@/components/primitives";
import DashboardPage from "@/pages/Dashboard/DashboardPage";
import TradingPage from "@/pages/Trading/TradingPage";
import PositionsPage from "@/pages/Positions/PositionsPage";
import RiskPage from "@/pages/Risk/RiskPage";
import MLPage from "@/pages/ML/MLPage";
import IntelligencePage from "@/pages/Intelligence/IntelligencePage";
import AuditPage from "@/pages/Audit/AuditPage";

const NAV_SECTIONS: Array<{ section: string; items: Array<{ to: string; icon: string; label: string }> }> = [
  {
    section: "Operations",
    items: [
      { to: "/", icon: "◈", label: "Dashboard" },
      { to: "/trading", icon: "⇅", label: "Trading" },
      { to: "/positions", icon: "▤", label: "Positions" },
    ],
  },
  {
    section: "Safety & Intelligence",
    items: [
      { to: "/risk", icon: "⛨", label: "Risk" },
      { to: "/ml", icon: "Σ", label: "ML / 70D" },
      { to: "/intelligence", icon: "≈", label: "Intelligence" },
      { to: "/audit", icon: "☰", label: "Audit" },
    ],
  },
];

export function AppShell() {
  const collapsed = useUiStore((s) => s.sidebarCollapsed);
  const toggleSidebar = useUiStore((s) => s.toggleSidebar);
  const queryClient = useQueryClient();
  const [nowMs, setNowMs] = useState(() => Date.now());

  // 1s ticker for data-age display (visual only).
  useEffect(() => {
    const t = window.setInterval(() => setNowMs(Date.now()), 1000);
    return () => window.clearInterval(t);
  }, []);

  const snapshotQuery = useQuery({
    queryKey: ["engine-snapshot"],
    queryFn: ({ signal }) => engineApi.snapshot(signal),
    refetchInterval: (query) => (query.state.data ? false : 5000),
    refetchOnWindowFocus: true,
    retry: 1,
  });

  const { snapshot, connectionState, realtimeStatus } = useRealtimeSnapshot(snapshotQuery.data);

  // Track feed state to harden rendering decisions.
  useEffect(() => {
    // noop — keeps hook shape stable for future re-subscription semantics
  }, [connectionState]);

  const authError =
    snapshotQuery.error instanceof ApiError && snapshotQuery.error.isAuthError
      ? (snapshotQuery.error as ApiError)
      : null;
  const feedStale = isFeedStale(realtimeStatus, nowMs);
  const engineStale = snapshot?.is_stale === true;

  return (
    <div className="app-shell">
      <aside className={`sidebar ${collapsed ? "collapsed" : ""}`}>
        <div className="brand">
          <div className="brand-logo">NSE</div>
          <div className="brand-text">
            NEXUS SCALP ENGINE
            <span className="sub">ALTERNATIVE CONSOLE</span>
          </div>
        </div>
        <nav className="nav">
          {NAV_SECTIONS.map((sec) => (
            <div key={sec.section}>
              <div className="nav-section">{sec.section}</div>
              {sec.items.map((item) => (
                <NavLink key={item.to} to={item.to} end={item.to === "/"} className={({ isActive }) => `nav-item ${isActive ? "active" : ""}`}>
                  <span className="icon">{item.icon}</span>
                  <span className="nav-label">{item.label}</span>
                </NavLink>
              ))}
            </div>
          ))}
        </nav>
        <button className="sidebar-toggle" onClick={toggleSidebar} title="Toggle sidebar">
          {collapsed ? "»" : "«"}
        </button>
      </aside>

      <div className="main-col">
        <header className="topbar">
          <ModeIndicator snapshot={snapshot} />
          <span className="conn-chip" title="Engine loop state (backend-authoritative)">
            <span className={`conn-dot ${snapshot?.engine_running ? "connected" : snapshot ? "disconnected" : "reconnecting"}`} />
            <span>ENGINE {snapshot ? (snapshot.engine_running ? "RUNNING" : "STOPPED") : "—"}</span>
          </span>
          <span className="conn-chip" title="Backend health.overall from /api/status">
            {snapshot ? <span className={`badge ${snapshot.health.overall === "READY" ? "good" : snapshot.health.overall === "STALE" || snapshot.health.overall === "WARMING_UP" ? "warn" : "bad"}`}>{snapshot.health.overall}</span> : <span className="badge unknown">HEALTH —</span>}
          </span>
          {snapshot?.symbol && <span className="inline-mono small muted">{snapshot.symbol} M1</span>}
          <span className="spacer" />
          <ConnectionIndicator
            status={realtimeStatus}
            nowMs={nowMs}
            onRetry={() => {
              queryClient.invalidateQueries({ queryKey: ["engine-snapshot"] });
            }}
          />
        </header>

        {authError && (
          <div className="banner auth">
            <span>⛔ Backend requires a web-auth token (WEB-AUTH-P0). Open this console as <span className="inline-mono">…/?token=&lt;NSE_WEB_AUTH_TOKEN&gt;</span> — the token is kept in sessionStorage only.</span>
          </div>
        )}
        {!authError && feedStale && !snapshotQuery.isPending && (
          <div className="banner stale">
            <span>
              ⏸ Live feed {realtimeStatus.state.toUpperCase()} — values on screen are from {realtimeStatus.lastMessageAt ? new Date(realtimeStatus.lastMessageAt).toLocaleTimeString("en-GB", { hour12: false }) : "—"} and may be outdated.
            </span>
          </div>
        )}
        {!authError && !feedStale && engineStale && (
          <div className="banner stale">
            <span>⚠ Backend reports <span className="inline-mono">live_freshness=STALE</span> — the engine process is up but its intelligence pipeline is not fresh (frozen tick/feature/inference chain).</span>
            <button className="dismiss-btn" title="Dismiss until state changes">✕</button>
          </div>
        )}

        <main className="page">
          {snapshotQuery.isPending ? (
            <LoadingState label="Connecting to NSE backend…" />
          ) : snapshotQuery.isError && !snapshot ? (
            <ErrorState
              message={snapshotQuery.error instanceof Error ? snapshotQuery.error.message : "Backend unreachable"}
              requestId={snapshotQuery.error instanceof ApiError ? snapshotQuery.error.requestId : null}
              onRetry={() => snapshotQuery.refetch()}
            />
          ) : (
            <Routes>
              <Route path="/" element={<DashboardPage snapshot={snapshot} nowMs={nowMs} />} />
              <Route path="/trading" element={<TradingPage snapshot={snapshot} nowMs={nowMs} />} />
              <Route path="/positions" element={<PositionsPage snapshot={snapshot} />} />
              <Route path="/risk" element={<RiskPage snapshot={snapshot} />} />
              <Route path="/ml" element={<MLPage snapshot={snapshot} />} />
              <Route path="/intelligence" element={<IntelligencePage snapshot={snapshot} />} />
              <Route path="/audit" element={<AuditPage />} />
              <Route path="*" element={<ErrorState message="Unknown route" />} />
            </Routes>
          )}
        </main>
      </div>
    </div>
  );
}

export function authConfigured(): boolean {
  return getAuthToken() !== null;
}
