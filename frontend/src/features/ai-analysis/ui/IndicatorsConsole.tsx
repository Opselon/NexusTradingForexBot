/**
 * IndicatorsConsole — TradingView-grade technicals console (lane I, wave 2).
 *
 * Port of the legacy `Web/tv_widget.js` widget (TV-REDESIGN-1): market context
 * strip → TF toolbar → three semicircle cycle gauges → distribution signal bar →
 * oscillators + moving-average tables → pivot matrix. Reference only for
 * visuals; data has ONE source: GET /api/v1/indicators (full snapshot) via
 * `@/api/indicatorsApi` through useCases — nothing is computed client-side.
 * Segment widths and needle geometry are display transforms of backend counts;
 * the needle uses backend `gauge.angle_deg`, falling back to the backend's own
 * Section-D rating buckets only when that field is absent.
 *
 * Honesty rules (UI_WAVE2_BRIEF invariants 1-3):
 *  - LIVE/STALE/ERROR derive from real react-query state only; on error the
 *    last successful snapshot stays on screen (STALE) — never synthetic data.
 *  - null values render "—"; empty tables render an honest empty state.
 *  - Timeframes are the curated backend-valid list; 1d/1w/1h aliases are NOT
 *    offered (audit D3: the backend 500s on them until T-09.1).
 *
 * D5 fix: `pivots.rows` is consumed as the dict-of-levels the backend actually
 * sends (see types/features.ts), so the old `rows.slice()` crash path is gone.
 */

import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";
import { useI18n } from "@/stores/i18nStore";
import { ApiError } from "@/types/api";
import { formatPrice, formatTime } from "@/lib/format";
import type {
  GaugeVerdict,
  IndicatorGauge,
  IndicatorPivots,
  IndicatorReading,
  IndicatorsSnapshot,
} from "../model";
import { aiAnalysisQueries } from "../useCases";
import { IndicatorCycle } from "./IndicatorCycle";
import {
  CountChip,
  VerdictPill,
  actionClasses,
  actionGlyph,
  fmtIndicatorValue,
  fmtPivotValue,
  verdictOfSafe,
} from "./indicatorKit";
import "./indicators.css";

/** Curated, backend-valid timeframes (legacy `#tv-timeframes` buttons). */
const TFS: Array<{ id: string; label: string }> = [
  { id: "M1", label: "1m" },
  { id: "M5", label: "5m" },
  { id: "M15", label: "15m" },
  { id: "M30", label: "30m" },
  { id: "H1", label: "1h" },
  { id: "H2", label: "2h" },
  { id: "H4", label: "4h" },
  { id: "D1", label: "1D" },
  { id: "W1", label: "1W" },
  { id: "MN1", label: "1M" },
];

/** Poll cadences (legacy POLL_OK_MS/POLL_ERR_MS): 30s healthy, 10s degraded. */
const POLL_OK_MS = 30_000;
const POLL_ERR_MS = 10_000;

type FeedStatus = "loading" | "live" | "stale" | "error";

export default function IndicatorsConsole({ tf, onTf }: { tf: string; onTf: (tf: string) => void }) {
  const t = useI18n((s) => s.t);
  const snapshotQ = useQuery({
    queryKey: ["ai-analysis", "indicators", "snapshot", tf],
    queryFn: ({ signal }) => aiAnalysisQueries.indicatorsSnapshot(tf, signal),
    retry: false,
    refetchInterval: (query) => (query.state.status === "error" ? POLL_ERR_MS : POLL_OK_MS),
  });

  const data: IndicatorsSnapshot | undefined = snapshotQ.data;
  // Feed state comes from REAL query state only: an error while cached data
  // exists is STALE (lastGood stays on screen), otherwise ERROR.
  const status: FeedStatus =
    snapshotQ.status === "error" ? (data ? "stale" : "error") : data ? "live" : "loading";
  const errorMessage = status === "stale" || status === "error" ? explainError(snapshotQ.error, t) : null;

  const gauges = data?.gauges ?? {};
  const osc = gauges.oscillators;
  const sum = gauges.summary;
  const ma = gauges.moving_averages;
  // Distribution counts: backend summary {Sell,Neutral,Buy}; the summary gauge
  // carries the same aggregate, so it is the verbatim fallback.
  // NOTE (lane E): the brief said line 53 carries an object-literal deps quirk
  // ([data?.table]) — verified against base: that line is the TFS entry
  // { id: "H1" }, the file had ZERO useMemo at base, and IndicatorsSnapshot
  // (types/features.ts:58) has NO `table` field, so such a dep cannot compile.
  // Deps below are therefore the exact values each derivation reads — same
  // objects/values, same fallback order, recomputed only on a payload change.
  const summaryCounts = data?.summary;
  const dist: Pick<IndicatorGauge, "sell" | "neutral" | "buy"> | null = useMemo(
    () =>
      summaryCounts
        ? { sell: summaryCounts.Sell ?? 0, neutral: summaryCounts.Neutral ?? 0, buy: summaryCounts.Buy ?? 0 }
        : sum
          ? { sell: sum.sell, neutral: sum.neutral, buy: sum.buy }
          : null,
    [summaryCounts, sum],
  );
  // Vote totals are arithmetic over the backend counts only.
  const sumTotal = useMemo(() => (sum ? sum.sell + sum.neutral + sum.buy : 0), [sum]);

  return (
    <div className="ic-console">
      {/* 1) MARKET CONTEXT STRIP — every value from the real snapshot */}
      <section className="ic-card ic-context" aria-label={t("ai-analysis.ind.context_aria", "Market context")}>
        <div className="ic-idbox">
          <span className="ic-flag" aria-hidden="true">
            ◆
          </span>
          <span className="ic-symbol">{data?.symbol || "—"}</span>
          <span className="ic-tfchip" title={t("ai-analysis.ind.tfchip_title", "Active timeframe")}>
            {data?.timeframe || tf}
          </span>
          <span className="ic-bars">
            {data ? t("ai-analysis.ind.bars", "{a} source bars · {b} in window", { a: data.source_bar_count ?? data.bar_count, b: data.bar_count }) : t("ai-analysis.ind.awaiting", "awaiting snapshot…")}
          </span>
        </div>
        <div className="ic-pricewrap">
          <span className="ic-plabel">{t("ai-analysis.ind.last_close", "Last close")}</span>
          <span className="ic-price">{formatPrice(data?.last_close ?? null)}</span>
          <span className="ic-updated" title={t("ai-analysis.ind.updated_hint", "When this browser last received a successful snapshot")}>
                      {status === "loading" ? t("ai-analysis.ind.updated_none", "updated —") : t("ai-analysis.ind.updated", "updated {time}", { time: formatTime(snapshotQ.dataUpdatedAt || null) })}
                      {snapshotQ.isFetching ? ` · ${t("ai-analysis.ind.refreshing", "refreshing…")}` : ""}
                    </span>
        </div>
        <span className={`ic-live ic-live-${status}`} role="status" aria-live="polite">
          <i className="ic-live-dot" aria-hidden="true" />
          {status === "live" ? t("ai-analysis.ind.status_live", "LIVE") : status === "stale" ? t("ai-analysis.ind.status_stale", "STALE") : status === "error" ? t("ai-analysis.ind.status_error", "ERROR") : t("ai-analysis.ind.status_waiting", "WAITING")}
        </span>
      </section>

      {/* real-state banner — stale keeps lastGood visible behind it (widget pattern) */}
      {(status === "stale" || status === "error") && (
        <div className={`ic-banner ${status === "stale" ? "is-stale" : "is-error"}`} role="alert">
          <span className="ic-banner-ico" aria-hidden="true">
            ●
          </span>
          <span className="ic-banner-text">
            {status === "stale"
                          ? t("ai-analysis.ind.stale_banner", "Live feed interrupted — showing values from {time} · {err}", { time: formatTime(snapshotQ.dataUpdatedAt || null), err: errorMessage ?? "" })
                          : errorMessage}
          </span>
          <button type="button" className="ic-retry" onClick={() => void snapshotQ.refetch()} disabled={snapshotQ.isFetching}>
            {snapshotQ.isFetching ? t("ai-analysis.ind.retrying", "retrying…") : t("ai-analysis.ind.retry", "Retry")}
          </button>
        </div>
      )}

      {/* 2) TIMEFRAME TOOLBAR — curated list, backend-invalid aliases never offered */}
      <div className="ic-tfbar" role="group" aria-label={t("ai-analysis.ind.tf_aria", "Timeframe selection")}>
        {TFS.map((t) => (
          <button
            key={t.id}
            type="button"
            className={`ic-tf ${tf === t.id ? "active" : ""}`}
            aria-pressed={tf === t.id}
            onClick={() => onTf(t.id)}
          >
            {t.label}
          </button>
        ))}
      </div>

      {status === "loading" ? (
        <div className="ic-loading" aria-label={t("ai-analysis.ind.loading_aria", "Loading indicator snapshot")}>
          <div className="skeleton" style={{ height: 96 }} />
          <div className="grid cols-3">
            <div className="skeleton" style={{ height: 190 }} />
            <div className="skeleton" style={{ height: 190 }} />
            <div className="skeleton" style={{ height: 190 }} />
          </div>
          <div className="skeleton" style={{ height: 220 }} />
        </div>
      ) : (
        <>
          {/* 3) THREE CYCLE GAUGES (Oscillators · Summary hero · Moving Averages) */}
          <div className="ic-cycles" role="group" aria-label={t("ai-analysis.ind.gauges_aria", "Technical gauges")}>
                      <GaugeCard title={t("ai-analysis.ind.oscillators", "Oscillators")} gauge={osc} meta={osc ? t("ai-analysis.ind.osc_meta", "{n} oscillators", { n: osc.sell + osc.neutral + osc.buy }) : null} />
                      <GaugeCard title={t("ai-analysis.ind.summary", "Summary")} gauge={sum} hero meta={sum ? t("ai-analysis.ind.summary_meta", "{n} signals · {tf}", { n: sumTotal, tf: data?.timeframe || tf }) : null} />
                      <GaugeCard title={t("ai-analysis.ind.moving_averages", "Moving Averages")} gauge={ma} meta={ma ? t("ai-analysis.ind.ma_meta", "{n} averages", { n: ma.sell + ma.neutral + ma.buy }) : null} />
                    </div>

          {/* 4) DISTRIBUTION SIGNAL BAR — widths ARE the backend counts */}
          <section className="ic-card ic-signalbar" aria-label={t("ai-analysis.ind.signalbar_aria", "Overall technical summary distribution")}>
            <div className="ic-signal-head">
              <span className="ic-signal-name">{t("ai-analysis.ind.signal_name", "Overall Technical Summary")}</span>
              <VerdictPill label={sum?.label ?? null} />
              <span className="ic-signal-meta">{sum ? t("ai-analysis.ind.votes", "{n} votes counted by the backend", { n: sumTotal }) : t("ai-analysis.ind.no_summary", "no summary gauge returned")}</span>
            </div>
            <DistributionBar counts={dist} />
            <div className="ic-counts">
              <CountChip kind="sell" value={dist?.sell ?? null} />
              <CountChip kind="neutral" value={dist?.neutral ?? null} />
              <CountChip kind="buy" value={dist?.buy ?? null} />
            </div>
          </section>

          {/* 5) INDICATOR TABLES — rows rendered verbatim {name,value,action} */}
          <div className="ic-tables">
            <ReadingTable
              title={t("ai-analysis.ind.oscillators", "Oscillators")}
              subtitle={t("ai-analysis.ind.osc_subtitle", "momentum & strength")}
              verdict={osc?.label ?? null}
              rows={data?.oscillators}
              show={status !== "error"}
              empty={t("ai-analysis.ind.table_osc_empty", "No oscillator data for this timeframe yet — waiting for completed bars.")}
            />
            <ReadingTable
              title={t("ai-analysis.ind.moving_averages", "Moving Averages")}
              subtitle={t("ai-analysis.ind.ma_subtitle", "trend & crossover")}
              verdict={ma?.label ?? null}
              rows={data?.moving_averages}
              show={status !== "error"}
              empty={t("ai-analysis.ind.table_ma_empty", "No moving-average data for this timeframe yet — waiting for completed bars.")}
            />
          </div>

          {/* 6) PIVOT MATRIX — levels × family dict as the backend sends (D5) */}
          <PivotMatrixPanel pivots={data?.pivots} show={status !== "error"} />
        </>
      )}
    </div>
  );
}

/* ─────────────────────────── error translation ─────────────────────────── */

/** Backend error codes -> honest captions (legacy tv_widget.js mapping). */
function explainError(e: unknown, t: (k: string, f: string, v?: Record<string, string | number>) => string): string {
  if (e instanceof ApiError) {
    if (e.code === "ENGINE_UNAVAILABLE") return t("ai-analysis.ind.err_engine", "Waiting for engine — indicators need live completed bars.");
    if (e.code === "RESOURCE_UNAVAILABLE") return t("ai-analysis.ind.err_bars", "No bar history yet…");
    if (e.code === "TIMEOUT") return t("ai-analysis.ind.err_timeout", "Indicator compute timed out (retrying on schedule)…");
    return t("ai-analysis.ind.err_unavailable_code", "Indicator feed unavailable ({c})", { c: e.code });
  }
  return t("ai-analysis.ind.err_unavailable", "Indicator feed unavailable");
}

/* ───────────────────────────── gauge card ──────────────────────────────── */

function GaugeCard({
  title,
  gauge,
  meta,
  hero = false,
}: {
  title: string;
  gauge: IndicatorGauge | undefined;
  meta: string | null;
  hero?: boolean;
}) {
  const t = useI18n((s) => s.t);
  const label: GaugeVerdict | null = gauge ? verdictOfSafe(gauge.label) : null;
  return (
    <section className={`ic-card ic-cycle ${hero ? "is-hero" : ""}`} aria-label={t("ai-analysis.ind.gauge_aria", "{title} gauge", { title })}>
      <div className="ic-cycle-name">{title}</div>
      <IndicatorCycle label={label} angleDeg={gauge ? needleAngle(gauge) : null} />
      <div className="ic-cycle-verdict">
        <VerdictPill label={gauge?.label ?? null} big={hero} />
      </div>
      <div className="ic-cycle-counts">
        <CountChip kind="sell" value={gauge?.sell ?? null} />
        <CountChip kind="neutral" value={gauge?.neutral ?? null} />
        <CountChip kind="buy" value={gauge?.buy ?? null} />
      </div>
      {meta && <div className="ic-cycle-meta">{meta}</div>}
    </section>
  );
}

/**
 * Needle angle: backend `angle_deg` wins. Only when that field is absent do we
 * replay the backend's own Section-D rating buckets as a pure display
 * transform of the backend counts — never a new opinion.
 */
function needleAngle(g: IndicatorGauge): number | null {
  if (typeof g.angle_deg === "number" && Number.isFinite(g.angle_deg)) return g.angle_deg;
  const total = (g.sell ?? 0) + (g.neutral ?? 0) + (g.buy ?? 0);
  if (!Number.isFinite(total) || total <= 0) return null;
  const rating = ((g.buy ?? 0) - (g.sell ?? 0)) / total;
  if (rating <= -0.5) return 8;
  if (rating <= -0.1) return 28;
  if (rating < 0.1) return 90;
  if (rating < 0.5) return 135;
  return 165;
}

/* ───────────────────── distribution bar (flex-grow counts) ─────────────── */

function DistributionBar({ counts }: { counts: Pick<IndicatorGauge, "sell" | "neutral" | "buy"> | null }) {
  const t = useI18n((s) => s.t);
  const total = counts ? counts.sell + counts.neutral + counts.buy : 0;
  if (!counts || total <= 0) {
    return (
      <div className="ic-gauge-track is-empty" role="img" aria-label={t("ai-analysis.ind.dist_empty_aria", "No distribution counts returned yet")}>
        <span className="ic-gauge-empty">{counts ? t("ai-analysis.ind.dist_no_votes", "no votes yet") : t("ai-analysis.ind.dist_no_summary", "summary not returned")}</span>
      </div>
    );
  }
  // Widths ARE the backend counts — a pure display transform (legacy seg(): flex-grow).
  const grow = (c: number) => ({ flexGrow: Math.max(0, c), flexBasis: 0 });
  return (
    <div className="ic-gauge-track" role="img" aria-label={t("ai-analysis.ind.dist_aria", "Sell {s}, Neutral {n}, Buy {b}", { s: counts.sell, n: counts.neutral, b: counts.buy })}>
      <div className="ic-gseg is-sell" style={grow(counts.sell)} />
      <div className="ic-gseg is-neutral" style={grow(counts.neutral)} />
      <div className="ic-gseg is-buy" style={grow(counts.buy)} />
    </div>
  );
}

/* ─────────────────────────── reading tables ────────────────────────────── */

function ReadingTable({
  title,
  subtitle,
  verdict,
  rows,
  empty,
  show,
}: {
  title: string;
  subtitle: string;
  verdict: string | null;
  rows: IndicatorReading[] | undefined;
  empty: string;
  show: boolean;
}) {
  const t = useI18n((s) => s.t);
  const list = rows ?? [];
  return (
    <section className="ic-card ic-table-panel" aria-label={t("ai-analysis.ind.table_aria", "{title} detail", { title })}>
      <div className="ic-panel-h">
        <h3 className="ic-panel-t">{title}</h3>
        <span className="ic-panel-sub">{subtitle}</span>
        <span className="ic-panel-chip">
          <VerdictPill label={verdict} />
        </span>
      </div>
      {!show || list.length === 0 ? (
        <div className="ic-empty">{empty}</div>
      ) : (
        <div tabIndex={0} className="ic-scroll">
          <table className="ic-table">
            <thead>
              <tr>
                <th scope="col">{t("ai-analysis.th.indicator", "Indicator")}</th>
                <th scope="col" className="ta-r">
                  {t("ai-analysis.th.value", "Value")}
                </th>
                <th scope="col" className="ta-r">
                  {t("ai-analysis.th.action", "Action")}
                </th>
              </tr>
            </thead>
            <tbody>
              {list.map((r, i) => (
                <tr key={`${r.name}-${i}`}>
                  <td className="ic-name">{r.name ?? "—"}</td>
                  <td className="ic-val">{fmtIndicatorValue(r.value)}</td>
                  <td className="ic-act">
                    <span className={actionClasses(r.action)}>
                      <i aria-hidden="true">{actionGlyph(r.action)}</i>
                      {r.action ? t(`ai-analysis.action.${r.action.toLowerCase()}`, r.action) : "—"}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

/* ─────────────────────────── pivot matrix ─────────────────────────────── */

function PivotMatrixPanel({ pivots, show }: { pivots: IndicatorPivots | undefined; show: boolean }) {
  const t = useI18n((s) => s.t);
  const levels = pivots?.levels ?? [];
  const columns = pivots?.columns ?? [];
  const rowsMap = pivots?.rows ?? {};
  const empty = !show || !pivots || levels.length === 0 || columns.length === 0 || Object.keys(rowsMap).length === 0;
  // Rows above P are resistance, below P support, P itself neutral (TV layout).
  const pIdx = levels.indexOf("P");
  return (
    <section className="ic-card ic-table-panel ic-pivots" aria-label={t("ai-analysis.ind.pivot_title", "Pivot Levels")}>
      <div className="ic-panel-h">
        <h3 className="ic-panel-t">{t("ai-analysis.ind.pivot_title", "Pivot Levels")}</h3>
        <span className="ic-panel-sub">{columns.length ? columns.join(" · ").toLowerCase() : t("ai-analysis.ind.pivot_sub", "families as the backend sends them")}</span>
      </div>
      {empty ? (
        <div className="ic-empty">{t("ai-analysis.ind.pivot_empty", "No pivot levels available yet — the backend returned an empty matrix.")}</div>
      ) : (
        <div tabIndex={0} className="ic-scroll">
          <table className="ic-table ic-pivot-table">
            <thead>
              <tr>
                <th scope="col">{t("ai-analysis.ind.pivot_th", "Pivot")}</th>
                {columns.map((c) => (
                  <th key={c} scope="col" className="ta-r">
                    {c}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {levels.map((lv, i) => {
                const row = rowsMap[lv] ?? {};
                const tint = pIdx === -1 ? "" : i < pIdx ? "is-res" : i > pIdx ? "is-sup" : "is-pivot";
                return (
                  <tr key={lv} className={tint}>
                    <th scope="row" className="ic-pivotlvl">
                      {lv}
                    </th>
                    {columns.map((c) => (
                      <td key={c} className="ic-val">
                        {fmtPivotValue(row[c])}
                      </td>
                    ))}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
