/**
 * SignalsTab — decision stats KPIs + charts + ledger history (wave 2b #42).
 *
 * Extracted from AiAnalysisPage.tsx to keep BOTH files under the repo's
 * 500-line cap. Takes the page's queries and reference-stable derived rows
 * as props, so it re-renders cheaply alongside the 15s latest-signal poll.
 * HistoryRow rides along — only this table renders it.
 */

import { memo } from "react";
import type { Dispatch, ReactNode, SetStateAction } from "react";
import type { UseQueryResult } from "@tanstack/react-query";
import { DataTable, EmptyState, ErrorState, MetricCard, Panel, Skeleton } from "@/components/primitives";
import { formatDateTime } from "@/lib/format";
import { confidence01, str, type SignalDto, type DecisionStatsDto, type NoTradeReasonsDto } from "../model";
import type { V1Page } from "@/types/domain";
import { topEntry, type ActionKpi, type CountRow } from "./vizMath";
import { AaActionChip, ActionDonut, BarList, ConfCell } from "./aaCharts";

/** Wave 2b (#39): named here because only this table's BarList uses it —
 *  the caption-lockstep rule applies wherever a number feeds a slice. */
const STAGE_MAX = 20;


/* ────────────── wave 2b (#42): signals tab extracted ──────────────
 * The page had grown past the repo's 500-line cap; the stats+history block
 * is a cohesive view that takes its queries and derived rows as props
 * (reference-stable from the parent's useMemos) so it re-renders cheaply and
 * the page file stays under the cap. */
export default function SignalsTab({
  statsQ,
  historyQ,
  reasonsQ,
  kpi,
  top,
  stageRows,
  historyRows,
  hoursBack,
  setHoursBack,
  page,
  setPage,
  inspectDecision,
  reasonBody,
  timelineBody,
}: {
  statsQ: UseQueryResult<DecisionStatsDto>;
  historyQ: UseQueryResult<V1Page<SignalDto>>;
  reasonsQ: UseQueryResult<NoTradeReasonsDto>;
  kpi: ActionKpi;
  top: ReturnType<typeof topEntry>;
  stageRows: CountRow[];
  historyRows: SignalDto[];
  hoursBack: number;
  setHoursBack: (h: number) => void;
  page: number;
  setPage: Dispatch<SetStateAction<number>>;
  inspectDecision: (id: string) => void;
  reasonBody: ReactNode;
  timelineBody: ReactNode;
}) {
  return (
    <div className="aa-stack">
      <div className="aa-segbar">
        <span className="section-title" style={{ margin: 0 }}>
          decision stats
        </span>
        <span className="tiny faint">window</span>
        <select
          aria-label="Stats window (hours)"
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
        <EmptyState message={statsQ.error instanceof Error ? statsQ.error.message : "stats unavailable"} />
      ) : (
        (() => {
          const st = statsQ.data;
          const pct = (n: number) => (kpi.total > 0 ? `${((n / kpi.total) * 100).toFixed(1)}%` : "—");
          return (
            <>
              <div className="grid cols-4">
                <MetricCard label="decisions in window" value={kpi.total.toLocaleString()} sub={`/decisions/stats · ${st?.window_hours ?? hoursBack}h scanned`} />
                <MetricCard label="trade actions" value={kpi.trade.toLocaleString()} sub={`${pct(kpi.trade)} of decisions · non-NO_TRADE`} />
                <MetricCard label="no-trade" value={kpi.noTrade.toLocaleString()} sub={`${pct(kpi.noTrade)} of decisions · filter blocks`} />
                <MetricCard
                  label="top stage"
                  value={<span style={{ fontSize: 13, fontWeight: 700 }}>{top?.label ?? "—"}</span>}
                  sub={top ? `${top.count.toLocaleString()} · ${pct(top.count)}` : "no stage rows"}
                />
              </div>

              <div className="grid cols-2">
                <Panel title="Decision mix (by action)" tight>
                  <div className="panel-body">
                    <ActionDonut byAction={st?.by_action} />
                  </div>
                </Panel>
                <Panel title="NO_TRADE reasons" right={<span className="tiny faint">{reasonsQ.isPending ? "loading…" : reasonsQ.isError ? "unavailable" : `${(reasonsQ.data?.total ?? 0).toLocaleString()} all-time in ledger`}</span>} tight>
                  <div className="panel-body">{reasonBody}</div>
                </Panel>
              </div>

              <div className="grid cols-2">
                <Panel title="Decision stage distribution" right={<span className="tiny faint">sorted by count — backend returns no stage order</span>} tight>
                  <div className="panel-body">
                    <BarList rows={stageRows} tone="var(--violet)" max={STAGE_MAX} />
                  </div>
                </Panel>
                <Panel
                  title="Confidence over time"
                  right={<span className="tiny faint">{historyQ.isPending ? "loading…" : `current history page · ${historyQ.data?.page_size ?? "—"} rows max`}</span>}
                  tight
                >
                  <div className="panel-body">{timelineBody}</div>
                </Panel>
              </div>
            </>
          );
        })()
      )}

      <Panel title="Signal history (ledger, newest first)" tight>
        {historyQ.isPending ? (
          <Skeleton count={5} />
        ) : historyQ.isError ? (
          <ErrorState message={historyQ.error instanceof Error ? historyQ.error.message : "history failed"} onRetry={() => void historyQ.refetch()} />
        ) : (historyQ.data?.items ?? []).length === 0 ? (
          <EmptyState message="No signals in this window." />
        ) : (
          <>
            <DataTable
              headers={[
                { label: "decision" },
                { label: "symbol" },
                { label: "action" },
                { label: "conf", num: true },
                { label: "stage" },
                { label: "reason" },
                { label: "at" },
                { label: "drill" },
              ]}
            >
              {historyRows.map((s: SignalDto, i: number) => (
                <HistoryRow key={s.request_id ?? `${s.symbol}-${s.generated_at}-${i}`} s={s} onInspect={inspectDecision} />
              ))}
            </DataTable>
            <div style={{ display: "flex", gap: 8, marginTop: 8, alignItems: "center" }}>
              <button className="btn small" disabled={page <= 1} onClick={() => setPage((p) => Math.max(1, p - 1))}>
                ← newer
              </button>
              <span className="tiny muted" aria-live="polite">
                page {page}
                {historyQ.isFetching && " · loading…"}
              </span>
              <button className="btn small" disabled={!historyQ.data?.has_more} onClick={() => setPage((p) => p + 1)}>
                older →
              </button>
              <span className="tiny faint" style={{ marginInlineStart: "auto" }}>
                {historyQ.data?.page_size} rows/page · hours_back cap 720 (backend-enforced)
              </span>
            </div>
          </>
        )}
      </Panel>
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
        {s.blocked_by ? `blocked:${s.blocked_by}` : (s.reason_code ?? "—")}
      </td>
      <td className="tiny">{formatDateTime(s.generated_at)}</td>
      <td>
        <button className="btn small ghost" disabled={!s.request_id} onClick={() => onInspect(String(s.request_id))}>
          drilldown
        </button>
      </td>
    </tr>
  );
});
