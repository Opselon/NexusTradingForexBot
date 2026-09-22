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
 * Styling: ./debug.css (namespaced `dbg-`), imported here so the feature is
 * self-contained — no other page loads it.
 */

import { useState } from "react";
import type { ShellPageProps } from "@/app/featureModule";
import { StatusBadge } from "@/components/primitives";
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

type TabId = "state" | "health" | "features" | "freshness" | "ipc" | "compare" | "snapshots" | "modeltest" | "trace" | "research" | "ops";

interface TabDef {
  id: TabId;
  label: string;
  icon: string;
  /** endpoint(s) the tab reads — shown as the hover hint, never guessed */
  ep: string;
}

const TABS: TabDef[] = [
  { id: "state", label: "State", icon: "▦", ep: "/api/debug/state" },
  { id: "health", label: "Health", icon: "✚", ep: "/api/debug/health" },
  { id: "features", label: "Features", icon: "ƒ", ep: "/api/debug/features" },
  { id: "freshness", label: "Freshness", icon: "◴", ep: "/api/debug/freshness" },
  { id: "ipc", label: "IPC", icon: "⇄", ep: "/api/debug/ipc-telemetry" },
  { id: "compare", label: "Compare", icon: "⚖", ep: "/api/debug/compare" },
  { id: "snapshots", label: "Snapshots", icon: "◈", ep: "/api/debug/snapshots" },
  { id: "modeltest", label: "Model test", icon: "⚗", ep: "POST /api/debug/model-test" },
  { id: "trace", label: "Trace", icon: "⌁", ep: "/api/debug/trace/{id}" },
  { id: "research", label: "Research", icon: "⌕", ep: "/api/research/*" },
  { id: "ops", label: "Ops", icon: "⚙", ep: "/api/simulation/tick · /api/observability/stats" },
];

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

function RailItem({ label, value, tone, title }: { label: string; value: string; tone?: string; title?: string }) {
  return (
    <div className="dbg-rail-item" title={title}>
      <span className="dbg-rail-label">{label}</span>
      <span className={`dbg-rail-value ${tone ?? ""}`}>{value}</span>
    </div>
  );
}

function StatusRail() {
  // paused=true ⇒ refetchInterval false: this observer never polls on its own.
  const state = useDebugStateQuery(true);
  const health = useDebugHealthQuery(true);
  const features = useDebugFeaturesQuery(true);

  const rt = (state.data?.runtime ?? {}) as Record<string, unknown>;
  const mode = typeof rt.mode === "string" ? rt.mode : undefined;
  const symbol = typeof rt.symbol === "string" ? rt.symbol : undefined;
  const timeframe = typeof rt.timeframe === "string" ? rt.timeframe : undefined;
  const age = features.data?.age_seconds;

  return (
    <div className="dbg-rail" aria-label="live status rail">
      <div className="dbg-rail-cell">
        <RailItem label="engine" value={mode ?? "—"} tone={modeTone(mode)} title={mode ? `mode ${mode}${symbol ? ` · ${symbol} ${timeframe ?? ""}` : ""}` : "state query has not loaded"} />
      </div>
      <div className="dbg-rail-cell">
        <span className="dbg-rail-label">health</span>
        <span className="dbg-rail-value">{health.data ? <StatusBadge status={health.data.overall_status} /> : "—"}</span>
      </div>
      <div className="dbg-rail-cell">
        <RailItem
          label="vector"
          value={features.data ? `${features.data.feature_count}D` : "—"}
          tone={features.data ? (features.data.is_stale ? "stale" : "fresh") : undefined}
          title={features.data ? `age ${age === null || age === undefined ? "unknown" : `${age.toFixed(1)}s`} · threshold ${features.data.stale_threshold_seconds}s · anomalies ${features.data.anomaly_count}` : "features query has not loaded"}
        />
      </div>
      <div className="dbg-rail-cell">
        <RailItem
          label="anomalies"
          value={features.data ? `${features.data.anomaly_count}` : "—"}
          tone={features.data ? (features.data.anomaly_count > 0 ? "bad" : "fresh") : undefined}
          title={features.data ? `NaN ${features.data.nan_count} · Inf ${features.data.inf_count} of ${features.data.feature_count}` : "features query has not loaded"}
        />
      </div>
      <div className="dbg-rail-cell">
        <RailItem label="snapshot" value={state.data?.snapshot_id ?? "—"} title={state.data?.timestamp ? `captured ${state.data.timestamp}` : "no snapshot id"} />
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Page                                                                */
/* ------------------------------------------------------------------ */

export default function DebugPage(props: ShellPageProps) {
  void props;
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
            PLATFORM · DEBUG HUB
          </div>
          <h1 className="dbg-title">Debug everything, verbatim.</h1>
          <p className="dbg-desc">
            Canonical snapshot · subsystem health · feature contract · freshness · IPC · compare · snapshots · model test · traces · research · ops (legacy tab-debug). Every figure below is a backend
            read — UNAVAILABLE sections show their own reason + correlation id, never a blank lie.
          </p>
          <div className="dbg-endpoints" aria-label="endpoints served by this hub">
            {ENDPOINTS.map((ep) => (
              <span className="dbg-ep" key={ep}>
                {ep}
              </span>
            ))}
          </div>
        </div>
        <StatusRail />
      </header>

      <nav className="dbg-tabs" role="tablist" aria-label="debug sections">
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
