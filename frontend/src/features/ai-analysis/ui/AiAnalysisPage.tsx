/**
 * AI Analysis — signals, decisions, indicators, 70D shadow, intel (legacy parity).
 *
 * Sections: hero live-signal card (action + radial confidence gauge +
 * entry/SL/TP price ladder with R:R), KPI strip + charts (action donut,
 * confidence timeline, stage/reason bars), signal history (page through v1),
 * indicators console (ui/IndicatorsConsole.tsx), shadow-70D envelope + deep
 * panel, intel hub (orphan panels, now wired). Per-decision drilldown opens a
 * drawer wired to gates/evidence/explanation.
 * No signal/decision is ever synthesized: RESOURCE_NOT_FOUND renders empty.
 * Charts are presentation-only derivations (vizMath.ts): backend counts,
 * backend rows, backend-supplied levels; dropped rows are captioned, never hidden.
 */

// Wave-2 latency: the five heavyweight sections (indicators console 19KB,
// decision drawer, shadow-deep, two intel panels) load ONLY when their tab or
// the drawer opens. Verified page-local: no importer outside this feature, so
// lazy() is a pure payload win for the default "Decisions & history" view.
const IndicatorsConsole = lazy(() => import("./IndicatorsConsole"));
const DecisionDrawer = lazy(() => import("./DecisionDrawer"));
const Shadow70DeepPanel = lazy(() =>
  import("./Shadow70DeepPanel").then((m) => ({ default: m.Shadow70DeepPanel })),
);
const IntelligenceTelemetryPanel = lazy(() =>
  import("./IntelligenceTelemetryPanel").then((m) => ({ default: m.IntelligenceTelemetryPanel })),
);
const PositionTimelineLookup = lazy(() =>
  import("./PositionTimelineLookup").then((m) => ({ default: m.PositionTimelineLookup })),
);

/** Uniform skeleton while a lazy section chunk loads (once per session). */
function TabFallback() {
  return <Skeleton count={4} />;
}

import { lazy, memo, Suspense, useCallback, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { DataTable, EmptyState, ErrorState, MetricCard, Panel, ProbBar, Segmented, Skeleton } from "@/components/primitives";
import { ConfidenceGauge } from "@/components/viz";
import type { ShellPageProps } from "@/app/featureModule";
import { ApiError } from "@/types/api";
import { formatDateTime, formatNumber } from "@/lib/format";
import { useI18n } from "@/stores/i18nStore";
import { FreshnessCaption, GateStepper, InfoRow } from "../../research/ui/lane5Kit";
import { actionTone, confidence01, obj, str, type SignalDto } from "../model";
import { aiAnalysisQueries, orderHistory } from "../useCases";
import { actionFamily, actionKpi, countRows, timelineSeries, topEntry } from "./vizMath";
import {
  AaActionChip,
  ActionDonut,
  BarList,
  ConfidenceTimeline,
  ConfCell,
  PriceLadder,
} from "./aaCharts";
import "./aiAnalysis.css";

type Tab = "signals" | "indicators" | "shadow" | "intel";

/** Wave 2: explicit caps instead of inline magic numbers, so the caption
 *  ("showing first N") and the slice stay in lockstep. */
const DRIFT_SHOW = 12;
const SUMMARY_ROWS = 14;

export default function AiAnalysisPage(props: ShellPageProps) {
  void props;
  const t = useI18n((s) => s.t);
  const [tab, setTab] = useState<Tab>("signals");
  const [hoursBack, setHoursBack] = useState(168);
  const [page, setPage] = useState(1);
  const [tf, setTf] = useState("M1");
  const [openDecision, setOpenDecision] = useState<string | null>(null);
  // Wave 2: stable identities so the 15s latest-poll re-render can bail out of
  // the memoized drawer/rows instead of re-creating handlers each pass.
  const inspectDecision = useCallback((id: string) => setOpenDecision(id), []);
  const closeDecision = useCallback(() => setOpenDecision(null), []);

  const latestQ = useQuery({
    queryKey: ["ai-analysis", "signal-latest"],
    queryFn: ({ signal }) => aiAnalysisQueries.signalLatest(signal),
    refetchInterval: 15_000,
    retry: false,
  });
  const historyQ = useQuery({
    queryKey: ["ai-analysis", "signal-history", page, hoursBack],
    queryFn: ({ signal }) => aiAnalysisQueries.signalHistory(page, hoursBack, signal),
    retry: false,
    enabled: tab === "signals",
    placeholderData: (prev) => prev,
    refetchInterval: 60_000,
  });
  const statsQ = useQuery({
    queryKey: ["ai-analysis", "decision-stats", hoursBack],
    queryFn: ({ signal }) => aiAnalysisQueries.decisionStats(hoursBack, signal),
    retry: false,
    enabled: tab === "signals",
    // Wave 2 (perceived latency): keep the previous window on screen while the
    // new window loads — the sub-labels print the response's own window_hours,
    // so the placeholder is never mislabeled as the newly selected window.
    placeholderData: (prev) => prev,
    refetchInterval: 60_000,
  });
  const reasonsQ = useQuery({
    queryKey: ["ai-analysis", "no-trade-reasons"],
    queryFn: ({ signal }) => aiAnalysisQueries.noTradeReasons(signal),
    retry: false,
    enabled: tab === "signals",
    refetchInterval: 60_000,
  });
  const shadowQ = useQuery({
    queryKey: ["ai-analysis", "shadow70d"],
    queryFn: ({ signal }) => aiAnalysisQueries.shadow70d(signal),
    retry: false,
    enabled: tab === "shadow",
    // Wave 2: while the tab is open the envelope stays as fresh as the deep
    // panel's 30s polls instead of freezing at first mount.
    refetchInterval: 60_000,
  });

  const isNotFound = (e: unknown) => e instanceof ApiError && (e.status === 404 || e.code === "RESOURCE_NOT_FOUND");
  const latest = latestQ.data;
  const latestFamily = actionFamily(latest?.action);

  // Timeline series: page rows → normalized confidence (model rule) → sorted.
  const timeline = useMemo(
    () =>
      timelineSeries(
        (historyQ.data?.items ?? []).map((s) => ({
          generated_at: s.generated_at,
          action: s.action,
          conf01: confidence01(s.confidence),
        })),
      ),
    [historyQ.data?.items],
  );

  // Wave 2 (render perf): derived chart/table inputs computed once per data
  // change — inline countRows()/orderHistory() used to allocate fresh arrays
  // on every 15s poll, defeating memo and re-sorting 100 rows per render.
  const stageRows = useMemo(() => countRows(statsQ.data?.by_stage), [statsQ.data]);
  const reasonRows = useMemo(() => countRows(reasonsQ.data?.reasons), [reasonsQ.data]);
  const historyRows = useMemo(() => orderHistory(historyQ.data?.items ?? []), [historyQ.data?.items]);

  return (
    <div>
      <div className="page-head" style={{ display: "flex", alignItems: "baseline", gap: 10, flexWrap: "wrap" }}>
        <h2>{t("nav.feature.ai-analysis", "AI Analysis")}</h2>
        <span className="muted small">{t("ai-analysis.page.subtitle_intel", "model signals · decision gates · indicators · shadow 70D · intel")}</span>
        <FreshnessCaption timestamp={str(latest?.generated_at)} source="audit_signals ledger" isFetching={latestQ.isFetching} error={latestQ.isError && !isNotFound(latestQ.error)} />
      </div>

      <Panel title={t("ai-analysis.panel.latest", "Latest signal (live card)")} accent tight>
        {latestQ.isPending ? (
          <div className="aa-hero aa-hero-skel">
            {[0, 1, 2, 3].map((i) => (
              <div key={i} className="aa-skel-cell">
                <Skeleton count={1} />
              </div>
            ))}
          </div>
        ) : isNotFound(latestQ.error) ? (
          <EmptyState
            message={t("ai-analysis.empty.no_signals", "No signals recorded yet.")}
            hint={t("ai-analysis.empty.no_signals_hint", "/api/v1/signals/latest answers RESOURCE_NOT_FOUND — the engine has not emitted a decision.")}
          />
        ) : latestQ.isError ? (
          <ErrorState
            message={(latestQ.error as Error).message}
            requestId={latestQ.error instanceof ApiError ? latestQ.error.requestId : null}
            onRetry={() => void latestQ.refetch()}
          />
        ) : latest ? (
          <div className={`panel-body aa-hero aa-fam-${latestFamily}`}>
            <div className="aa-hero-act">
              <div className="aa-big">{latest.action ?? "—"}</div>
              <div className="aa-when">{formatDateTime(latest.generated_at)}</div>
              <button className="btn small primary" onClick={() => setOpenDecision(String(latest.request_id ?? ""))}>
                {t("ai-analysis.card.inspect", "inspect decision")}
              </button>
              <span className="tiny muted">{t("ai-analysis.card.meta", "gates · evidence · explanation")}</span>
            </div>

            <div className="aa-gauge">
              <ConfidenceGauge value={confidence01(latest.confidence)} label={t("ux.signal.confidence", "confidence")} />
            </div>

            <div className="aa-details">
              <div className="aa-meta">
                <b className="aa-symbol">{latest.symbol ?? "—"}</b>
                {latest.regime ? <span className="aa-chip aa-fam-unknown">{latest.regime}</span> : <span className="tiny faint">{t("ai-analysis.card.regime_missing", "regime not recorded")}</span>}
                {latest.execution_mode ? <span className="aa-chip aa-fam-unknown">{latest.execution_mode}</span> : null}
                <span className="aa-chip aa-fam-unknown">{latest.decision_stage ?? t("ai-analysis.card.stage_missing", "stage not recorded")}</span>
              </div>
              <ProbBar
                rows={[
                  { label: t("ux.signal.confidence", "confidence"), value: confidence01(latest.confidence), tone: actionTone(latest.action) },
                  { label: t("ai-analysis.card.before_filters", "before filters"), value: confidence01(latest.confidence_before_filters), tone: "flat" },
                  { label: t("ai-analysis.card.after_filters", "after filters"), value: confidence01(latest.confidence_after_filters), tone: "flat" },
                ]}
              />
              <dl className="kv" style={{ marginTop: 2 }}>
                <InfoRow label={t("ai-analysis.card.htf_smc", "htf / smc score")} value={`${formatNumber(latest.htf_score ?? null, 3)} / ${formatNumber(latest.smc_score ?? null, 3)}`} />
                <InfoRow label="blocked_by" value={latest.blocked_by ?? "—"} />
              </dl>
              <div className="aa-reason-line" title={latest.reason_code ?? ""}>
                {t("ai-analysis.card.reason", "reason: {r}", { r: latest.reason_code ?? "—" })}
              </div>
            </div>

            <div className="aa-hero-ladder">
              <PriceLadder entry={latest.proposed_entry ?? null} sl={latest.stop_loss ?? null} tp={latest.take_profit ?? null} />
            </div>
          </div>
        ) : null}
      </Panel>

      <div style={{ marginBlock: 12 }}>
        <Segmented
          options={[
            { id: "signals" as const, label: t("ai-analysis.tab.decisions", "Decisions & history") },
            { id: "indicators" as const, label: t("ai-analysis.tab.indicators", "Indicators wall") },
            { id: "shadow" as const, label: t("ai-analysis.tab.shadow", "Shadow 70D") },
            { id: "intel" as const, label: t("ai-analysis.tab.intel", "Intel & timeline") },
          ]}
          value={tab}
          onChange={setTab}
        />
      </div>

      {tab === "signals" && (
        <div className="aa-stack">
          <div className="aa-segbar">
            <span className="section-title" style={{ margin: 0 }}>
              {t("ai-analysis.stats.heading", "decision stats")}
            </span>
            <span className="tiny faint">{t("ai-analysis.stats.window", "window")}</span>
            <select
              aria-label={t("ai-analysis.stats.window_aria", "Stats window (hours)")}
              className="select"
              style={{ width: 92 }}
              value={hoursBack}
              onChange={(e) => {
                setHoursBack(Number(e.target.value));
                setPage(1);
              }}
            >
              {[24, 72, 168, 720].map((h) => (
                <option key={h} value={h}>
                  {h}h
                </option>
              ))}
            </select>
          </div>

          {statsQ.isPending ? (
            <div className="grid cols-4">
              {[0, 1, 2, 3].map((i) => (
                <div key={i} className="aa-metric-skel">
                  <Skeleton count={2} />
                </div>
              ))}
            </div>
          ) : statsQ.isError ? (
            <EmptyState message={statsQ.error instanceof Error ? statsQ.error.message : t("ai-analysis.empty.stats_unavailable", "stats unavailable")} />
          ) : (
            (() => {
              const st = statsQ.data;
              const kpi = actionKpi(st?.by_action);
              const top = topEntry(st?.by_stage);
              const pct = (n: number) => (kpi.total > 0 ? `${((n / kpi.total) * 100).toFixed(1)}%` : "—");
              return (
                <>
                  <div className="grid cols-4">
                    <MetricCard label={t("ai-analysis.stats.decisions_in_window", "decisions in window")} value={kpi.total.toLocaleString()} sub={t("ai-analysis.stats.scanned_path", "/decisions/stats · {h}h scanned", { h: st?.window_hours ?? hoursBack })} />
                    <MetricCard label={t("ai-analysis.stats.trade_actions", "trade actions")} value={kpi.trade.toLocaleString()} sub={t("ai-analysis.stats.trade_sub", "{p} of decisions · non-NO_TRADE", { p: pct(kpi.trade) })} />
                    <MetricCard label={t("ai-analysis.stats.no_trade", "no-trade")} value={kpi.noTrade.toLocaleString()} sub={t("ai-analysis.stats.notrade_sub", "{p} of decisions · filter blocks", { p: pct(kpi.noTrade) })} />
                    <MetricCard
                      label={t("ai-analysis.stats.top_stage", "top stage")}
                      value={<span style={{ fontSize: 13, fontWeight: 700 }}>{top?.label ?? "—"}</span>}
                      sub={top ? `${top.count.toLocaleString()} · ${pct(top.count)}` : t("ai-analysis.stats.no_stage_rows", "no stage rows")}
                    />
                  </div>

                  <div className="grid cols-2">
                    <Panel title={t("ai-analysis.panel.decision_mix", "Decision mix (by action)")} tight>
                      <div className="panel-body">
                        <ActionDonut byAction={st?.by_action} />
                      </div>
                    </Panel>
                    <Panel title={t("ai-analysis.panel.no_trade_reasons", "NO_TRADE reasons")} right={<span className="tiny faint">{reasonsQ.isPending ? t("ai-analysis.panel.loading", "loading…") : t("ai-analysis.reasons.in_ledger", "{n} in ledger", { n: (reasonsQ.data?.total ?? 0).toLocaleString() })}</span>} tight>
                      <div className="panel-body">
                        <BarList rows={reasonRows} tone="var(--amber)" max={8} />
                      </div>
                    </Panel>
                  </div>

                  <div className="grid cols-2">
                    <Panel title={t("ai-analysis.panel.stage_distribution", "Decision stage distribution")} right={<span className="tiny faint">{t("ai-analysis.panel.stage_sort_note", "sorted by count — backend returns no stage order")}</span>} tight>
                      <div className="panel-body">
                        <BarList rows={stageRows} tone="var(--violet)" max={20} />
                      </div>
                    </Panel>
                    <Panel
                      title={t("ai-analysis.panel.conf_over_time", "Confidence over time")}
                      right={<span className="tiny faint">{historyQ.isPending ? t("ai-analysis.panel.loading", "loading…") : t("ai-analysis.panel.history_page_rows", "current history page · {n} rows max", { n: String(historyQ.data?.page_size ?? "—") })}</span>}
                      tight
                    >
                      <div className="panel-body">
                        <ConfidenceTimeline series={timeline} />
                      </div>
                    </Panel>
                  </div>
                </>
              );
            })()
          )}

          <Panel title={t("ai-analysis.panel.history", "Signal history (ledger, newest first)")} tight>
            {historyQ.isPending ? (
              <Skeleton count={5} />
            ) : historyQ.isError ? (
              <ErrorState message={historyQ.error instanceof Error ? historyQ.error.message : t("ai-analysis.empty.history_failed", "history failed")} onRetry={() => void historyQ.refetch()} />
            ) : (historyQ.data?.items ?? []).length === 0 ? (
              <EmptyState message={t("ai-analysis.empty.window", "No signals in this window.")} />
            ) : (
              <>
                <DataTable
                  headers={[
                    { label: t("ai-analysis.th.decision", "decision") },
                    { label: t("ai-analysis.th.symbol", "symbol") },
                    { label: t("ai-analysis.th.action", "action") },
                    { label: t("ai-analysis.th.conf", "conf"), num: true },
                    { label: t("ai-analysis.th.stage", "stage") },
                    { label: t("ai-analysis.th.reason", "reason") },
                    { label: t("ai-analysis.th.at", "at") },
                    { label: "" },
                  ]}
                >
                  {historyRows.map((s: SignalDto) => (
                    <HistoryRow key={s.request_id ?? s.generated_at} s={s} onInspect={inspectDecision} />
                  ))}
                </DataTable>
                <div style={{ display: "flex", gap: 8, marginTop: 8, alignItems: "center" }}>
                  <button className="btn small" disabled={page <= 1} onClick={() => setPage((p) => Math.max(1, p - 1))}>
                    {t("ai-analysis.pager.newer", "← newer")}
                  </button>
                  <span className="tiny muted" aria-live="polite">
                    {t("ai-analysis.pager.page", "page {p}", { p: page })}
                    {historyQ.isFetching && " · " + t("ai-analysis.panel.loading", "loading…")}
                  </span>
                  <button className="btn small" disabled={!historyQ.data?.has_more} onClick={() => setPage((p) => p + 1)}>
                    {t("ai-analysis.pager.older", "older →")}
                  </button>
                  <span className="tiny faint" style={{ marginInlineStart: "auto" }}>
                    {t("ai-analysis.pager.rows", "{n} rows/page · hours_back cap 720 (backend-enforced)", { n: String(historyQ.data?.page_size ?? "") })}
                  </span>
                </div>
              </>
            )}
          </Panel>
        </div>
      )}

      {tab === "indicators" && (
        <Suspense fallback={<TabFallback />}>
          <IndicatorsConsole tf={tf} onTf={setTf} />
        </Suspense>
      )}

      {tab === "shadow" && (
        <div className="aa-stack">
          <Panel
            title={t("ai-analysis.panel.shadow_observer", "Shadow 70D observer (health · disagreements · drift)")}
            right={<FreshnessCaption timestamp={shadowQ.data?.generated_at} isFetching={shadowQ.isFetching} error={shadowQ.isError} />}
            tight
          >
            {shadowQ.isPending ? (
              <Skeleton count={3} />
            ) : shadowQ.isError ? (
              <EmptyState message={shadowQ.error instanceof Error ? shadowQ.error.message : t("ai-analysis.empty.shadow_unavailable", "shadow store unavailable")} />
            ) : (
              <div className="grid cols-2">
                <div>
                  <div className="section-title">{t("ai-analysis.shadow.summary", "summary")}</div>
                  <dl className="kv">
                    {Object.entries(obj(shadowQ.data?.summary))
                      .slice(0, SUMMARY_ROWS)
                      .map(([k, v]) => (
                        <InfoRow key={k} label={k} value={typeof v === "object" ? "…" : String(v)} />
                      ))}
                  </dl>
                  <div className="section-title" style={{ marginTop: 10 }}>
                    {t("ai-analysis.shadow.disagreement_counts", "disagreement counts")}
                  </div>
                  <GateStepper gates={Object.entries(obj(shadowQ.data?.disagreement_counts)).map(([k, v]) => ({ name: k, status: "INFO", reason: String(v) }))} />
                </div>
                <div>
                  <div className="section-title">
                    {t("ai-analysis.shadow.drift_alerts_shown", "drift alerts ({t} recorded, showing first {n})", { t: (shadowQ.data?.drift_alerts ?? []).length, n: Math.min(DRIFT_SHOW, (shadowQ.data?.drift_alerts ?? []).length) })}
                  </div>
                  {(shadowQ.data?.drift_alerts ?? []).length === 0 ? (
                    <EmptyState message={t("ai-analysis.empty.no_drift", "No drift alerts recorded.")} />
                  ) : (
                    <DataTable headers={[{ label: t("ai-analysis.th.feature", "feature") }, { label: t("ai-analysis.th.kind", "kind") }, { label: t("ai-analysis.th.value", "value"), num: true }, { label: t("ai-analysis.th.at", "at") }]}>
                      {(shadowQ.data?.drift_alerts ?? []).slice(0, DRIFT_SHOW).map((a, i) => (
                        <tr key={i}>
                          <td className="tiny">{str(a.feature) ?? str(a.name) ?? "—"}</td>
                          <td className="tiny">{str(a.kind) ?? str(a.alert_type) ?? "—"}</td>
                          <td className="num tiny">{formatNumber(Number(a.value ?? a.score ?? NaN), 3)}</td>
                          <td className="tiny">{formatDateTime(str(a.created_at) ?? str(a.detected_at))}</td>
                        </tr>
                      ))}
                    </DataTable>
                  )}
                </div>
              </div>
            )}
          </Panel>

          {/* v1 deep panel: legacy envelope above + health/disagreements/alerts below. */}
          <Suspense fallback={<TabFallback />}>
            <Shadow70DeepPanel />
          </Suspense>
        </div>
      )}

      {tab === "intel" && (
        <div className="aa-stack">
          <div className="tiny faint">{t("ai-analysis.intel.coverage_note", "behaviour coverage — legacy tab-ai-analysis also loads intelligence centre + position timeline (orphaned components, now wired).")}</div>
          <Suspense fallback={<TabFallback />}>
            <IntelligenceTelemetryPanel />
          </Suspense>
          <Suspense fallback={<TabFallback />}>
            <PositionTimelineLookup />
          </Suspense>
        </div>
      )}

      {openDecision && (
        <Suspense fallback={null}>
          <DecisionDrawer decisionId={openDecision} onClose={closeDecision} />
        </Suspense>
      )}
    </div>
  );
}

/* ────────────── wave 2: memoized history row (100 rows x 15s poll) ──────────────
 * The page re-renders on every latest-signal poll; extracting the row lets
 * React bail out of all but the changed rows instead of re-creating 100 rows
 * (each with a chip, a confidence cell and a closure) every 15 seconds. */
const HistoryRow = memo(function HistoryRow({
  s,
  onInspect,
}: {
  s: SignalDto;
  onInspect: (id: string) => void;
}) {
  const t = useI18n((s) => s.t);
  return (
    <tr>
      <td className="inline-mono tiny">{str(s.request_id)?.slice(0, 10) ?? "—"}</td>
      <td className="small">{s.symbol}</td>
      <td>
        <AaActionChip action={s.action} />
      </td>
      <td className="num">
        <ConfCell value={confidence01(s.confidence)} action={s.action} />
      </td>
      <td className="tiny">{s.decision_stage ?? "—"}</td>
      <td className="tiny muted aa-reason" title={s.reason_code ?? ""}>
        {s.blocked_by ? t("ai-analysis.cell.blocked", "blocked:{b}", { b: s.blocked_by }) : (s.reason_code ?? "—")}
      </td>
      <td className="tiny">{formatDateTime(s.generated_at)}</td>
      <td>
        <button className="btn small ghost" onClick={() => onInspect(String(s.request_id ?? ""))}>
          {t("ai-analysis.cell.drilldown", "drilldown")}
        </button>
      </td>
    </tr>
  );
});
