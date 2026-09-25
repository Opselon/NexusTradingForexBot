/**
 * HeatBar — one SVG row per labeled key with a segmented 0..1 intensity strip.
 *
 * Used wherever the backend reports a set of ratio-like values (source
 * success ratios, keyword share, factor scores). Values are rendered exactly as
 * given; a null ratio draws an UNKNOWN row (dashed outline) instead of an empty
 * bar that could be mistaken for a healthy zero.
 */

import { clampRatio } from "./geometry";
import { useI18n } from "@/stores/i18nStore";
import "./viz.css";

export interface HeatBarItem {
  label: string;
  /** 0..1 intensity (backend ratio). null = unknown. */
  value: number | null | undefined;
  /** Right-hand caption (verbatim backend text/number). */
  caption?: string;
  /** Force the semantic tone; otherwise derived from the ratio. */
  tone?: "pos" | "warn" | "bad" | "neu";
  title?: string;
}

export interface HeatBarProps {
  items: HeatBarItem[];
  /** Legend captions under the strip (defaults 0 / 1). */
  scaleCaptions?: [string, string];
  emptyHint?: string;
  /** When true, high ratios are BAD (failure rates). Default: high = good. */
  invert?: boolean;
  /** Number of segments in the strip. */
  segments?: number;
}

function toneFor(ratio: number | null, invert: boolean): "pos" | "warn" | "bad" | "neu" {
  if (ratio === null) return "neu";
  const r = invert ? 1 - ratio : ratio;
  if (r >= 0.7) return "pos";
  if (r >= 0.4) return "warn";
  return "bad";
}

const FILL: Record<string, number[]> = {
  pos: [0.18, 0.42, 0.68, 0.9],
  warn: [0.18, 0.42, 0.68, 0.9],
  bad: [0.18, 0.42, 0.68, 0.9],
  neu: [0.18, 0.42, 0.68, 0.9],
};

const VAR: Record<string, string> = {
  pos: "var(--green)",
  warn: "var(--amber)",
  bad: "var(--red)",
  neu: "var(--accent)",
};

export function HeatBar({
  items,
  scaleCaptions,
  emptyHint,
  invert = false,
  segments = 4,
}: HeatBarProps) {
  const t = useI18n((s) => s.t);
  if (items.length === 0)
    return <div className="viz-empty">{emptyHint ?? t("ui.viz.heatbar_empty", "no rows reported")}</div>;
  const W = 120;
  const gap = 2;
  const cellW = (W - gap * (segments - 1)) / segments;
  return (
    <div className="heatbar">
      {items.map((it, i) => {
        const ratio = clampRatio(it.value);
        const tone = it.tone ?? toneFor(ratio, invert);
        const lit = ratio === null ? 0 : Math.min(segments, Math.max(ratio > 0 ? 1 : 0, Math.round(ratio * segments)));
        const alphas = FILL[tone] ?? FILL.neu ?? [0.18, 0.42, 0.68, 0.9];
        return (
          <div className="row" key={`${it.label}-${i}`} title={it.title ?? it.label}>
            <span className="lab">{it.label}</span>
            <svg
              className="viz"
              viewBox={`0 0 ${W} 14`}
              style={{ height: 14 }}
              role="img"
              aria-label={`${it.label}: ${ratio === null ? t("ui.word.unknown", "UNKNOWN") : `${(ratio * 100).toFixed(0)}%`}`}
            >
              {Array.from({ length: segments }, (_, s) => {
                const on = s < lit;
                const a = alphas[Math.min(s, alphas.length - 1)] ?? 0.9;
                return (
                  <rect
                    key={s}
                    x={s * (cellW + gap)}
                    y={0}
                    width={cellW}
                    height={14}
                    rx={2}
                    fill={on ? `color-mix(in srgb, ${VAR[tone]} ${Math.round(a * 100)}%, transparent)` : "var(--bg-inset)"}
                    stroke={ratio === null ? "var(--border-strong)" : "none"}
                    strokeDasharray={ratio === null ? "2 2" : undefined}
                  />
                );
              })}
            </svg>
            <span className="val">{ratio === null ? t("ui.word.unknown", "UNKNOWN") : (it.caption ?? `${(ratio * 100).toFixed(0)}%`)}</span>
          </div>
        );
      })}
      <div className="heatbar-scale">
        <span>{scaleCaptions?.[0] ?? "0"}</span>
        <span>{scaleCaptions?.[1] ?? "1.0"}</span>
      </div>
    </div>
  );
}
