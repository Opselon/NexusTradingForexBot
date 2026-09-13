/**
 * App shell: routing, providers, top status bar, banners.
 *
 * Server state baseline comes from TanStack Query (`/api/status` snapshot);
 * the realtime layer merges live updates on top. LIVE/PAPER and every
 * authoritative value render from backend data only.
 *
 * Pro UX layer (presentation only): persistent sidebar/topbar, density mode,
 * freshness meters, keyboard navigation (Alt+1..7, Alt+B), dismissible stale
 * banners, and the command-result toast host. No state decisions live here.
 */

import { useEffect, useState } from "react";
import { NavLink, Route, Routes, useNavigate } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { engineApi } from "@/api/engineApi";
import { useRealtimeSnapshot } from "@/hooks/useRealtimeSnapshot";
import { ConnectionIndicator, FreshnessMeter } from "@/components/ConnectionIndicator";
import { ModeIndicator } from "@/components/ModeIndicator";
import { AttentionStrip } from "@/components/AttentionStrip";
import { CommandPalette } from "@/components/CommandPalette";
import { ConfirmModal, ToastHost } from "@/components/primitives";
import { useUiStore } from "@/stores/uiStore";
import { useI18n } from "@/stores/i18nStore";
import { LANGUAGES } from "@/lib/i18n";
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

const NAV_SECTIONS: Array<{ section: string; sectionKey: string; items: Array<{ to: string; icon: string; label: string }> }> = [
  {
    section: "Operations",
    sectionKey: "ux.sidebar.operate",
    items: [
      { to: "/", icon: "◈", label: "Dashboard" },
      { to: "/trading", icon: "⇅", label: "Trading" },
      { to: "/positions", icon: "▤", label: "Positions" },
    ],
  },
  {
    section: "Safety & Intelligence",
    sectionKey: "ux.sidebar.analyze",
    items: [
      { to: "/risk", icon: "⛨", label: "Risk" },
      { to: "/ml", icon: "Σ", label: "ML / 70D" },
      { to: "/intelligence", icon: "≈", label: "Intelligence" },
      { to: "/audit", icon: "☰", label: "Audit" },
    ],
  },
];

/** Flat route list for Alt+<n> keyboard navigation (visual shortcut only). */
const NAV_ROUTES = NAV_SECTIONS.flatMap((s) => s.items).map((i) => i.to);

/** Backend age (seconds) -> ms for the freshness meter; null stays null. */
function ageSecToMs(sec: number | null | undefined): number | null {
  return sec === null || sec === undefined || !Number.isFinite(sec) ? null : sec * 1000;
}

/** Language picker (sidebar foot) — UI preference shared with the legacy
 *  dashboard (localStorage['nexus.ui.lang']). Never a system setting. */
function LangRow() {
  const lang = useI18n((s) => s.lang);
  const setLang = useI18n((s) => s.setLang);
  const t = useI18n((s) => s.t);
  return (
    <div className="side-row">
      <span>{t("ux.lang.label", "LANGUAGE")}</span>
      <select
        className="select"
        style={{ padding: "2px 4px", fontSize: 11 }}
        value={lang}
        onChange={(e) => setLang(e.target.value as (typeof LANGUAGES)[number]["id"])}
        aria-label="Language"
      >
        {LANGUAGES.map((l) => (
          <option key={l.id} value={l.id}>
            {l.label}
          </option>
        ))}
      </select>
    </div>
  );
}

export function AppShell() {
  const collapsed = useUiStore((s) => s.sidebarCollapsed);
  const toggleSidebar = useUiStore((s) => s.toggleSidebar);
  const dense = useUiStore((s) => s.dense);
  const toggleDense = useUiStore((s) => s.toggleDense);
  const [helpOpen, setHelpOpen] = useState(false);
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [nowMs, setNowMs] = useState(() => Date.now());

  // 1s ticker for data-age display (visual only).
  useEffect(() => {
    const t = window.setInterval(() => setNowMs(Date.now()), 1000);
    return () => window.clearInterval(t);
  }, []);

  // Density class on <body> — CSS custom properties cascade from there.
  useEffect(() => {
    document.body.classList.toggle("dense", dense);
  }, [dense]);

  // Alt+1..7 route jump, Alt+B sidebar, R = refresh (skipped while typing).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null;
      if (target && (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.tagName === "SELECT" || target.isContentEditable)) return;
      if (e.ctrlKey || e.metaKey) return;
      if (!e.altKey) {
        if ((e.key === "r" || e.key === "R") && queryClient.getQueryData(["engine-snapshot"]) !== undefined) {
          e.preventDefault();
          void queryClient.invalidateQueries({ queryKey: ["engine-snapshot"] });
        }
        return;
      }
      const digit = Number(e.key);
      if (Number.isInteger(digit) && digit >= 1 && digit <= NAV_ROUTES.length) {
        e.preventDefault();
        const route = NAV_ROUTES[digit - 1];
        if (route) navigate(route);
      } else if (e.key.toLowerCase() === "b") {
        e.preventDefault();
        toggleSidebar();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [navigate, toggleSidebar]);

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
  const lf = snapshot?.live_freshness ?? null;

  return (
    <div className="app-shell">
      <aside className={`sidebar ${collapsed ? "collapsed" : ""}`}>
        <div className="brand">
          <div className="brand-logo">NSE</div>
          <div className="brand-text">
            NEXUS SCALP ENGINE
            <span className="sub">PRO CONSOLE</span>
          </div>
        </div>
        <nav className="nav">
          {NAV_SECTIONS.map((sec) => (
            <div key={sec.section}>
              <div className="nav-section">{sec.section}</div>
              {sec.items.map((item) => (
                <NavLink key={item.to} to={item.to} end={item.to === "/"} className={({ isActive }) => `nav-item ${isActive ? "active" : ""}`} title={item.label}>
                  <span className="icon">{item.icon}</span>
                  <span className="nav-label">{item.label}</span>
                </NavLink>
              ))}
            </div>
          ))}
        </nav>
        <div className="sidebar-foot">
          <div className="side-row">
            <span>DENSITY</span>
            <button
              className={`switch ${dense ? "on" : ""}`}
              role="switch"
              aria-checked={dense}
              aria-label="Toggle dense layout"
              title="Dense layout (visual only)"
              onClick={toggleDense}
            />
          </div>
          <LangRow />
          <div className="side-row" title="Keyboard shortcuts">
            <span><kbd>alt</kbd> 1–7 · <kbd>ctrl</kbd>K</span>
          </div>
          <button className="sidebar-toggle" onClick={toggleSidebar} title="Toggle sidebar (Alt+B)">
            {collapsed ? "»" : "«"}
          </button>
        </div>
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
          {snapshot && (
            <span className="conn-chip" style={{ gap: 10 }} title="Pipeline freshness stages (backend live_freshness)">
              <FreshnessMeter label="MKT" state={lf?.market?.state} ageMs={lf?.market?.age_ms ?? ageSecToMs(snapshot.diagnostics.tick_age_sec)} />
              <FreshnessMeter label="FEAT" state={lf?.features?.state} ageMs={lf?.features?.age_ms ?? ageSecToMs(snapshot.diagnostics.features_age_sec)} />
              <FreshnessMeter label="INFR" state={lf?.inference?.state} ageMs={lf?.inference?.age_ms ?? ageSecToMs(snapshot.diagnostics.inference_age_sec)} />
              <FreshnessMeter label="DEC" state={lf?.decision?.state} ageMs={lf?.decision?.age_ms ?? ageSecToMs(snapshot.diagnostics.proposal_age_sec)} />
            </span>
          )}
          {snapshot?.symbol && <span className="inline-mono small muted">{snapshot.symbol} M1</span>}
          <span className="spacer" />
          <span className="timestamp-note" title="Local wall clock (visual aid)">
            {new Date(nowMs).toLocaleTimeString("en-GB", { hour12: false })} · v{snapshot?.state_version ?? "—"}
          </span>
          <ConnectionIndicator
            status={realtimeStatus}
            nowMs={nowMs}
            onRetry={() => {
              queryClient.invalidateQueries({ queryKey: ["engine-snapshot"] });
            }}
          />
        </header>

        {!authError && <AttentionStrip snapshot={snapshot} feed={realtimeStatus} nowMs={nowMs} />}
        {authError && (
          <div className="banner auth">
            <span>⛔ Backend rejected the web-auth credential (WEB-AUTH-P0). The console self-bootstraps via the first-party cookie (BUG-266) — if this persists, reload once, or open as <span className="inline-mono">…/?token=&lt;NSE_WEB_AUTH_TOKEN&gt;</span> (token kept in sessionStorage only).</span>
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
      <ToastHost />
      <CommandPalette onOpenHelp={() => setHelpOpen(true)} />
      {helpOpen && (
        <ConfirmModal
          title="Keyboard shortcuts"
          danger={false}
          confirmLabel="OK"
          onCancel={() => setHelpOpen(false)}
          onConfirm={() => setHelpOpen(false)}
        >
          <div className="kv" style={{ gridTemplateColumns: "max-content 1fr", fontSize: 12 }}>
            <dt>Ctrl / Cmd + K</dt><dd style={{ textAlign: "left" }}>Command palette</dd>
            <dt>Alt + 1–7</dt><dd style={{ textAlign: "left" }}>Jump to page</dd>
            <dt>Alt + B</dt><dd style={{ textAlign: "left" }}>Toggle sidebar</dd>
            <dt>R</dt><dd style={{ textAlign: "left" }}>Refresh data (not while typing)</dd>
            <dt>Esc</dt><dd style={{ textAlign: "left" }}>Close dialogs</dd>
          </div>
        </ConfirmModal>
      )}
    </div>
  );
}

export function authConfigured(): boolean {
  return getAuthToken() !== null;
}
