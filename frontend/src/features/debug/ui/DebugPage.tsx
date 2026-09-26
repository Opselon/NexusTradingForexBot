/**
 * Debug hub — the page shell for /alt/debug.
 *
 * Composition only: hero header + live status rail + tab rail, then one
 * mounted tab. Every tab component lives in ./tabs and owns exactly one
 * backend surface (SRP); this file owns page-level state (which tab, the
 * compare A/B handoff) and nothing else.
 *
 * Status rail honesty: the three queries it mirrors are mounted with
 * `paused = true`, i.e. NO refetch interval of their own — it is a pure
 * cache mirror of whatever the mounted tab is already polling. It never
 * fights a tab's pause control and never invents a value: missing data
 * renders as "—", and every figure is a verbatim backend field.
 *
 * Styling: ./debug.css + ./debug-panels.css + ./debug-detail.css (namespaced
 * `dbg-`, split so each sheet stays under 500 lines), imported here so the
 * feature is self-contained — no other page loads them.
 */

import { useMemo, useState } from "react";
import type { ShellPageProps } from "@/app/featureModule";
import { StatusBadge } from "@/components/primitives";
import { useI18n } from "@/stores/i18nStore";
import { useDebugFeaturesQuery, useDebugHealthQuery, useDebugStateQuery } from "../hooks";
import { StateTab } from "./tabs/StateTab";
import { HealthTab } from "./tabs/HealthTab";
import { FeaturesTab } from "./tabs/FeaturesTab";
import { FreshnessTab } from "./tabs/FreshnessTab";
import { IpcTab } from "./tabs/IpcTab";
import { CompareTab } from "./tabs/CompareTab";
import { SnapshotsTab } from "./tabs/SnapshotsTab";
import { ModelTestTab } from "./tabs/ModelTestTab";
import { TraceTab } from "./tabs/TraceTab";
import { ResearchTab } from "./tabs/ResearchTab";
import { OpsTab } from "./tabs/OpsTab";
import "./debug.css";
import "./debug-panels.css";
import "./debug-detail.css";

type TabId = "state" | "health" | "features" | "freshness" | "ipc" | "compare" | "snapshots" | "modeltest" | "trace" | "research" | "ops";

interface TabDef {
  id: TabId;
  label: string;
  icon: string;
  /** endpoint(s) the tab reads — shown as the hover hint, never guessed */
  ep: string;
}

function debugTabs(t: (k: string, fb: string, v?: Record<string, string | number>) => string): TabDef[] {
  return [
    { id: "state", label: t("debug.tab.state", "State"), icon: "▦", ep: "/api/debug/state" },
    { id: "health", label: t("debug.tab.health", "Health"), icon: "✚", ep: "/api/debug/health" },
    { id: "features", label: t("debug.tab.features", "Features"), icon: "ƒ", ep: "/api/debug/features" },
    { id: "freshness", label: t("debug.tab.freshness", "Freshness"), icon: "◴", ep: "/api/debug/freshness" },
    { id: "ipc", label: t("debug.tab.ipc", "IPC"), icon: "⇄", ep: "/api/debug/ipc-telemetry" },
    { id: "compare", label: t("debug.tab.compare", "Compare"), icon: "⚖", ep: "/api/debug/compare" },
    { id: "snapshots", label: t("debug.tab.snapshots", "Snapshots"), icon: "◈", ep: "/api/debug/snapshots" },
    { id: "modeltest", label: t("debug.tab.modeltest", "Model test"), icon: "⚗", ep: "POST /api/debug/model-test" },
    { id: "trace", label: t("debug.tab.trace", "Trace"), icon: "⌁", ep: "/api/debug/trace/{id}" },
    { id: "research", label: t("debug.tab.research", "Research"), icon: "⌕", ep: "/api/research/*" },
    { id: "ops", label: t("debug.tab.ops", "Ops"), icon: "⚙", ep: "/api/simulation/tick · /api/observability/stats" },
  ];
}

const ENDPOINTS = [
  "/api/debug/state",
  "/api/debug/health",
  "/api/debug/features",
  "/api/debug/freshness",
  "/api/debug/ipc-telemetry",
  "/api/debug/snapshots",
  "/api/debug/compare",
  "/api/debug/trace/{id}",
  "POST /api/debug/model-test",
  "/api/research/*",
];

/* ------------------------------------------------------------------ */
/* Status rail — cache mirror of state/health/features (no interval)   */
/* ------------------------------------------------------------------ */

function modeTone(mode: string | undefined): string {
  const m = (mode ?? "").toUpperCase();
  if (m === "LIVE") return "live";
  if (m === "PAPER") return "paper";
  if (m === "SHADOW") return "shadow";
  return "neutral";
}

function RailItem({ label, value, tone, title, err }: { label: string; value: string; tone?: string; title?: string; err?: string | null }) {
  return (
    <div className="dbg-rail-item" title={err ?? title}>
      <span className="dbg-rail-label">{label}</span>
      <span className={`dbg-rail-value ${tone ?? ""}`}>
        {value}
        {err && (
          <span className="dbg-rail-err" role="img" aria-label={err} title={err}>
            !
          </span>
        )}
      </span>
    </div>
  );
}

function StatusRail() {
  const t = useI18n((s) => s.t);
  // paused=true ⇒ refetchInterval false: this observer never polls on its own.
  const state = useDebugStateQuery(true);
  const health = useDebugHealthQuery(true);
  const features = useDebugFeaturesQuery(true);

  // A failed mirror must not read as "not loaded": the rail keeps the absent
  // value ("—") and carries the backend's own error words instead of a guess.
  const railErr = (q: { isError: boolean; error: unknown }): string | null =>
    q.isError
      ? `${t("debug.rail.query_failed", "query failed — backend message:")}${q.error instanceof Error ? ` ${q.error.message}` : ""}`
      : null;
  const errState = railErr(state);
  const errHealth = railErr(health);
  const errFeatures = railErr(features);

  const rt = (state.data?.runtime ?? {}) as Record<string, unknown>;
  const mode = typeof rt.mode === "string" ? rt.mode : undefined;
  const symbol = typeof rt.symbol === "string" ? rt.symbol : undefined;
  const timeframe = typeof rt.timeframe === "string" ? rt.timeframe : undefined;
  const age = features.data?.age_seconds;

  return (
    <div className="dbg-rail" aria-label={t("debug.rail.aria", "live status rail")}>
      <div className="dbg-rail-cell">
        <RailItem
          label={t("debug.rail.engine", "engine")}
          value={mode ?? "—"}
          tone={modeTone(mode)}
          err={errState}
          title={
            mode
              ? t("debug.rail.engine_title", "mode {mode}{suffix}", {
                  mode,
                  suffix: symbol ? t("debug.rail.engine_title_suffix", " · {symbol} {timeframe}", { symbol, timeframe: timeframe ?? "" }) : "",
                })
              : t("debug.rail.not_loaded", "state query has not loaded")
          }
        />
      </div>
      <div className="dbg-rail-cell" title={errHealth ?? undefined}>
        <span className="dbg-rail-label">{t("debug.rail.health", "health")}</span>
        <span className="dbg-rail-value">
          {health.data ? <StatusBadge status={health.data.overall_status} /> : "—"}
          {errHealth && (
            <span className="dbg-rail-err" role="img" aria-label={errHealth} title={errHealth}>
              !
            </span>
          )}
        </span>
      </div>
      <div className="dbg-rail-cell">
        <RailItem
          label={t("debug.rail.vector", "vector")}
          value={features.data ? `${features.data.feature_count}D` : "—"}
          tone={features.data ? (features.data.is_stale ? "stale" : "fresh") : undefined}
          err={errFeatures}
          title={
            features.data
              ? t("debug.rail.vector_title", "age {age} · threshold {threshold}s · anomalies {anomalies}", {
                  age: age === null || age === undefined ? t("debug.rail.unknown", "unknown") : `${age.toFixed(1)}s`,
                  threshold: features.data.stale_threshold_seconds,
                  anomalies: features.data.anomaly_count,
                })
              : t("debug.rail.features_not_loaded", "features query has not loaded")
          }
        />
      </div>
      <div className="dbg-rail-cell">
        <RailItem
          label={t("debug.rail.anomalies", "anomalies")}
          value={features.data ? `${features.data.anomaly_count}` : "—"}
          tone={features.data ? (features.data.anomaly_count > 0 ? "bad" : "fresh") : undefined}
          err={errFeatures}
          title={
            features.data
              ? t("debug.rail.anomalies_title", "NaN {nan} · Inf {inf} of {total}", {
                  nan: features.data.nan_count,
                  inf: features.data.inf_count,
                  total: features.data.feature_count,
                })
              : t("debug.rail.features_not_loaded", "features query has not loaded")
          }
        />
      </div>
      <div className="dbg-rail-cell">
        <RailItem
          label={t("debug.rail.snapshot", "snapshot")}
          value={state.data?.snapshot_id ?? "—"}
          title={state.data?.timestamp ? t("debug.rail.snapshot_title", "captured {at}", { at: state.data.timestamp }) : t("debug.rail.no_snapshot", "no snapshot id")}
        />
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Page                                                                */
/* ------------------------------------------------------------------ */

export default function DebugPage(props: ShellPageProps) {
  void props;
  const t = useI18n((s) => s.t);
  const TABS = useMemo(() => debugTabs(t), [t]);
  const [tab, setTab] = useState<TabId>("state");
  const [cmpA, setCmpA] = useState<string | null>(null);
  const [cmpB, setCmpB] = useState<string | null>(null);

  const sendToCompare = (id: string, slot: "a" | "b") => {
    if (slot === "a") setCmpA(id);
    else setCmpB(id);
    setTab("compare");
  };

  return (
    <div className="dbg-page l3-wrap">
      <header className="dbg-hero">
        <div className="dbg-hero-main">
          <div className="dbg-eyebrow">
            <span className="dbg-eyebrow-bar" aria-hidden="true" />
            {t("debug.head.crumb2", "PLATFORM · DEBUG HUB")}
          </div>
          <h1 className="dbg-title">{t("debug.head.title2", "Debug everything, verbatim.")}</h1>
          <p className="dbg-desc">
            {t(
              "debug.head.desc2",
              "Canonical snapshot · subsystem health · feature contract · freshness · IPC · compare · snapshots · model test · traces · research · ops (legacy tab-debug). Every figure below is a backend read — UNAVAILABLE sections show their own reason + correlation id, never a blank lie.",
            )}
          </p>
          <div className="dbg-endpoints" aria-label={t("debug.head.endpoints_aria", "endpoints served by this hub")}>
            {ENDPOINTS.map((ep) => (
              <span className="dbg-ep" key={ep}>
                {ep}
              </span>
            ))}
          </div>
        </div>
        <StatusRail />
      </header>

      <nav className="dbg-tabs" role="tablist" aria-label={t("debug.head.tabs_aria", "debug sections")}>
        {TABS.map((t) => (
          <button
            key={t.id}
            role="tab"
            aria-selected={tab === t.id}
            className={`dbg-tab ${tab === t.id ? "active" : ""}`}
            title={t.ep}
            onClick={() => setTab(t.id)}
          >
            <span className="dbg-tab-ico" aria-hidden="true">
              {t.icon}
            </span>
            <span className="dbg-tab-label">{t.label}</span>
          </button>
        ))}
      </nav>

      <div className="dbg-tabbody" key={tab}>
        {tab === "state" && <StateTab />}
        {tab === "health" && <HealthTab />}
        {tab === "features" && <FeaturesTab />}
        {tab === "freshness" && <FreshnessTab />}
        {tab === "ipc" && <IpcTab />}
        {tab === "compare" && <CompareTab a={cmpA} b={cmpB} setA={setCmpA} setB={setCmpB} />}
        {tab === "snapshots" && <SnapshotsTab onSendToCompare={sendToCompare} />}
        {tab === "modeltest" && <ModelTestTab />}
        {tab === "trace" && <TraceTab />}
        {tab === "research" && <ResearchTab />}
        {tab === "ops" && <OpsTab />}
      </div>
    </div>
  );
}
