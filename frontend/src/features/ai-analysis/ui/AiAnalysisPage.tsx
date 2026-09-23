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

import { lazy, Suspense, useCallback, useEffect, useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { DataTable, EmptyState, ErrorState, Panel, ProbBar, Segmented, Skeleton } from "@/components/primitives";
import { ConfidenceGauge } from "@/components/viz";
import type { ShellPageProps } from "@/app/featureModule";
import { ApiError } from "@/types/api";
import { formatDateTime, formatNumber } from "@/lib/format";
import { FreshnessCaption, GateStepper, InfoRow } from "../../research/ui/lane5Kit";
import { actionTone, confidence01, isNotFound, obj, str } from "../model";
import { aiAnalysisQueries, orderHistory } from "../useCases";
import { actionFamily, actionKpi, countRows, timelineSeries, topEntry } from "./vizMath";
import { BarList, ConfidenceTimeline, PriceLadder } from "./aaCharts";
import SignalsTab from "./SignalsTab";
import "./aiAnalysis.css";

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

type Tab = "signals" | "indicators" | "shadow" | "intel";

const TABS: Tab[] = ["signals", "indicators", "shadow", "intel"];
const TAB_PARAM = "tab";

/** Wave 2: explicit caps instead of inline magic numbers, so the caption
 *  ("showing first N") and the slice stay in lockstep. */
const DRIFT_SHOW = 12;
const SUMMARY_ROWS = 14;
/** Wave 2b (#39): every magic number that feeds a caption or a slice lives
 *  here so the two can never drift apart again. */
const REASONS_MAX = 8;

export default function AiAnalysisPage(props: ShellPageProps) {
  void props;
  const queryClient = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();
  const [tab, setTabState] = useState<Tab>(() => {
    const t = searchParams.get(TAB_PARAM);
    return TABS.some((x) => x === t) ? (t as Tab) : "signals";
  });
  const setTab = useCallback(
    (next: Tab) => {
      setTabState(next);
      // Wave 2b (#31): mirror to the URL so the tab survives refresh, is
      // linkable, and Back returns to the section the operator left.
      setSearchParams((prev) => {
        if (next === "signals") prev.delete(TAB_PARAM);
        else prev.set(TAB_PARAM, next);
        return prev;
      });
    },
    [setSearchParams],
  );
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
    // Wave 2b (#3): data is good for a poll cycle — returning to this tab
    // renders from cache instead of firing 3 background requests.
    staleTime: 30_000,
    gcTime: 600_000,
  });
  const statsQ = useQuery({
    queryKey: ["ai-analysis", "decision-stats", hoursBack],
    queryFn: ({ signal }) => aiAnalysisQueries.decisionStats(hoursBack, signal),
    retry: false,
    enabled: tab === "signals",
    placeholderData: (prev) => prev,
    refetchInterval: 60_000,
    staleTime: 30_000,
    gcTime: 600_000,
  });
  const reasonsQ = useQuery({
    queryKey: ["ai-analysis", "no-trade-reasons"],
    queryFn: ({ signal }) => aiAnalysisQueries.noTradeReasons(signal),
    retry: false,
    enabled: tab === "signals",
    refetchInterval: 60_000,
    staleTime: 30_000,
    gcTime: 600_000,
  });
  const shadowQ = useQuery({
    queryKey: ["ai-analysis", "shadow70d"],
    queryFn: ({ signal }) => aiAnalysisQueries.shadow70d(signal),
    retry: false,
    enabled: tab === "shadow",
    // Wave 2b (#16): 60s here, 30s in the deep panel — the envelope refreshes
    // half as often as the v1 panel below it. Kept slower deliberately: the
    // observer strip is a summary, not the live trace.
    refetchInterval: 60_000,
  });

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
  // Wave 2b (#8): KPI derivations hoisted to component level — the old IIFE
  // re-ran actionKpi/topEntry inside the render branch on every 15s poll.
  const kpi = useMemo(() => actionKpi(statsQ.data?.by_action), [statsQ.data]);
  const top = useMemo(() => topEntry(statsQ.data?.by_stage), [statsQ.data]);

  // Wave 2b (#4): warm the chunk + query for the other tabs on idle so the
  // first click paints instead of loading. Signals is already loaded.
  useEffect(() => {
    // Wave 2b (#4): after first paint, warm the five section chunks so a tab
    // click never waits on a code download (the visible cliff). Queries stay
    // intent-driven — hover/focus above or tab enable — no server load
    // without intent. Initial route payload is unchanged (measured); this
    // runs post-idle.
    const warm = () => {
      void import("./IndicatorsConsole");
      void import("./Shadow70DeepPanel");
      void import("./IntelligenceTelemetryPanel");
      void import("./PositionTimelineLookup");
      void import("./DecisionDrawer");
    };
    if (window.requestIdleCallback) {
      const idle = window.requestIdleCallback(warm);
      return () => window.cancelIdleCallback(idle);
    }
    const timer = window.setTimeout(warm, 1200);
    return () => window.clearTimeout(timer);
  }, []);
  const prefetchTab = useCallback((id: Tab) => {
    switch (id) {
      case "indicators":
        void import("./IndicatorsConsole");
        void queryClient.prefetchQuery({
          queryKey: ["ai-analysis", "indicators", "snapshot", tf],
          queryFn: ({ signal }) => aiAnalysisQueries.indicatorsSnapshot(tf, signal),
        });
        break;
      case "shadow":
        void import("./Shadow70DeepPanel");
        void queryClient.prefetchQuery({
          queryKey: ["ai-analysis", "shadow70d"],
          queryFn: ({ signal }) => aiAnalysisQueries.shadow70d(signal),
        });
        break;
      case "intel":
        void import("./IntelligenceTelemetryPanel");
        void import("./PositionTimelineLookup");
        break;
      case "signals":
        break;
    }
  }, [tf]);

  /** Wave 2b (#23/#24): a pending/error query must not fall through to the
   *  chart components' "backend returned no rows" empty text, which would
   *  claim an empty ledger while the header says "loading…". Gate at the
   *  page; the components stay pure presenters. */
  const reasonBody = reasonsQ.isPending ? (
    <Skeleton count={3} />
  ) : reasonsQ.isError ? (
    <ErrorState message={reasonsQ.error instanceof Error ? reasonsQ.error.message : "reasons failed"} onRetry={() => void reasonsQ.refetch()} />
  ) : (
    <BarList rows={reasonRows} tone="var(--amber)" max={REASONS_MAX} />
  );
  const timelineBody = historyQ.isPending ? (
    <Skeleton count={3} />
  ) : historyQ.isError ? (
    <ErrorState message={historyQ.error instanceof Error ? historyQ.error.message : "history failed"} onRetry={() => void historyQ.refetch()} />
  ) : (
    <ConfidenceTimeline series={timeline} />
  );

  return (
    <div>
      <div className="page-head" style={{ display: "flex", alignItems: "baseline", gap: 10, flexWrap: "wrap" }}>
        <h2>AI Analysis</h2>
        <span className="muted small">model signals · decision gates · indicators · shadow 70D · intel</span>
        <FreshnessCaption timestamp={str(latest?.generated_at)} source="audit_signals ledger" isFetching={latestQ.isFetching} error={latestQ.isError && !isNotFound(latestQ.error)} />
      </div>

      <Panel title="Latest signal (live card)" accent tight>
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
            message="No signals recorded yet."
            hint="/api/v1/signals/latest answers RESOURCE_NOT_FOUND — the engine has not emitted a decision."
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
              <button className="btn small primary" disabled={!latest.request_id} onClick={() => inspectDecision(String(latest.request_id))}>
                inspect decision
              </button>
              <span className="tiny muted">gates · evidence · explanation</span>
            </div>

            <div className="aa-gauge">
              <ConfidenceGauge value={confidence01(latest.confidence)} label="confidence" />
            </div>

            <div className="aa-details">
              <div className="aa-chips aa-meta">
                <b className="aa-symbol">{latest.symbol ?? "—"}</b>
                {latest.regime ? <span className="aa-chip aa-fam-unknown">{latest.regime}</span> : <span className="tiny faint">regime not recorded</span>}
                {latest.execution_mode ? <span className="aa-chip aa-fam-unknown">{latest.execution_mode}</span> : null}
                <span className="aa-chip aa-fam-unknown">{latest.decision_stage ?? "stage not recorded"}</span>
              </div>
              <ProbBar
                rows={[
                  { label: "confidence", value: confidence01(latest.confidence), tone: actionTone(latest.action) },
                  { label: "before filters", value: confidence01(latest.confidence_before_filters), tone: "flat" },
                  { label: "after filters", value: confidence01(latest.confidence_after_filters), tone: "flat" },
                ]}
              />
              <dl className="kv" style={{ marginTop: 2 }}>
                <InfoRow label="htf / smc score" value={`${formatNumber(latest.htf_score ?? null, 3)} / ${formatNumber(latest.smc_score ?? null, 3)}`} />
                <InfoRow label="blocked_by" value={latest.blocked_by ?? "—"} />
              </dl>
              <div className="aa-reason-line" title={latest.reason_code ?? ""}>
                reason: {latest.reason_code ?? "—"}
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
            { id: "signals" as const, label: "Decisions & history" },
            { id: "indicators" as const, label: "Indicators wall" },
            { id: "shadow" as const, label: "Shadow 70D" },
            { id: "intel" as const, label: "Intel & timeline" },
          ]}
          value={tab}
          onChange={setTab}
          onPrefetch={(id) => prefetchTab(id as Tab)}
        />
      </div>

      {tab === "signals" && (
        <SignalsTab
          statsQ={statsQ}
          historyQ={historyQ}
          reasonsQ={reasonsQ}
          kpi={kpi}
          top={top}
          stageRows={stageRows}
          historyRows={historyRows}
          hoursBack={hoursBack}
          setHoursBack={setHoursBack}
          page={page}
          setPage={setPage}
          inspectDecision={inspectDecision}
          reasonBody={reasonBody}
          timelineBody={timelineBody}
        />
      )}

      {tab === "indicators" && (
        <Suspense fallback={<TabFallback />}>
          <IndicatorsConsole tf={tf} onTf={setTf} />
        </Suspense>
      )}

      {tab === "shadow" && (
        <div className="aa-stack">
          <Panel
            title="Shadow 70D observer (health · disagreements · drift)"
            right={<FreshnessCaption timestamp={shadowQ.data?.generated_at} isFetching={shadowQ.isFetching} error={shadowQ.isError} />}
            tight
          >
            {shadowQ.isPending ? (
              <Skeleton count={3} />
            ) : shadowQ.isError ? (
              <ErrorState
                message={shadowQ.error instanceof Error ? shadowQ.error.message : "shadow store unavailable"}
                requestId={shadowQ.error instanceof ApiError ? shadowQ.error.requestId : null}
                onRetry={() => void shadowQ.refetch()}
              />
            ) : (
              <div className="grid cols-2">
                <div>
                  <div className="section-title">
                    summary ({Object.keys(obj(shadowQ.data?.summary)).length} keys, showing first {SUMMARY_ROWS})
                  </div>
                  <dl className="kv">
                    {Object.entries(obj(shadowQ.data?.summary))
                      .slice(0, SUMMARY_ROWS)
                      .map(([k, v]) => (
                        <InfoRow key={k} label={k} value={typeof v === "object" ? "…" : String(v)} />
                      ))}
                  </dl>
                  <div className="section-title" style={{ marginTop: 10 }}>
                    disagreement counts
                  </div>
                  <GateStepper gates={Object.entries(obj(shadowQ.data?.disagreement_counts)).map(([k, v]) => ({ name: k, status: "INFO", reason: String(v) }))} />
                </div>
                <div>
                  <div className="section-title">
                    drift alerts ({(shadowQ.data?.drift_alerts ?? []).length} recorded, showing first {Math.min(DRIFT_SHOW, (shadowQ.data?.drift_alerts ?? []).length)})
                  </div>
                  {(shadowQ.data?.drift_alerts ?? []).length === 0 ? (
                    <EmptyState message="No drift alerts recorded." />
                  ) : (
                    <DataTable headers={[{ label: "feature" }, { label: "kind" }, { label: "value", num: true }, { label: "at" }]}>
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
          <div className="tiny faint">behaviour coverage — legacy tab-ai-analysis also loads intelligence centre + position timeline (orphaned components, now wired).</div>
          <Suspense fallback={<TabFallback />}>
            <IntelligenceTelemetryPanel />
          </Suspense>
          <Suspense fallback={<TabFallback />}>
            <PositionTimelineLookup />
          </Suspense>
        </div>
      )}

      {openDecision && (
        <Suspense fallback={<TabFallback />}>
          <DecisionDrawer decisionId={openDecision} onClose={closeDecision} />
        </Suspense>
      )}
    </div>
  );
}
