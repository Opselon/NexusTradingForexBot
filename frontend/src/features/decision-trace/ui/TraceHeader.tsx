/**
 * TraceHeader — hero header for the decision-trace page (§01/§38, RUNTIME
 * TOPOLOGY OBSERVABILITY).
 *
 * Structure follows the wave's merged heroes (kicker → glyph + gradient
 * title → description + endpoint provenance chips | status side), then the
 * runtime identity rail: ENGINE · MODE · MT5 · SYMBOL · TF · MODEL · REGIME ·
 * OBSERVER · SCHEMA — every value is a verbatim field from the shell's
 * EngineSnapshot or the observer status. Missing data renders `UNKNOWN`/`—`,
 * never a synthesized value.
 *
 * Status/mode/regime tokens shown as UI TEXT go through localized
 * enum→label maps (data tokens stay verbatim in payloads/comparisons);
 * technical identifiers (MT5, symbols, model ids, schema versions, endpoint
 * paths) render verbatim in every language.
 *
 * OWNER:    uiux-modern-20260926 lane 6 (decision-trace).
 * CONSUMES: EngineSnapshot props, observer status token, i18n.
 * PROVIDES: <header class="dt-header"> hero + identity rail.
 * INVARIANTS: presentation only — no query, no fetch, no changed value or
 *           wording; endpoint paths are provenance FACTS copied from
 *           api.ts's own endpoint list, never inferred here; class prefix
 *           `dt` (contract §3).
 * EXTEND:   new hero visuals go in decision-trace-hero.css, never a global.
 */

import { useI18n } from "@/stores/i18nStore";
import type { EngineSnapshot } from "@/types/domain";

type T = (key: string, fallback: string) => string;

/** Backend endpoints this page reads — verbatim from api.ts:5-17 (provenance). */
const ENDPOINTS: readonly string[] = [
  "/api/trace/observer",
  "/api/trace/topology",
  "/api/trace/latency",
  "/api/trace/integrity",
  "/api/trace/decisions",
  "/api/trace/events",
  "/api/trace/why/{event_id}",
  "/api/trace/bundle/{key}",
  "/api/trace/stream?last_seq=",
];

function val(v: unknown): string {
  if (v === null || v === undefined || v === "") return "UNKNOWN";
  return String(v);
}

function modeLabel(t: T, mode: string): string {
  switch (mode) {
    case "LIVE": return t("trace.mode.live", "LIVE");
    case "PAPER": return t("trace.mode.paper", "PAPER");
    case "SHADOW": return t("trace.mode.shadow", "SHADOW");
    case "REPLAY": return t("trace.mode.replay", "REPLAY");
    case "BACKTEST": return t("trace.mode.backtest", "BACKTEST");
    default: return mode;
  }
}

function regimeLabel(t: T, regime: string): string {
  switch (regime) {
    case "MACRO_NEWS_FREEZE": return t("trace.regime.macro_news_freeze", "MACRO_NEWS_FREEZE");
    case "HIGH_SPREAD_CHOP": return t("trace.regime.high_spread_chop", "HIGH_SPREAD_CHOP");
    case "VOLATILITY_EXPANSION": return t("trace.regime.volatility_expansion", "VOLATILITY_EXPANSION");
    case "TRENDING_MOMENTUM": return t("trace.regime.trending_momentum", "TRENDING_MOMENTUM");
    case "RANGING_MEAN_REVERSION": return t("trace.regime.ranging_mean_reversion", "RANGING_MEAN_REVERSION");
    default: return regime;
  }
}

function observerLabel(t: T, status: string): string {
  switch (status) {
    case "OFF": return t("trace.observer.off", "OFF");
    case "STARTING": return t("trace.observer.starting", "STARTING");
    case "ACTIVE": return t("trace.observer.active", "ACTIVE");
    case "STOPPING": return t("trace.observer.stopping", "STOPPING");
    case "ERROR": return t("trace.observer.error", "ERROR");
    default: return status;
  }
}

/** Colour only — the displayed word is never changed by this map. */
function toneOf(token: string | null | undefined): string {
  const w = (token ?? "").toUpperCase();
  if (!w || w === "UNKNOWN" || w === "—") return "unknown";
  if (w === "ACTIVE" || w === "RUNNING") return "ok";
  if (w === "ERROR" || w === "FAILED") return "fail";
  if (w === "STARTING" || w === "STOPPING" || w === "OFF" || w === "RECONNECTING") return "warn";
  return "neutral";
}

function RailItem({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  const unknown = value === "UNKNOWN" || value === "—";
  return (
    <div className={`dt-rail-item ${unknown ? "unknown" : ""}`} title={`${label}: ${value}`}>
      <span className="dt-rail-label">{label}</span>
      <span className={`dt-rail-value ${mono ? "mono" : ""}`}>{value}</span>
    </div>
  );
}

export function TraceHeader({
  snapshot,
  schemaVersion,
  observerStatus,
}: {
  snapshot?: EngineSnapshot;
  schemaVersion?: number;
  observerStatus?: string;
}) {
  const t = useI18n((s) => s.t);
  const engine = snapshot;
  const engineTone = engine?.engine_running ? "run" : "stop";

  const engineStatus = !engine
    ? t("trace.status.unknown", "UNKNOWN")
    : engine.engine_running
      ? t("trace.status.running", "RUNNING")
      : t("trace.status.stopped", "STOPPED");

  const mode = val(engine?.execution_mode);
  const regime = val(engine?.regime);
  const observer = val(observerStatus);

  return (
    <header className="dt-header" role="banner">
      <div className="dt-hero">
        <span className="dt-hero-mesh" aria-hidden="true" />

        <div className="dt-hero-main">
          <div className="dt-eyebrow">
            <span className="dt-eyebrow-dot" aria-hidden="true" />
            {t("trace.header.eyebrow", "RUNTIME TOPOLOGY OBSERVABILITY")}
            <span className="dt-eyebrow-rule" aria-hidden="true" />
          </div>
          <div className="dt-title-row">
            <span className="dt-hero-glyph" aria-hidden="true">⌁</span>
            <h1 className="dt-title">{t("trace.header.title", "Decision Trace")}</h1>
          </div>
          <p className="dt-hero-desc">
            {t("trace.header.note", "Observability window into the live engine — the runtime is the only source of truth.")}
          </p>
          <div
            className="dt-hero-endpoints"
            aria-label={t("trace.header.endpoints", "backend endpoints this page reads")}
          >
            {ENDPOINTS.map((ep) => (
              <span
                className="dt-ep"
                key={ep}
                title={t(
                  "trace.header.endpoint_title",
                  "Provenance — backend endpoint {ep}: every figure on this page is read from it, never inferred here",
                  { ep },
                )}
              >
                <span className="dt-ep-dot" aria-hidden="true" />
                {ep}
              </span>
            ))}
          </div>
        </div>

        {/* Status side — mirrors the page's OWN inputs (EngineSnapshot +
         * observer query); it never polls anything of its own. */}
        <div className="dt-hero-side">
          <div
            className={`dt-status-pill ${engineTone === "run" ? "ok" : "fail"}`}
            title={
              engine?.engine_running
                ? t("trace.header.engine_running", "engine running")
                : t("trace.header.engine_stopped", "engine stopped")
            }
          >
            <span className="dt-status-dot" aria-hidden="true" />
            <span className="dt-status-label">{t("trace.header.engine", "ENGINE")}</span>
            <span className="dt-status-value">{engineStatus}</span>
          </div>
          <div className={`dt-status-pill ${toneOf(observerStatus)}`} title={`${t("trace.header.observer", "OBSERVER")}: ${observer}`}>
            <span className="dt-status-dot" aria-hidden="true" />
            <span className="dt-status-label">{t("trace.header.observer", "OBSERVER")}</span>
            <span className="dt-status-value">{observerLabel(t, observer)}</span>
          </div>
        </div>
      </div>

      <div className="dt-header-rail" role="list" aria-label={t("trace.header.identity", "Runtime identity")}>
        <RailItem label={t("trace.header.mode", "MODE")} value={modeLabel(t, mode)} mono />
        <RailItem label="MT5" value={val(engine?.adapter_class)} mono />
        <RailItem label={t("trace.header.symbol", "SYMBOL")} value={val(engine?.symbol)} mono />
        <RailItem label={t("trace.header.tf", "TF")} value={val(engine?.data_source)} mono />
        <RailItem label={t("trace.header.model", "MODEL")} value={val(engine?.model?.model_id)} mono />
        <RailItem label={t("trace.header.regime", "REGIME")} value={regimeLabel(t, regime)} mono />
        <RailItem label={t("trace.header.schema", "SCHEMA")} value={schemaVersion ? `v${schemaVersion}` : "UNKNOWN"} mono />
      </div>
    </header>
  );
}
