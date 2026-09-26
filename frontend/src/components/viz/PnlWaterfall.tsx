/**
 * PnlWaterfall — gross -> costs -> net decomposition of ONE closed trade.
 *
 * Every bar is a backend number from the forensic trace outcome block
 * (`gross_pnl`, `commission`, `swap`, `net_pnl`). The running totals are only
 * the arithmetic cumulative of those same reported values — no estimation, and
 * a missing component renders as an explicit UNKNOWN step, never as zero.
 */

import { useMemo } from "react";
import { fmtCompact, scaleLinear } from "./geometry";
import { useI18n } from "@/stores/i18nStore";
import "./viz.css";

const W = 560;

/** Default waterfall value format — module-level, one shared closure. */
const fmtStep = (v: number): string => fmtCompact(v, 2);

export interface WaterfallStep {
  label: string;
  /** Absolute reported value (backend). null = reported as unavailable. */
  value: number | null;
  /** true = running total (balance-like), false = delta bar. */
  total?: boolean;
}

export interface PnlWaterfallProps {
  steps: WaterfallStep[];
  height?: number;
  formatValue?: (v: number) => string;
  emptyHint?: string;
}

export function buildTradeWaterfall(outcome: {
  gross_pnl?: number | null;
  commission?: number | null;
  swap?: number | null;
  net_pnl?: number | null;
}): WaterfallStep[] {
  const steps: WaterfallStep[] = [{ label: "gross", value: outcome.gross_pnl ?? null }];
  if (outcome.commission !== null && outcome.commission !== undefined) {
    steps.push({ label: "commission", value: -Math.abs(outcome.commission) });
  } else {
    steps.push({ label: "commission?", value: null });
  }
  if (outcome.swap !== null && outcome.swap !== undefined) {
    steps.push({ label: "swap", value: -outcome.swap });
  } else {
    steps.push({ label: "swap?", value: null });
  }
  steps.push({ label: "net", value: outcome.net_pnl ?? null, total: true });
  return steps;
}

export function PnlWaterfall({ steps, height = 170, formatValue, emptyHint }: PnlWaterfallProps) {
  const t = useI18n((s) => s.t);
  // Label words depend only on `t`, the formatter only on `formatValue`:
  // memoizing keeps a parent re-render (live trade panel) from rebuilding two
  // closures per paint. Output is byte-identical.
  const stepLabel = useMemo(
    () =>
      (raw: string): string =>
        raw === "gross" ? t("ui.viz.step_gross", "gross")
          : raw === "commission" ? t("ui.viz.step_commission", "commission")
            : raw === "commission?" ? t("ui.viz.step_commission_unknown", "commission?")
              : raw === "swap" ? t("ui.viz.step_swap", "swap")
                : raw === "swap?" ? t("ui.viz.step_swap_unknown", "swap?")
                  : raw === "net" ? t("ui.viz.step_net", "net")
                    : raw,
    [t],
  );
  const fmt = useMemo(() => formatValue ?? fmtStep, [formatValue]);
  const emptyHintText = emptyHint ?? t("ui.viz.pnl_empty", "no PnL decomposition reported");
  // Running levels + scale are a pure function of `steps` (memoized so an
  // unchanged trade never re-derives them); the emitted SVG is byte-identical.
  const geo = useMemo(() => {
    const usable = steps.filter((s) => s.value !== null);
    if (usable.length === 0) return null;

    // Running levels: totals reset the level, deltas accumulate.
    let level = 0;
    const bars = steps.map((s) => {
      if (s.value === null) return { s, from: null, to: null };
      if (s.total) {
        const from = 0;
        const to = s.value;
        level = s.value;
        return { s, from, to };
      }
      const from = level;
      level += s.value;
      return { s, from, to: level };
    });

    const all = bars.flatMap((b) => (b.from === null || b.to === null ? [] : [b.from, b.to]));
    const lo = Math.min(0, ...all);
    const hi = Math.max(0, ...all);
    const padT = 14;
    const padB = 30;
    const toY = scaleLinear(lo, hi, height - padB, padT);
    const zeroY = toY(0);
    const step = W / steps.length;
    const barW = Math.max(10, Math.min(48, step * 0.55));
    return { bars, toY, zeroY, step, barW };
  }, [steps, height]);

  if (geo === null) return <div className="viz-empty">{emptyHintText}</div>;
  const { bars, toY, zeroY, step, barW } = geo;

  return (
    <div className="viz-frame">
      <svg className="viz" viewBox={`0 0 ${W} ${height}`} role="img" aria-label={t("ui.viz.pnl_aria", "PnL waterfall from backend-reported components")}>
        <line className="viz-zero" x1={0} x2={W} y1={zeroY} y2={zeroY} />
        {bars.map((b, i) => {
          const x = i * step + (step - barW) / 2;
          if (b.from === null || b.to === null) {
            return (
              <g key={i}>
                <rect x={x} y={zeroY - 3} width={barW} height={6} rx={2} fill="var(--bg-panel-2)" stroke="var(--border-strong)" />
                <text className="viz-step-label" x={x + barW / 2} y={height - 16} textAnchor="middle">
                  {stepLabel(b.s.label)}
                </text>
                <text className="viz-step-value neu" x={x + barW / 2} y={height - 5} textAnchor="middle">
                  {t("ui.word.unknown", "UNKNOWN")}
                </text>
              </g>
            );
          }
          const y0 = toY(Math.max(b.from, b.to));
          const y1 = toY(Math.min(b.from, b.to));
          const h = Math.max(2, y1 - y0);
          const positive = b.to >= b.from;
          const cls = b.s.total ? (b.to >= 0 ? "pos" : "neg") : positive ? "pos" : "neg";
          return (
            <g key={i}>
              <rect
                className={`viz-bar ${cls}`}
                x={x}
                y={y0}
                width={barW}
                height={h}
                rx={2}
                opacity={b.s.total ? 1 : 0.85}
              />
              <text className="viz-step-label" x={x + barW / 2} y={height - 16} textAnchor="middle">
                {stepLabel(b.s.label)}
              </text>
              <text className={`viz-step-value ${cls}`} x={x + barW / 2} y={y0 - 4} textAnchor="middle">
                {fmt(b.s.value ?? 0)}
              </text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}
