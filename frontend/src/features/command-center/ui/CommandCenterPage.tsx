/**
 * Command Center — strategy fleet command view (legacy command_center_* parity).
 *
 * Sections: overview KPI strip · ANALYSIS tab (drop-off funnel, gate outcome
 * bars, lifecycle donut, eligibility census, confidence/health/evidence-ring
 * histograms — BUG-312 analysis wave) · spatial 2.5D fleet map (Canvas2D port
 * of Web/command_center_spatial.js) · fleet grid (risk-first) · inspector
 * drawer (extracted to ./InspectorDrawer) · time machine (extracted to
 * ./TimeMachinePanel).
 *
 * Data-delivery contract ("data must send for sure"): every query retries
 * retryable transport failures (timeout/network/5xx — ApiError.retryable)
 * with exponential backoff via ccRetry, errors surface as a visible
 * ErrorState with a Retry button, and the backend spatial route no longer
 * hangs (BUG-312 fix).
 */

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import type { ShellPageProps } from "@/app/featureModule";
import { DataTable, EmptyState, ErrorState, MetricCard, Panel, Segmented, Skeleton, StatusBadge } from "@/components/primitives";
import { formatDateTime, formatNumber } from "@/lib/format";
import { useI18n } from "@/stores/i18nStore";
import { FreshnessCaption, StatusPill, useNow } from "../../research/ui/lane5Kit";
import { obj, stuckRows, type CcFleetRowDto } from "../model";
import { ccRetry, ccRetryDelay, commandCenterQueries, commandCenterUseCases } from "../useCases";
import { AnalysisSection } from "./AnalysisSection";
import { InspectorDrawer } from "./InspectorDrawer";
import { SpatialFleetCanvas } from "./SpatialFleetCanvas";
import { TimeMachine } from "./TimeMachinePanel";

type View = "analysis" | "spatial" | "fleet" | "timemachine";

export default function CommandCenterPage(props: ShellPageProps) {
  void props;
  const t = useI18n((s) => s.t);
  const nowMs = useNow(5000);
  const [view, setView] = useState<View>("analysis");
  const [lifecycle, setLifecycle] = useState("");
  const [executionFilter, setExecutionFilter] = useState("");
  const [inspectId, setInspectId] = useState<string | null>(null);

  const overviewQ = useQuery({
    queryKey: ["command-center", "overview"],
    queryFn: ({ signal }) => commandCenterQueries.overview(signal),
    refetchInterval: 30_000,
    retry: ccRetry,
    retryDelay: ccRetryDelay,
  });
  const fleetQ = useQuery({
    queryKey: ["command-center", "fleet", lifecycle, executionFilter],
    queryFn: ({ signal }) => commandCenterQueries.fleet(lifecycle || undefined, executionFilter || undefined, signal),
    refetchInterval: 60_000,
    retry: ccRetry,
    retryDelay: ccRetryDelay,
  });

  const overview = overviewQ.data?.available === true ? overviewQ.data : null;
  // perf: risk-first sort + stuck census derived only when their inputs change
  // (deps: fleetRows / nowMs / overview — every reactive value read).
  const fleetRows = fleetQ.data?.rows;
  const rows = useMemo(() => commandCenterUseCases.fleetByRisk(fleetRows ?? [], nowMs), [fleetRows, nowMs]);
  const stuck = useMemo(() => stuckRows(overview ?? undefined), [overview]);
  const overviewDead = !overview && overviewQ.isError && !overviewQ.isPending;

  return (
    <div>
      <div className="page-head" style={{ display: "flex", alignItems: "baseline", gap: 10, flexWrap: "wrap" }}>
        <h2>{t("nav.feature.command-center", "Command Center")}</h2>
        <span className="muted small">{t("command-center.page.subtitle", "analysis · spatial map · fleet grid · inspector · time machine")}</span>
        <FreshnessCaption
          timestamp={null}
          source="research engine projection"
          isFetching={overviewQ.isFetching || fleetQ.isFetching}
          error={overviewQ.isError && fleetQ.isError}
        />
      </div>

      {overviewDead && (
        <ErrorState
          message={t("command-center.err.overview_dead", "Command Center data unavailable — overview failed after retries ({e})", {
            e: overviewQ.error instanceof Error ? overviewQ.error.message : t("command-center.err.unknown", "unknown error"),
          })}
          onRetry={() => void overviewQ.refetch()}
        />
      )}

      {/* ---- overview KPI strip ---- */}
      <div className="grid cols-4">
        <MetricCard
          label={t("command-center.kpi.strategies", "strategies")}
          value={overviewQ.isPending ? "…" : String(overview?.total_strategies ?? "—")}
          tone="dim"
          sub={t("command-center.kpi.running_evaluations", "running evaluations {n}", { n: String(overview?.running_evaluations ?? 0) })}
        />
        <MetricCard
          label={t("command-center.kpi.execution_eligible", "execution eligible")}
          value={String(overview?.execution_eligible_count ?? "—")}
          tone={overview?.execution_eligible_count ? "pos" : "dim"}
          sub={t("command-center.kpi.eligibility_yes", "eligibility YES (domain authority)")}
        />
        <MetricCard label={t("command-center.kpi.blocked", "blocked")} value={String(overview?.blocked_count ?? "—")} tone={overview?.blocked_count ? "neg" : "dim"} sub={t("command-center.kpi.eligibility_blocked", "eligibility BLOCKED")} />
        <MetricCard
          label={t("command-center.kpi.terminal_states", "terminal states")}
          value={`${String(obj(overview?.terminal).REJECTED ?? 0)}R / ${String(obj(overview?.terminal).DEGRADED ?? 0)}D`}
          tone="dim"
          sub={t("command-center.kpi.retired_excluded", "retired excluded from pipeline census")}
        />
      </div>

      <div style={{ height: 12 }} />

      {stuck.length > 0 && (
        <Panel title={t("command-center.panel.stuck", "Stuck strategies (hours in non-terminal state)")} tight>
          <DataTable headers={[{ label: t("command-center.th.strategy", "strategy") }, { label: t("command-center.th.state", "state") }, { label: t("command-center.th.hours", "hours"), num: true }, { label: "" }]}>
            {stuck.map((s) => (
              <tr key={s.strategy_id}>
                <td className="inline-mono tiny">{s.strategy_id.slice(0, 16)}</td>
                <td>
                  <StatusPill status={s.state} />
                </td>
                <td className="num tiny">{s.hours === null ? "—" : formatNumber(s.hours, 1)}</td>
                <td>
                  <button className="btn small ghost" onClick={() => setInspectId(s.strategy_id)}>
                    {t("command-center.action.inspect", "inspect")}
                  </button>
                </td>
              </tr>
            ))}
          </DataTable>
        </Panel>
      )}

      <div style={{ marginBlock: 12 }}>
        <Segmented
          options={[
            { id: "analysis" as const, label: t("command-center.view.analysis", "Analysis") },
            { id: "spatial" as const, label: t("command-center.view.spatial", "Spatial 2.5D map") },
            { id: "fleet" as const, label: t("command-center.view.fleet", "Fleet grid") },
            { id: "timemachine" as const, label: t("command-center.view.time_machine", "Time machine") },
          ]}
          value={view}
          onChange={setView}
        />
      </div>

      {view === "analysis" && (
        <AnalysisSection
          overview={overview}
          overviewQ={overviewQ}
          fleet={fleetQ.data?.available === true ? fleetQ.data : undefined}
          fleetQ={fleetQ}
        />
      )}

      {view === "spatial" && (
        <Panel title={t("command-center.panel.spatial", "Spatial fleet map — lifecycle strata (Canvas2D 2.5D)")} tight>
          <div style={{ padding: 10 }}>
            <SpatialFleetCanvas selectedId={inspectId} onSelect={(id) => setInspectId(id)} onInspect={(id) => setInspectId(id)} />
          </div>
        </Panel>
      )}

      {view === "fleet" && (
        <Panel
          title={t("command-center.panel.fleet", "Fleet ({rows} rows, risk-first order)", { rows: String(fleetQ.data?.count ?? 0) })}
          right={
            <div style={{ display: "flex", gap: 6 }}>
              <select aria-label={t("command-center.a11y.lifecycle_filter", "Lifecycle filter")} className="select" style={{ width: 150 }} value={lifecycle} onChange={(e) => setLifecycle(e.target.value)}>
                <option value="">{t("command-center.filter.lifecycle_any", "lifecycle: any")}</option>
                {["DISCOVERED", "VALIDATED", "SHADOW", "ACTIVE", "REJECTED", "DEGRADED", "RETIRED"].map((l) => (
                  <option key={l} value={l}>
                    {l}
                  </option>
                ))}
              </select>
              <select
                aria-label={t("command-center.a11y.eligibility_filter", "Eligibility filter")}
                className="select"
                style={{ width: 140 }}
                value={executionFilter}
                onChange={(e) => setExecutionFilter(e.target.value)}
              >
                <option value="">{t("command-center.filter.eligibility_any", "eligibility: any")}</option>
                {["YES", "BLOCKED", "CONDITIONAL", "UNKNOWN"].map((l) => (
                  <option key={l} value={l}>
                    {l}
                  </option>
                ))}
              </select>
            </div>
          }
          tight
        >
          {fleetQ.isPending ? (
            <Skeleton count={6} />
          ) : fleetQ.isError ? (
            <ErrorState message={fleetQ.error instanceof Error ? fleetQ.error.message : t("command-center.err.fleet", "fleet failed")} onRetry={() => void fleetQ.refetch()} />
          ) : fleetQ.data?.available === false ? (
            <EmptyState message={t("command-center.empty.research_unavailable", "Research engine unavailable")} hint={fleetQ.data.reason ?? t("command-center.empty.spatial_hint", "RESEARCH_ENGINE_UNAVAILABLE")} />
          ) : rows.length === 0 ? (
            <EmptyState message={t("command-center.empty.no_strategies", "No strategies match the filters.")} />
          ) : (
            <div className="table-wrap" style={{ maxHeight: 560 }}>
              <table className="data-table">
                <thead>
                  <tr>
                    <th scope="col">{t("command-center.th.strategy", "strategy")}</th>
                    <th scope="col">{t("command-center.th.lifecycle", "lifecycle")}</th>
                    <th scope="col" className="num">{t("command-center.th.conf", "conf")}</th>
                    <th scope="col" className="num">{t("command-center.th.samples", "samples")}</th>
                    <th scope="col" className="num">{t("command-center.th.health", "health")}</th>
                    <th scope="col">{t("command-center.th.eligibility", "eligibility")}</th>
                    <th scope="col">{t("command-center.th.reason", "reason")}</th>
                    <th scope="col">{t("command-center.th.updated", "updated")}</th>
                    <th scope="col" />
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r: CcFleetRowDto, i) => (
                    <tr key={`${r.strategy_id}-${i}`}>
                      <td className="inline-mono tiny" title={r.strategy_id}>
                        {(r.strategy_id ?? "—").slice(0, 14)}
                      </td>
                      <td>
                        <StatusPill status={r.lifecycle} />
                      </td>
                      <td className="num tiny">{r.confidence === null || r.confidence === undefined ? "—" : formatNumber(r.confidence, 3)}</td>
                      <td className="num tiny">{r.sample_count ?? "—"}</td>
                      <td className="num tiny">{r.health_final === null || r.health_final === undefined ? "—" : formatNumber(r.health_final, 1)}</td>
                      <td>
                        <StatusBadge status={r.eligibility_state} />
                      </td>
                      <td className="tiny muted" title={r.eligibility_reason ?? ""}>
                        {(r.eligibility_reason ?? "—").slice(0, 32)}
                      </td>
                      <td className="tiny">{r.updated_at ? formatDateTime(r.updated_at) : "—"}</td>
                      <td>
                        <button className="btn small ghost" onClick={() => setInspectId(String(r.strategy_id ?? ""))}>
                          {t("command-center.action.inspector", "inspector")}
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Panel>
      )}

      {view === "timemachine" && <TimeMachine />}

      {view === "analysis" && !overview && overviewQ.isPending && <Skeleton count={4} />}
      {view === "analysis" && overviewQ.isPending && overviewQ.isFetching && !overview && <div className="tiny muted">{t("command-center.analysis.loading", "loading analysis data…")}</div>}

      {inspectId && <InspectorDrawer strategyId={inspectId} onClose={() => setInspectId(null)} />}
    </div>
  );
}

