/**
 * App shell: routing, providers, top status bar, banners, navigation.
 *
 * Server state baseline comes from TanStack Query (`/api/status` snapshot);
 * the realtime layer (core/realtime SSE) merges live updates on top. LIVE/PAPER
 * and every authoritative value render from backend data only.
 *
 * Pro UX layer (presentation only): persistent sidebar/topbar, density mode,
 * freshness meters, keyboard navigation (Alt+1..9, Alt+B), dismissible stale
 * banners, and the command-result toast host. No state decisions live here.
 *
 * Auth banner is driven by core/auth (getAuthState + auth:expired events) —
 * never by ad-hoc token probing.
 */

import { Suspense, useEffect, useRef, useState } from "react";
import { NavLink, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import DashboardPage from "@/pages/Dashboard/DashboardPage";
import TradingPage from "@/pages/Trading/TradingPage";
import PositionsPage from "@/pages/Positions/PositionsPage";
import RiskPage from "@/pages/Risk/RiskPage";
import MLPage from "@/pages/ML/MLPage";
import IntelligencePage from "@/pages/Intelligence/IntelligencePage";
import AuditPage from "@/pages/Audit/AuditPage";
import type { EngineSnapshot } from "@/types/domain";
import { engineApi } from "@/api/engineApi";
import { useRealtimeSnapshot } from "@/hooks/useRealtimeSnapshot";
import { ConnectionIndicator, FreshnessMeter } from "@/components/ConnectionIndicator";
import { ModeIndicator } from "@/components/ModeIndicator";
import { AttentionStrip } from "@/components/AttentionStrip";
import { CommandPalette } from "@/components/CommandPalette";
import { ErrorBoundary } from "@/components/ErrorBoundary";
import { ConfirmModal, ToastHost, ErrorState, LoadingState, StatusBadge } from "@/components/primitives";
import { useUiStore } from "@/stores/uiStore";
import { useI18n } from "@/stores/i18nStore";
import { LANGUAGES } from "@/lib/i18n";
import { ApiError } from "@/types/api";
import {
  FEATURE_SECTIONS,
  featureLabelKey,
  type FeatureSectionName,
} from "@/app/featureRegistry";
import { getAuthState, hasAccessToken } from "@/core/auth";
import { onCore } from "@/core/events";

/** Max Alt+<n> digit (spec: 1..9 capped). */
const MAX_ALT_ROUTES = 9;

const NAV_SECTIONS: Array<{ section: string; sectionKey: string; items: Array<{ to: string; icon: string; label: string; labelKey: string }> }> = [
  {
    section: "Operations",
    sectionKey: "ux.sidebar.operate",
    items: [
      { to: "/", icon: "◈", label: "Dashboard", labelKey: "nav.page.dashboard" },
      { to: "/trading", icon: "⇅", label: "Trading", labelKey: "nav.page.trading" },
      { to: "/positions", icon: "▤", label: "Positions", labelKey: "nav.page.positions" },
    ],
  },
  {
    section: "Safety & Intelligence",
    sectionKey: "ux.sidebar.analyze",
    items: [
      { to: "/risk", icon: "⛨", label: "Risk", labelKey: "nav.page.risk" },
      { to: "/ml", icon: "Σ", label: "ML / 70D", labelKey: "nav.page.ml" },
      { to: "/intelligence", icon: "≈", label: "Intelligence", labelKey: "nav.page.intelligence" },
      { to: "/audit", icon: "☰", label: "Audit", labelKey: "nav.page.audit" },
    ],
  },
];

/** Section -> i18n key for the registry groups (4 groups, featureRegistry order). */
const SECTION_KEYS: Record<FeatureSectionName, string> = {
  OPERATIONS: "ux.sidebar.features.operations",
  "MARKET & RESEARCH": "ux.sidebar.features.market",
  "SAFETY & GOVERNANCE": "ux.sidebar.features.safety",
  PLATFORM: "ux.sidebar.features.platform",
};

/** Registry-driven sections (lazy features) mapped into the sidebar shape. */
const FEATURE_NAV = FEATURE_SECTIONS.map((sec) => ({
  section: sec.section,
  sectionKey: SECTION_KEYS[sec.section],
  items: sec.items.map((f) => ({ to: f.route, icon: f.icon, label: f.label, labelKey: featureLabelKey(f.route) })),
}));

/** Full nav = legacy pages first (stable Alt+1..7), then feature registry. */
const ALL_NAV_SECTIONS = [...NAV_SECTIONS, ...FEATURE_NAV];

/** Flat route list for Alt+<n> keyboard navigation (visual shortcut only). */
const NAV_ROUTES = ALL_NAV_SECTIONS.flatMap((s) => s.items).map((i) => i.to);

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
        className="select lang-select"
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
  const t = useI18n((s) => s.t);
  const [nowMs, setNowMs] = useState(() => Date.now());
  // Route path drives the ErrorBoundary reset key: navigating away from (or
  // back to) a crashed page re-arms the boundary instead of wedging the tree.
  const routePathname = useLocation().pathname;
  // Active-route label (render-time): drives the tab title AND the single
    // visually-hidden <h1> per route, so every page announces exactly one h1.
    const navHit = NAV_SECTIONS.flatMap((s) => s.items).find((i) => i.to === routePathname);
    const featHit = FEATURE_SECTIONS.flatMap((s) => s.items).find((f) => f.route === routePathname);
    const routeLabel = navHit
      ? t(navHit.labelKey, navHit.label)
      : featHit
        ? t(featureLabelKey(featHit.route), featHit.label)
        : null;

  // Auth state (core/auth is the source of truth; bus keeps the banner live).
  const [authExpiredAt, setAuthExpiredAt] = useState<number | null>(() => getAuthState().lastUnauthorizedAt);
  useEffect(() => onCore("auth:expired", ({ at }) => setAuthExpiredAt(at)), []);
  useEffect(() => onCore("auth:changed", () => setAuthExpiredAt(getAuthState().lastUnauthorizedAt)), []);

  // 1s ticker for data-age display (visual only).
  useEffect(() => {
    const t = window.setInterval(() => setNowMs(Date.now()), 1000);
    return () => window.clearInterval(t);
  }, []);

  // Density class on <body> — CSS custom properties cascade from there.
  useEffect(() => {
    document.body.classList.toggle("dense", dense);
  }, [dense]);

  // Browser-tab title follows the active route (presentation only).
  useEffect(() => {
    document.title = routeLabel ? `${routeLabel} · NSE Console` : "NSE Console";
  }, [routeLabel]);

  // SPA navigation: return the scroll container to the top and move focus to
  // <main> so keyboard/SR users land on the new page, not the old scroll
  // position. Skipped on first mount — the landing page keeps its place.
  const bootedRef = useRef(false);
  useEffect(() => {
    if (!bootedRef.current) {
      bootedRef.current = true;
      return;
    }
    const main = document.getElementById("main-content");
    main?.scrollTo(0, 0);
    window.scrollTo(0, 0);
    main?.focus({ preventScroll: true });
  }, [routePathname]);

  // Alt+1..9 route jump, Alt+B sidebar, R = refresh (skipped while typing).
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
      if (Number.isInteger(digit) && digit >= 1 && digit <= Math.min(MAX_ALT_ROUTES, NAV_ROUTES.length)) {
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

  const { snapshot, realtimeStatus } = useRealtimeSnapshot(snapshotQuery.data);

  const authError =
    snapshotQuery.error instanceof ApiError && snapshotQuery.error.isAuthError
      ? (snapshotQuery.error as ApiError)
      : null;
  const showAuthBanner = authError !== null || authExpiredAt !== null;
  const lf = snapshot?.live_freshness ?? null;

  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">Skip to content</a>
      <aside aria-label="Sidebar" className={`sidebar ${collapsed ? "collapsed" : ""}`}>
        <div className="brand">
          <div className="brand-logo">NSE</div>
          <div className="brand-text">
            NEXUS SCALP ENGINE
            <span className="sub">PRO CONSOLE</span>
          </div>
        </div>
        <nav className="nav" aria-label="Primary">
          {ALL_NAV_SECTIONS.map((sec) => (
            <div key={sec.section}>
              <div className="nav-section">{t(sec.sectionKey, sec.section)}</div>
              {sec.items.map((item) => (
                <NavLink
                  key={item.to}
                  to={item.to}
                  end={item.to === "/"}
                  className={({ isActive }) => `nav-item ${isActive ? "active" : ""}`}
                  title={t(item.labelKey, item.label)}
                >
                  <span className="icon">{item.icon}</span>
                  <span className="nav-label">{t(item.labelKey, item.label)}</span>
                </NavLink>
              ))}
            </div>
          ))}
        </nav>
        <div className="sidebar-foot">
          <div className="side-row">
            <span>{t("ux.density.label", "DENSITY")}</span>
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
          <div className="side-row" title={t("ux.shortcut.help", "Keyboard shortcuts")}>
            <span><kbd>alt</kbd> 1–9 · <kbd>ctrl</kbd>K</span>
          </div>
          <button className="sidebar-toggle" onClick={toggleSidebar} title="Toggle sidebar (Alt+B)" aria-label="Toggle sidebar" aria-expanded={!collapsed}>
            {collapsed ? "»" : "«"}
          </button>
        </div>
      </aside>

      <div className="main-col">
        <header aria-label="Top bar" className="topbar">
          <ModeIndicator snapshot={snapshot} />
          <span className="conn-chip" title="Engine loop state (backend-authoritative)">
            <span className={`conn-dot ${snapshot?.engine_running ? "connected" : snapshot ? "disconnected" : "reconnecting"}`} />
            <span>ENGINE {snapshot ? (snapshot.engine_running ? "RUNNING" : "STOPPED") : "—"}</span>
          </span>
          <span className="conn-chip" title="Backend health.overall from /api/status">
            {snapshot ? <StatusBadge status={snapshot.health.overall} /> : <span className="badge unknown">HEALTH —</span>}
          </span>
          {snapshot && (
            <span className="conn-chip freshness-chip" title="Pipeline freshness stages (backend live_freshness)">
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

        {!showAuthBanner && <AttentionStrip snapshot={snapshot} feed={realtimeStatus} nowMs={nowMs} />}
        {showAuthBanner && (
          <div className="banner auth" role="alert">
            <span>
              {t("ux.auth.banner", "⛔ Backend rejected the web-auth credential (WEB-AUTH-P0). The console self-bootstraps via the first-party cookie (BUG-267) — if this persists, reload once, or open as")}{" "}
              <span className="inline-mono">…/?token=&lt;NSE_WEB_AUTH_TOKEN&gt;</span>
              {t("ux.auth.banner.suffix", "(token kept in sessionStorage only).")}
              {!hasAccessToken() && (
                <span className="muted start-8">
                  {t("ux.auth.mode.cookie", "Mode: cookie-only (no bearer token).")}
                </span>
              )}
            </span>
          </div>
        )}

        <main className="page" id="main-content" tabIndex={-1}>
          <h1 className="sr-only">{routeLabel ?? "NSE Console"}</h1>
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
              <Route path="/" element={<DashboardRoute snapshot={snapshot} nowMs={nowMs} />} />
              <Route path="/trading" element={<TradingRoute snapshot={snapshot} nowMs={nowMs} />} />
              <Route path="/positions" element={<PositionsRoute snapshot={snapshot} />} />
              <Route path="/risk" element={<RiskRoute snapshot={snapshot} />} />
              <Route path="/ml" element={<MlRoute snapshot={snapshot} />} />
              <Route path="/intelligence" element={<IntelRoute snapshot={snapshot} />} />
              <Route path="/audit" element={<AuditRoute />} />
              {FEATURE_SECTIONS.flatMap((sec) => sec.items).map((f) => (
                <Route
                  key={f.route}
                  path={f.route}
                  element={
                    <ErrorBoundary label={f.label} resetKey={routePathname}>
                      <Suspense fallback={<LoadingState label={`Loading ${f.label}…`} />}>
                        <f.lazy snapshot={snapshot} nowMs={nowMs} />
                      </Suspense>
                    </ErrorBoundary>
                  }
                />
              ))}
              <Route path="*" element={<ErrorState message="Unknown route" />} />
            </Routes>
          )}
        </main>
      </div>
      <ToastHost />
      <CommandPalette onOpenHelp={() => setHelpOpen(true)} />
      {helpOpen && (
        <ConfirmModal
          title={t("ux.shortcut.help", "Keyboard shortcuts")}
          danger={false}
          confirmLabel={t("ux.confirm.ok", "OK")}
          onCancel={() => setHelpOpen(false)}
          onConfirm={() => setHelpOpen(false)}
        >
          <div className="kv left">
            <dt>Ctrl / Cmd + K</dt><dd>{t("ux.shortcut.palette", "Command palette")}</dd>
            <dt>Alt + 1–9</dt><dd>{t("ux.shortcut.jump", "Jump to page")}</dd>
            <dt>Alt + B</dt><dd>{t("ux.shortcut.sidebar", "Toggle sidebar")}</dd>
            <dt>R</dt><dd>{t("ux.shortcut.refresh", "Refresh data (not while typing)")}</dd>
            <dt>Esc</dt><dd>{t("ux.shortcut.esc", "Close dialogs")}</dd>
          </div>
        </ConfirmModal>
      )}
    </div>
  );
}

/* Legacy page routes are statically imported (parity guarantee — they must
 * keep working while feature pages lazy-load). Wrappers keep the JSX terse. */
function DashboardRoute({ snapshot, nowMs }: { snapshot: EngineSnapshot | undefined; nowMs: number }) {
  return <DashboardPage snapshot={snapshot} nowMs={nowMs} />;
}
function TradingRoute({ snapshot, nowMs }: { snapshot: EngineSnapshot | undefined; nowMs: number }) {
  return <TradingPage snapshot={snapshot} nowMs={nowMs} />;
}
function PositionsRoute({ snapshot }: { snapshot: EngineSnapshot | undefined }) {
  return <PositionsPage snapshot={snapshot} />;
}
function RiskRoute({ snapshot }: { snapshot: EngineSnapshot | undefined }) {
  return <RiskPage snapshot={snapshot} />;
}
function MlRoute({ snapshot }: { snapshot: EngineSnapshot | undefined }) {
  return <MLPage snapshot={snapshot} />;
}
function IntelRoute({ snapshot }: { snapshot: EngineSnapshot | undefined }) {
  return <IntelligencePage snapshot={snapshot} />;
}
function AuditRoute() {
  return <AuditPage />;
}

/** Back-compat helper (other lanes import it from AppShell). */
export function authConfigured(): boolean {
  return hasAccessToken();
}
