/**
 * PURPOSE:  KPI strip for /alt/intelligence — four backend news-state cards
 *           (state + stale flag, bull-vs-bear split, confidence/relevance
 *           meters, active events) plus the optional fetch-age caption.
 * OWNER:    ui/w2-lane-a  (future edits to this file belong to lane A)
 * CONSUMES: NewsState payload fields only (state, bullish_score,
 *           bearish_score, confidence, xauusd_relevance,
 *           active_event_count, timestamp, stale, freshness, available),
 *           the query's pending flag, and an OPTIONAL dataUpdatedAt (client
 *           cache time of the news-state query — not a backend field).
 * PROVIDES: default KpiStrip — rendered by IntelligencePage.
 * INVARIANTS: numbers are the backend's own values — only unit-scaled for
 *           display (percent label, bar width clamped to the track) with
 *           the RAW 0–1 value shown next to every bar; missing/unavailable
 *           renders "—" or the explicit UNAVAILABLE word — never
 *           zero-filled, never inferred; stale/available flags always
 *           rendered as such; tone only restate backend words (BREAKING/
 *           HIGH_IMPACT, STALE) — no frontend verdicts.
 * EXTEND:   a new KPI = a new backend field rendered verbatim.
 */

import type { NewsState } from "@/types/domain";
import { formatDateTime, formatPct } from "@/lib/format";
import { relTime } from "@/pages/Intelligence/signalBits";
import { useI18n } from "@/stores/i18nStore";

interface KpiStripProps {
  ns: NewsState | undefined;
  pending: boolean;
  /**
   * react-query dataUpdatedAt (ms, client clock) for the news-state query.
   * Optional: IntelligencePage does not pass it today, so the "fetched Xm
   * ago" caption stays omitted until it does. Not a backend payload field —
   * the caption labels it as a client cache age.
   */
  dataUpdatedAt?: number;
}

/** Backend 0–1 fraction → percent label; missing/non-finite → "—" (never 0). */
function pct(v: number | null | undefined): string {
  return formatPct(v !== null && v !== undefined && Number.isFinite(v) ? v * 100 : null, 0);
}

/** Raw display of a backend 0–1 fraction ("—" when the field was not sent). */
function raw(v: number | null | undefined): string {
  return v !== null && v !== undefined && Number.isFinite(v) ? String(v) : "—";
}

/**
 * One meter row: label + RAW backend 0–1 value + a bar scaled FROM that
 * value (clamped to the track for display only). Missing field → "—" on a
 * dashed track — an absence never reads as a zero score.
 */
function ScoreRow({ label, value, tone }: { label: string; value: number | null | undefined; tone: "bull" | "bear" | "accent" }) {
  const t = useI18n((s) => s.t);
  const v = value !== null && value !== undefined && Number.isFinite(value) ? value : null;
  const frac = v === null ? null : Math.min(1, Math.max(0, v));
  return (
    <div className={`ix-kpi-srow${v === null ? " is-empty" : ""}`}>
      <span className={`lab ${tone}`}>{label}</span>
      <span className="track" role="img" aria-label={`${label}: ${v === null ? t("intelligence.status.unavailable", "unavailable") : v}`}>
        {frac !== null && <i className={tone} style={{ inlineSize: `${frac * 100}%` }} />}
      </span>
      <span
        className="num"
        title={
          v === null
            ? t("intelligence.kpi.not_sent", "{label}: not sent by the backend", { label })
            : t("intelligence.kpi.raw_field_title", "{label} = {value} (verbatim backend 0–1 field)", { label, value: String(v) })
        }
      >
        {raw(v)}
      </span>
    </div>
  );
}

export default function KpiStrip({ ns, pending, dataUpdatedAt }: KpiStripProps) {
  const t = useI18n((s) => s.t);
  const loading = pending && !ns;
  /** Only restates backend flags/words — never a computed verdict. */
  const available = Boolean(ns?.available);
  const stale = Boolean(ns?.stale);
  const breaking = ns?.state === "BREAKING" || ns?.state === "HIGH_IMPACT";
  /** Event pulse scales with the backend's own count (0 → no pulse). */
  const eventPulse = typeof ns?.active_event_count === "number" && ns.active_event_count > 0;
  /** Optional client cache age ("fetched Xm ago") — omitted until wired. */
  const fetchedAgo = typeof dataUpdatedAt === "number" && dataUpdatedAt > 0 ? relTime(dataUpdatedAt) : null;

  return (
<div className="ix-kpi-wrap">
  <div className="grid cols-4 ix-kpis">
    {/* state — fields: available, state, stale, freshness */}
    <article className="ix-kpi-card ix-kpi--state">
      <div className="ix-kpi-head">
        <span className="ix-kpi-ico" aria-hidden="true">◈</span>
        <span className="ix-kpi-lab">{t("intelligence.kpi.news_state", "News state (backend)")}</span>
        {stale && (
          <span className="ix-kpi-flag" title={t("intelligence.kpi.stale_title", "stale: true — the backend marks this context stale (verbatim flag)")}>
            {t("intelligence.kpi.stale_flag", "STALE")}
          </span>
        )}
      </div>
      <div className={`ix-kpi-val${loading || !available ? " is-dim" : breaking ? " is-neg" : ""}`}>
        {loading ? "…" : available ? (ns?.state ?? "—") : t("intelligence.status.unavailable", "UNAVAILABLE")}
      </div>
      <div className="ix-kpi-sub">
        {loading
          ? t("intelligence.kpi.awaiting", "awaiting first backend payload")
          : !available
            ? t("intelligence.kpi.news_offline", "news subsystem disabled or offline")
            : stale
              ? t("intelligence.kpi.news_stale", "⚠ backend marks context STALE")
              : t("intelligence.kpi.freshness", "freshness {v}", { v: raw(ns?.freshness) })}
      </div>
    </article>

    {/* sentiment split — fields: bullish_score, bearish_score (0–1 each) */}
    <article className="ix-kpi-card ix-kpi--sentiment">
      <div className="ix-kpi-head">
        <span className="ix-kpi-ico" aria-hidden="true">▲▼</span>
        <span className="ix-kpi-lab">{t("intelligence.kpi.bullish_bearish", "Bullish / Bearish")}</span>
      </div>
      <div className={`ix-kpi-val${loading || !available ? " is-dim" : ""}`}>
        {loading ? "…" : available ? `${pct(ns?.bullish_score)} / ${pct(ns?.bearish_score)}` : "—"}
      </div>
      <div className="ix-kpi-sub">
        <ScoreRow label={t("intelligence.kpi.bull", "BULL")} tone="bull" value={available ? ns?.bullish_score : null} />
        <ScoreRow label={t("intelligence.kpi.bear", "BEAR")} tone="bear" value={available ? ns?.bearish_score : null} />
        <span className="ix-kpi-cap">{t("intelligence.kpi.sentiment_sub", "backend-scored sentiment (display only)")}</span>
      </div>
    </article>

    {/* confidence + relevance — fields: confidence, xauusd_relevance (0–1) */}
    <article className="ix-kpi-card ix-kpi--confidence">
      <div className="ix-kpi-head">
        <span className="ix-kpi-ico" aria-hidden="true">◐</span>
        <span className="ix-kpi-lab">{t("intelligence.kpi.context_confidence", "Context confidence")}</span>
      </div>
      <div className={`ix-kpi-val${loading || pct(ns?.confidence) === "—" ? " is-dim" : ""}`}>
        {loading ? "…" : pct(ns?.confidence)}
      </div>
      <div className="ix-kpi-sub">
        <ScoreRow label={t("intelligence.kpi.confidence", "confidence")} tone="accent" value={ns?.confidence} />
        <ScoreRow label={t("intelligence.kpi.xauusd_rel", "XAUUSD rel.")} tone="accent" value={ns?.xauusd_relevance} />
        <span className="ix-kpi-cap">{t("intelligence.kpi.raw_beside_bar", "raw 0–1 shown beside every bar")}</span>
      </div>
    </article>

    {/* events — fields: active_event_count, timestamp (verbatim) */}
    <article className={`ix-kpi-card ix-kpi--events${eventPulse ? " has-pulse" : ""}`}>
      <div className="ix-kpi-head">
        <span className="ix-kpi-ico" aria-hidden="true">◉</span>
        <span className="ix-kpi-lab">{t("intelligence.kpi.active_events", "Active events")}</span>
      </div>
      <div className={`ix-kpi-val${loading || (ns?.active_event_count ?? null) === null ? " is-dim" : ""}`}>
        {loading ? "…" : (ns?.active_event_count ?? "—")}
      </div>
      <div className="ix-kpi-sub">
        <span>{t("intelligence.kpi.context_time", "context time {v}", { v: ns?.timestamp ? formatDateTime(ns.timestamp) : "—" })}</span>
        {!loading && !available && <span className="ix-kpi-cap">{t("intelligence.kpi.news_offline", "news subsystem disabled or offline")}</span>}
      </div>
    </article>
  </div>

  {/* optional client cache age — only rendered when dataUpdatedAt is passed in */}
  {fetchedAgo !== null && (
    <div
      className="ix-kpi-fetched"
      title={t("intelligence.kpi.cache_age_title", "Client-side cache age of the news-state query (react-query dataUpdatedAt) — not a backend payload field.")}
    >
      <span className="dot" aria-hidden="true" />
      {t("intelligence.kpi.fetched", "fetched {v}", { v: fetchedAgo })}
    </div>
  )}
</div>
  );
}
