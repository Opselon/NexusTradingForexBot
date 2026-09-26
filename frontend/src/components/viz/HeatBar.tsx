/**
 * PURPOSE:  one labelled row per backend ratio with a segmented 0..1 strip.
 * OWNER:    uiux-modern-20260926 lane 2 (components/viz) — future edits go here.
 * CONSUMES: HeatBarItem.value (0..1 backend ratio), label/caption/tone props,
 *           i18n words ui.viz.heatbar_empty / ui.word.unknown, theme tokens.
 * PROVIDES: HeatBar + the HeatBarProps/HeatBarItem types (via ./index).
 * INVARIANTS: values render exactly as sent — a null ratio is a dashed UNKNOWN
 *           row (never a filled zero); colours come from color-mix() over
 *           --green/--red/--amber/--accent; no fetching, no derived verdicts.
 * EXTEND:   add a tone by extending TONE_VARS and the fill precompute; a new
 *           prop must default to an honest state, never to a number.
 *
 * Used wherever the backend reports ratio-like values (source success ratios,
 * keyword share, factor scores).
 */

import { useMemo } from "react";
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

type HeatTone = NonNullable<HeatBarItem["tone"]>;

function toneFor(ratio: number | null, invert: boolean): HeatTone {
  if (ratio === null) return "neu";
  const r = invert ? 1 - ratio : ratio;
  if (r >= 0.7) return "pos";
  if (r >= 0.4) return "warn";
  return "bad";
}

/** Strip coordinate space (viewBox units) — fixed, so every row aligns. */
const W = 120;
const H = 14;
const GAP = 2;
const TONES: readonly HeatTone[] = ["pos", "warn", "bad", "neu"];
/** Intensity ramp shared by every tone: segment k lights at ALPHAS[k]. */
const ALPHAS: readonly number[] = [0.18, 0.42, 0.68, 0.9];
const TONE_VARS: Record<HeatTone, string> = {
  pos: "var(--green)",
  warn: "var(--amber)",
  bad: "var(--red)",
  neu: "var(--accent)",
};

interface SegGeometry {
  xs: number[];
  cellW: number;
  /** fills[tone][lit][segmentIndex] -> ready-to-use fill string. */
  fills: Record<HeatTone, string[][]>;
}

/**
 * Segment geometry plus every `color-mix()` fill string, built ONCE per
 * `segments` value. The row loop then does array lookups instead of
 * re-deriving cell widths and re-painting ramp strings on every render of a
 * live-updating list.
 */
function buildSegments(segments: number): SegGeometry {
  const cellW = (W - GAP * (segments - 1)) / segments;
  const xs: number[] = [];
  for (let s = 0; s < segments; s += 1) xs.push(s * (cellW + GAP));
  const fills = {} as Record<HeatTone, string[][]>;
  for (const tone of TONES) {
    const byLit: string[][] = [];
    for (let lit = 0; lit <= segments; lit += 1) {
      const row: string[] = [];
      for (let s = 0; s < segments; s += 1) {
        if (s < lit) {
          const alpha = ALPHAS[Math.min(s, ALPHAS.length - 1)] ?? 0.9;
          row.push(`color-mix(in srgb, ${TONE_VARS[tone]} ${Math.round(alpha * 100)}%, transparent)`);
        } else {
          row.push("var(--bg-inset)");
        }
      }
      byLit.push(row);
    }
    fills[tone] = byLit;
  }
  return { xs, cellW, fills };
}

export function HeatBar({
  items,
  scaleCaptions,
  emptyHint,
  invert = false,
  segments = 4,
}: HeatBarProps) {
  const t = useI18n((s) => s.t);
  const seg = useMemo(() => buildSegments(segments), [segments]);
  if (items.length === 0)
    return <div className="viz-empty">{emptyHint ?? t("ui.viz.heatbar_empty", "no rows reported")}</div>;
  const { xs, cellW, fills } = seg;
  return (
    <div className="heatbar">
      {items.map((it, i) => {
        const ratio = clampRatio(it.value);
        const unknown = ratio === null;
        const tone = it.tone ?? toneFor(ratio, invert);
        const lit = unknown ? 0 : Math.min(segments, Math.max(ratio > 0 ? 1 : 0, Math.round(ratio * segments)));
        // An unknown row paints NOTHING into the strip: the `.row.unknown`
        // rail (dashed border + hatched inset) shows through, so "no data"
        // can never be mistaken for a healthy, empty-zero bar.
        const rowFills = unknown ? null : (fills[tone] ?? fills.neu);
        return (
          <div
            className={`row${unknown ? " unknown" : ""}`}
            key={`${it.label}-${i}`}
            title={it.title ?? it.label}
          >
            <span className="lab">{it.label}</span>
            <svg
              className="viz"
              viewBox={`0 0 ${W} ${H}`}
              style={{ height: H }}
              role="img"
              aria-label={`${it.label}: ${unknown ? t("ui.word.unknown", "UNKNOWN") : `${(ratio * 100).toFixed(0)}%`}`}
            >
              {xs.map((x, s) => (
                <rect
                  key={s}
                  x={x}
                  y={0}
                  width={cellW}
                  height={H}
                  rx={2}
                  fill={rowFills ? (rowFills[lit]?.[s] ?? "var(--bg-inset)") : "none"}
                />
              ))}
            </svg>
            <span className="val">
              {unknown ? t("ui.word.unknown", "UNKNOWN") : (it.caption ?? `${(ratio * 100).toFixed(0)}%`)}
            </span>
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
