/**
 * SignedBucketChart — signed bullish/bearish/neutral bars per time bucket.
 *
 * Purpose-built for the news impact timeline (backend buckets) but generic:
 * any "signed contributions per time bucket" payload renders here. Bars are the
 * backend's own sums; nothing is smoothed or extrapolated.
 */

import { useMemo } from "react";
import { extent, fmtCompact, scaleLinear } from "./geometry";
import { useI18n } from "@/stores/i18nStore";
import "./viz.css";

export interface SignedBucket {
  bucket_start?: string;
  bucket_ts?: number;
  bullish: number | null;
  bearish: number | null;
  neutral?: number | null;
  article_count?: number;
  top_title?: string;
}

export interface SignedBucketChartProps {
  buckets: SignedBucket[];
  height?: number;
  emptyHint?: string;
  /** Caption for hovered/annotated bucket count. */
  bucketLabel?: (b: SignedBucket) => string;
}

const W = 640;

/** Bucket axis/tooltip number format — module-level, one shared closure. */
const fmtBucket = (v: number): string => fmtCompact(v, 2);

/** Default bucket caption from the backend timestamp; "" when it is absent. */
const defaultBucketLabel = (b: SignedBucket): string =>
  b.bucket_start ? new Date(b.bucket_start).toLocaleString("en-GB", { hour12: false }) : "";

export function SignedBucketChart({ buckets, height = 180, emptyHint, bucketLabel }: SignedBucketChartProps) {
  const t = useI18n((s) => s.t);
  const emptyHintText = emptyHint ?? t("ui.viz.sb_empty", "no timeline buckets from the backend");
  // Bars derive from `buckets` (identity changes only when a fetch lands);
  // memoizing keeps an unchanged timeline from being re-walked on every parent
  // render. The emitted SVG (and each <title> caption) is byte-identical.
  const geo = useMemo(() => {
    if (buckets.length === 0) return null;
    const ups = buckets.map((b) => Math.max(0, b.bullish ?? 0));
    const downs = buckets.map((b) => Math.max(0, b.bearish ?? 0));
    const neut = buckets.map((b) => Math.max(0, b.neutral ?? 0));
    const ext = extent([...ups, ...downs, ...neut, 0]) ?? [0, 1];
    const maxAbs = Math.max(ext[1], 1e-9);
    const padT = 10;
    const padB = 20;
    const toY = scaleLinear(-maxAbs, maxAbs, height - padB, padT);
    const step = W / buckets.length;
    const barW = Math.max(3, Math.min(26, step * 0.6));
    return { toY, step, barW };
  }, [buckets, height]);

  if (geo === null) return <div className="viz-empty">{emptyHintText}</div>;
  const { toY, step, barW } = geo;
  const fmt = fmtBucket;
  const label = bucketLabel ?? defaultBucketLabel;
  return (
    <div className="viz-frame">
      <svg className="viz" viewBox={`0 0 ${W} ${height}`} role="img" aria-label={t("ui.viz.sb_aria", "impact timeline, {n} buckets", { n: buckets.length })}>
        <line className="viz-zero" x1={0} x2={W} y1={toY(0)} y2={toY(0)} />
        {buckets.map((b, i) => {
          const x = i * step + (step - barW) / 2;
          const bull = Math.max(0, b.bullish ?? 0);
          const bear = Math.max(0, b.bearish ?? 0);
          const neu = Math.max(0, b.neutral ?? 0);
          const zero = toY(0);
          const neuH = Math.abs(toY(neu / 2) - zero);
          return (
            <g key={i}>
              <title>{t("ui.viz.sb_tooltip", "{l} · bullish {bull} · bearish {bear} · neutral {neu} · {n} articles{extra}", { l: label(b), bull: fmt(bull), bear: fmt(bear), neu: fmt(neu), n: b.article_count ?? 0, extra: b.top_title ? ` · ${b.top_title}` : "" })}</title>
              {neu > 0 && <rect className="viz-bar neu" x={x - 1} y={zero - neuH} width={barW + 2} height={Math.max(1, neuH * 2)} opacity={0.25} />}
              {bull > 0 && <rect className="viz-bar pos" x={x} y={toY(bull)} width={barW} height={Math.max(1, zero - toY(bull))} rx={1} />}
              {bear > 0 && <rect className="viz-bar neg" x={x} y={zero} width={barW} height={Math.max(1, toY(-bear) - zero)} rx={1} />}
            </g>
          );
        })}
        <text className="viz-axis" x={0} y={height - 6}>
          {label(buckets[0] ?? { bullish: 0, bearish: 0 })}
        </text>
        <text className="viz-axis" x={W} y={height - 6} textAnchor="end">
          {label(buckets[buckets.length - 1] ?? { bullish: 0, bearish: 0 })}
        </text>
      </svg>
      <div className="viz-legend">
        <span>
          <i className="sw pos" />
          {t("ui.viz.bullish", "bullish")}
        </span>
        <span>
          <i className="sw neg" />
          {t("ui.viz.bearish", "bearish")}
        </span>
        <span>
          <i className="sw flat" />
          {t("ui.viz.neutral", "neutral")}
        </span>
      </div>
    </div>
  );
}
