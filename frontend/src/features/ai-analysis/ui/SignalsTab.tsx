/**
 * SignalsTab — decision stats KPIs + charts + ledger history (wave 2b #42).
 *
 * Extracted from AiAnalysisPage.tsx to keep BOTH files under the repo's
 * 500-line cap. Takes the page's queries and reference-stable derived rows
 * as props, so it re-renders cheaply alongside the 15s latest-signal poll.
 * HistoryRow rides along — only this table renders it.
 */

import { memo, useCallback, useMemo } from "react";
import type { Dispatch, ReactNode, SetStateAction } from "react";
import type { UseQueryResult } from "@tanstack/react-query";
import { EmptyState, ErrorState, MetricCard, Panel, Skeleton } from "@/components/primitives";
import { formatDateTime } from "@/lib/format";
import { ApiError } from "@/types/api";
import { confidence01, str, type SignalDto, type DecisionStatsDto, type NoTradeReasonsDto } from "../model";
import type { V1Page } from "@/types/domain";
import { useI18n } from "@/stores/i18nStore";
import { topEntry, type ActionKpi, type CountRow } from "./vizMath";
import { AaActionChip, ActionDonut, BarList, ConfCell } from "./aaCharts";
import { AaTable, cmpNum, cmpStr, type AaColumn, type AaSort } from "./AaTable";

/** Wave 2b (#39): named here because only this table's BarList uses it —
 *  the caption-lockstep rule applies wherever a number feeds a slice. */
const STAGE_MAX = 20;

/** Epoch ms for ledger timestamps — absent/unparseable → null (cmpNum pins
 *  those last); never coerced to 0. */
function tsNum(v: string | null | undefined): number | null {
  if (!v) return null;
  const ms = Date.parse(v);
  return Number.isFinite(ms) ? ms : null;
}

/** The panel title already claims "newest first", so that IS the order this
 *  table shows before anyone clicks a header (law 1: no surprise reorder). */
const HISTORY_SORT: AaSort = { id: "generated_at", dir: "desc" };


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
  const t = useI18n((s) => s.t);
  /** Header definitions built once per locale: the 15s latest-signal poll
   *  re-renders this tab, so a fresh columns array would defeat AaTable's
   *  memo and re-sort all 100 rows on every pass. */
  const historyColumns = useMemo<AaColumn<SignalDto>[]>(
    () => [
      {
        id: "request_id",
        label: t("ai-analysis.th.decision", "decision"),
        keyOf: (s) => str(s.request_id),
        compare: (a, b) => cmpStr(str(a.request_id), str(b.request_id)),
      },
      {
        id: "symbol",
        label: t("ai-analysis.th.symbol", "symbol"),
        keyOf: (s) => s.symbol,
        compare: (a, b) => cmpStr(a.symbol, b.symbol),
      },
      {
        id: "action",
        label: t("ai-analysis.th.action", "action"),
        keyOf: (s) => s.action,
        compare: (a, b) => cmpStr(a.action, b.action),
      },
      {
        id: "confidence",
        label: t("ai-analysis.th.conf", "conf"),
        num: true,
        keyOf: (s) => confidence01(s.confidence),
        compare: (a, b) => cmpNum(confidence01(a.confidence), confidence01(b.confidence)),
      },
      {
        id: "decision_stage",
        label: t("ai-analysis.th.stage", "stage"),
        keyOf: (s) => s.decision_stage,
        compare: (a, b) => cmpStr(a.decision_stage, b.decision_stage),
      },
      {
        id: "reason_code",
        label: t("ai-analysis.th.reason", "reason"),
        keyOf: (s) => s.blocked_by ?? s.reason_code,
        compare: (a, b) => cmpStr(a.blocked_by ?? a.reason_code, b.blocked_by ?? b.reason_code),
      },
      {
        id: "generated_at",
        label: t("ai-analysis.th.at", "at"),
        num: true,
        keyOf: (s) => s.generated_at,
        compare: (a, b) => cmpNum(tsNum(a.generated_at), tsNum(b.generated_at)),
      },
      { id: "drill", label: t("ai-analysis.th.drill", "drill") },
    ],
    [t],
  );
  /** Stable row identity: the memoized HistoryRow bails out of poll
   *  re-renders instead of rebuilding 100 rows every 15 seconds. */
  const renderRow = useCallback(
    (s: SignalDto, i: number) => (
      <HistoryRow
        key={s.request_id ?? `${s.symbol}-${s.generated_at}-${i}`}
        s={s}
        onInspect={inspectDecision}
      />
    ),
    [inspectDecision],
  );
  return (
    <div className="aa-stack">
      <div className="aa-segbar">
        <span className="section-title aa-segbar-title">
          {t("ai-analysis.stats.heading", "decision stats")}
        </span>
        <span className="tiny faint">{t("ai-analysis.stats.window", "window")}</span>
        <select
          aria-label={t("ai-analysis.stats.window_aria", "Stats window (hours)")}
          className="select aa-window-select"
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
        <EmptyState message={statsQ.error instanceof ApiError ? statsQ.error.localized(t) : t("ai-analysis.empty.stats_unavailable", "stats unavailable")} />
      ) : (
        (() => {
          const st = statsQ.data;
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
                <Panel title={t("ai-analysis.panel.no_trade_reasons", "NO_TRADE reasons")} right={<span className="tiny faint">{reasonsQ.isPending ? t("ai-analysis.panel.loading", "loading…") : reasonsQ.isError ? t("ai-analysis.reasons.unavailable", "unavailable") : t("ai-analysis.reasons.in_ledger", "{n} all-time in ledger", { n: (reasonsQ.data?.total ?? 0).toLocaleString() })}</span>} tight>
                  <div className="panel-body">{reasonBody}</div>
                </Panel>
              </div>

              <div className="grid cols-2">
                <Panel title={t("ai-analysis.panel.stage_distribution", "Decision stage distribution")} right={<span className="tiny faint">{t("ai-analysis.panel.stage_sort_note", "sorted by count — backend returns no stage order")}</span>} tight>
                  <div className="panel-body">
                    <BarList rows={stageRows} tone="var(--violet)" max={STAGE_MAX} />
                  </div>
                </Panel>
                <Panel
                  title={t("ai-analysis.panel.conf_over_time", "Confidence over time")}
                                    right={<span className="tiny faint">{historyQ.isPending ? t("ai-analysis.panel.loading", "loading…") : t("ai-analysis.panel.history_page_rows", "current history page · {n} rows max", { n: historyQ.data?.page_size ?? "—" })}</span>}
                  tight
                >
                  <div className="panel-body">{timelineBody}</div>
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
          <ErrorState message={historyQ.error instanceof ApiError ? historyQ.error.localized(t) : t("ai-analysis.empty.history_failed", "history failed")} onRetry={() => void historyQ.refetch()} />
        ) : (historyQ.data?.items ?? []).length === 0 ? (
          <EmptyState message={t("ai-analysis.empty.window", "No signals in this window.")} />
        ) : (
          <>
            <AaTable
              rows={historyRows}
              columns={historyColumns}
              defaultSort={HISTORY_SORT}
              renderRow={renderRow}
            />
            <div className="aa-pager">
              <button className="btn small" disabled={page <= 1} onClick={() => setPage((p) => Math.max(1, p - 1))}>
                {t("ai-analysis.pager.newer", "← newer")}
                              </button>
              <span className="tiny muted" aria-live="polite">
                {t("ai-analysis.pager.page", "page {p}", { p: page })}
                                {historyQ.isFetching && ` · ${t("ai-analysis.panel.loading", "loading…")}`}
              </span>
              <button className="btn small" disabled={!historyQ.data?.has_more} onClick={() => setPage((p) => p + 1)}>
                {t("ai-analysis.pager.older", "older →")}
                              </button>
              <span className="tiny faint aa-pager-more">
                {t("ai-analysis.pager.rows", "{n} rows/page · hours_back cap 720 (backend-enforced)", { n: historyQ.data?.page_size ?? "—" })}
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
        <button className="btn small ghost" disabled={!s.request_id} onClick={() => onInspect(String(s.request_id))}>
          {t("ai-analysis.cell.drilldown", "drilldown")}
        </button>
      </td>
    </tr>
  );
});
