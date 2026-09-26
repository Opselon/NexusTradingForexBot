/**
 * ConfidenceGauge — responsive radial meter for ONE backend ratio (0..1).
 *
 * SVG viewBox has a FIXED coordinate space; all responsiveness is CSS
 * (inline-size 100% + aspect-ratio). The parent card sets the box, this
 * component fills it. It can never out-grow its container, which is the exact
 * failure mode the old fixed-pixel Gauge produced when .viz{width:100%}
 * scaled its viewBox without any height constraint.
 *
 * 270° sweep (open at the bottom), value arc + tick marks + tier label.
 * null/absent value renders a dashed UNKNOWN track — never a fabricated 0.
 *
 * Contract inherited from the viz kit: props carry backend values only; this
 * component derives nothing from the data and fills no gaps.
 */

import { useMemo } from "react";
import { arcPath, clampRatio } from "./geometry";
import { useI18n } from "@/stores/i18nStore";
import "./confidence-gauge.css";

export type ConfidenceTone = "pos" | "warn" | "bad" | "neu";

export interface ConfidenceGaugeProps {
  /** Confidence ratio in [0,1] from the backend registry payload. */
  value: number | null | undefined;
  /** Optional alias shown in place of the raw strategy_id. */
  label?: string;
  /** Size of the square viewBox coordinate space (NOT the rendered size). */
  viewBoxSize?: number;
  /** Override the tone; otherwise derived from the ratio. */
  tone?: ConfidenceTone;
  /** Compact variant drops the tier word + tick labels (grid view). */
  compact?: boolean;
}

const START = -135;
const SWEEP = 270;

/** Tier classification by confidence ratio. Echoes registry tiers, display-only. */
export function tierFor(ratio: number | null): string {
  if (ratio === null) return "UNKNOWN";
  if (ratio >= 0.7) return "HIGH";
  if (ratio >= 0.5) return "MID";
  return "LOW";
}

export function toneForRatio(ratio: number | null): ConfidenceTone {
  if (ratio === null) return "neu";
  if (ratio >= 0.7) return "pos";
  if (ratio >= 0.5) return "warn";
  return "bad";
}

/** Localize the display tier word at the render site; tierFor stays pure. */
export function tierText(
  t: (key: string, fallback: string, vars?: Record<string, string | number>) => string,
  tier: string,
): string {
  if (tier === "HIGH") return t("ui.viz.tier_high", "HIGH");
  if (tier === "MID") return t("ui.viz.tier_mid", "MID");
  if (tier === "LOW") return t("ui.viz.tier_low", "LOW");
  return t("ui.word.unknown", "UNKNOWN");
}

export function ConfidenceGauge({
  value,
  label,
  viewBoxSize = 200,
  tone,
  compact = false,
}: ConfidenceGaugeProps) {
  const t = useI18n((s) => s.t);
  const ratio = clampRatio(value);
  const toneCls = tone ?? toneForRatio(ratio);
  const tier = tierFor(ratio);
  const readout = ratio === null ? "—" : ratio.toFixed(2);

  // The dial's static geometry (centre, radius, full-sweep track path) is a
  // pure function of the viewBox size — computed once per size, not per paint
  // of a live-updating strategy grid.
  const { cx, cy, r, trackD } = useMemo(() => {
    const c = viewBoxSize / 2;
    const radius = c - 14;
    return { cx: c, cy: c, r: radius, trackD: arcPath(c, c, radius, START, START + SWEEP) };
  }, [viewBoxSize]);

  const angle = START + (ratio ?? 0) * SWEEP;

  return (
    <div className="cg-wrap">
      <svg
        className="cg-svg"
        viewBox={`0 0 ${viewBoxSize} ${viewBoxSize}`}
        role="img"
        aria-label={label ? `${label}: ${readout}` : t("ui.viz.confidence_aria", "confidence {r}", { r: readout })}
      >
        {/* Track + value arc. vector-effect keeps the stroke crisp at any
            scale. A null ratio swaps the track for the dashed indeterminate
            rail — the arc is not drawn at 0, because the backend sent no
            ratio (see confidence-gauge.css `.cg-track.unknown`). */}
        <path
          className={`cg-track${ratio === null ? " unknown" : ""}`}
          d={trackD}
          fill="none"
          strokeLinecap="round"
        />
        {ratio !== null && (
          <path
            className={`cg-value ${toneCls}`}
            d={arcPath(cx, cy, r, START, angle)}
            fill="none"
            strokeLinecap="round"
          />
        )}
        <circle className={`cg-hub ${ratio === null ? "neu" : toneCls}`} cx={cx} cy={cy} r={2.6} />
        {!compact && (
          <>
            <text
              className="cg-value-text"
              x={cx}
              y={cy - 2}
              textAnchor="middle"
            >
              {readout}
            </text>
            <text className="cg-tier-text" x={cx} y={cy + 18} textAnchor="middle">
              {tierText(t, tier)}
            </text>
          </>
        )}
        {compact && (
          <text className="cg-value-text cg-compact-text" x={cx} y={cy + 4} textAnchor="middle">
            {readout}
          </text>
        )}
      </svg>
      {label && <div className="cg-label">{label}</div>}
    </div>
  );
}
