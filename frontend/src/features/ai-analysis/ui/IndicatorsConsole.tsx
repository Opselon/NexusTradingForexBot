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
  const errorMessage = status === "stale" || status === "error" ? explainError(snapshotQ.error) : null;

  const gauges = data?.gauges ?? {};
  const osc = gauges.oscillators;
  const sum = gauges.summary;
  const ma = gauges.moving_averages;
  const sumTotal = sum ? sum.sell + sum.neutral + sum.buy : 0;
  // Distribution counts: backend summary {Sell,Neutral,Buy}; the summary gauge
  // carries the same aggregate, so it is the verbatim fallback.
  const summaryCounts = data?.summary;
  const dist: Pick<IndicatorGauge, "sell" | "neutral" | "buy"> | null = summaryCounts
    ? { sell: summaryCounts.Sell ?? 0, neutral: summaryCounts.Neutral ?? 0, buy: summaryCounts.Buy ?? 0 }
    : sum
      ? { sell: sum.sell, neutral: sum.neutral, buy: sum.buy }
      : null;

  return (
    <div className="ic-console">
      {/* 1) MARKET CONTEXT STRIP — every value from the real snapshot */}
      <section className="ic-card ic-context" aria-label="Market context">
        <div className="ic-idbox">
          <span className="ic-flag" aria-hidden="true">
            ◆
          </span>
          <span className="ic-symbol">{data?.symbol || "—"}</span>
          <span className="ic-tfchip" title="Active timeframe">
            {data?.timeframe || tf}
          </span>
          <span className="ic-bars">
            {data ? `${data.source_bar_count ?? data.bar_count} source bars · ${data.bar_count} in window` : "awaiting snapshot…"}
          </span>
        </div>
        <div className="ic-pricewrap">
          <span className="ic-plabel">Last close</span>
          <span className="ic-price">{formatPrice(data?.last_close ?? null)}</span>
          <span className="ic-updated" title="When this browser last received a successful snapshot">
            {status === "loading" ? "updated —" : `updated ${formatTime(snapshotQ.dataUpdatedAt || null)}`}
            {snapshotQ.isFetching ? " · refreshing…" : ""}
          </span>
        </div>
        <span className={`ic-live ic-live-${status}`} role="status" aria-live="polite">
          <i className="ic-live-dot" aria-hidden="true" />
          {status === "live" ? "LIVE" : status === "stale" ? "STALE" : status === "error" ? "ERROR" : "WAITING"}
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
              ? `Live feed interrupted — showing values from ${formatTime(snapshotQ.dataUpdatedAt || null)} · ${errorMessage}`
              : errorMessage}
          </span>
          <button type="button" className="ic-retry" onClick={() => void snapshotQ.refetch()} disabled={snapshotQ.isFetching}>
            {snapshotQ.isFetching ? "retrying…" : "Retry"}
          </button>
        </div>
      )}

      {/* 2) TIMEFRAME TOOLBAR — curated list, backend-invalid aliases never offered */}
      <div className="ic-tfbar" role="group" aria-label="Timeframe selection">
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
        <div className="ic-loading" aria-label="Loading indicator snapshot">
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
          <div className="ic-cycles" role="group" aria-label="Technical gauges">
            <GaugeCard title="Oscillators" gauge={osc} meta={osc ? `${osc.sell + osc.neutral + osc.buy} oscillators` : null} />
            <GaugeCard title="Summary" gauge={sum} hero meta={sum ? `${sumTotal} signals · ${data?.timeframe || tf}` : null} />
            <GaugeCard title="Moving Averages" gauge={ma} meta={ma ? `${ma.sell + ma.neutral + ma.buy} averages` : null} />
          </div>

          {/* 4) DISTRIBUTION SIGNAL BAR — widths ARE the backend counts */}
          <section className="ic-card ic-signalbar" aria-label="Overall technical summary distribution">
            <div className="ic-signal-head">
              <span className="ic-signal-name">Overall Technical Summary</span>
              <VerdictPill label={sum?.label ?? null} />
              <span className="ic-signal-meta">{sum ? `${sumTotal} votes counted by the backend` : "no summary gauge returned"}</span>
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
              title="Oscillators"
              subtitle="momentum & strength"
              verdict={osc?.label ?? null}
              rows={data?.oscillators}
              show={status !== "error"}
              empty="No oscillator data for this timeframe yet — waiting for completed bars."
            />
            <ReadingTable
              title="Moving Averages"
              subtitle="trend & crossover"
              verdict={ma?.label ?? null}
              rows={data?.moving_averages}
              show={status !== "error"}
              empty="No moving-average data for this timeframe yet — waiting for completed bars."
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
function explainError(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.code === "ENGINE_UNAVAILABLE") return "Waiting for engine — indicators need live completed bars.";
    if (e.code === "RESOURCE_UNAVAILABLE") return "No bar history yet…";
    if (e.code === "TIMEOUT") return "Indicator compute timed out (retrying on schedule)…";
    return `Indicator feed unavailable (${e.code})`;
  }
  return "Indicator feed unavailable";
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
  const label: GaugeVerdict | null = gauge ? verdictOfSafe(gauge.label) : null;
  return (
    <section className={`ic-card ic-cycle ${hero ? "is-hero" : ""}`} aria-label={`${title} gauge`}>
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
  const total = counts ? counts.sell + counts.neutral + counts.buy : 0;
  if (!counts || total <= 0) {
    return (
      <div className="ic-gauge-track is-empty" role="img" aria-label="No distribution counts returned yet">
        <span className="ic-gauge-empty">{counts ? "no votes yet" : "summary not returned"}</span>
      </div>
    );
  }
  // Widths ARE the backend counts — a pure display transform (legacy seg(): flex-grow).
  const grow = (c: number) => ({ flexGrow: Math.max(0, c), flexBasis: 0 });
  return (
    <div className="ic-gauge-track" role="img" aria-label={`Sell ${counts.sell}, Neutral ${counts.neutral}, Buy ${counts.buy}`}>
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
  const list = rows ?? [];
  return (
    <section className="ic-card ic-table-panel" aria-label={`${title} detail`}>
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
                <th scope="col">Indicator</th>
                <th scope="col" className="ta-r">
                  Value
                </th>
                <th scope="col" className="ta-r">
                  Action
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
                      {r.action ?? "—"}
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
  const levels = pivots?.levels ?? [];
  const columns = pivots?.columns ?? [];
  const rowsMap = pivots?.rows ?? {};
  const empty = !show || !pivots || levels.length === 0 || columns.length === 0 || Object.keys(rowsMap).length === 0;
  // Rows above P are resistance, below P support, P itself neutral (TV layout).
  const pIdx = levels.indexOf("P");
  return (
    <section className="ic-card ic-table-panel ic-pivots" aria-label="Pivot levels">
      <div className="ic-panel-h">
        <h3 className="ic-panel-t">Pivot Levels</h3>
        <span className="ic-panel-sub">{columns.length ? columns.join(" · ").toLowerCase() : "families as the backend sends them"}</span>
      </div>
      {empty ? (
        <div className="ic-empty">No pivot levels available yet — the backend returned an empty matrix.</div>
      ) : (
        <div tabIndex={0} className="ic-scroll">
          <table className="ic-table ic-pivot-table">
            <thead>
              <tr>
                <th scope="col">Pivot</th>
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
