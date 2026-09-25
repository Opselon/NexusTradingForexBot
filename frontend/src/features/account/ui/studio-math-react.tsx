/**
 * studio-math-react — thin React renderers over studio-math (the pure core).
 *
 * PURPOSE:  JSX wrappers for emphasis bars and distribution rows so the
 *           sections stay declarative and free of inline math.
 * OWNER:    uiux-w6-account
 * CONSUMES: ./studio-math (pure math), ./shared (moneyOrDash), @/lib/format
 * PROVIDES: <EmphBars/>, <DistRow/>, <DistPanel/>, <KpiCard/>
 * INVARIANTS: every prop is a backend number; null renders "—" or an explicit
 *           gap slot, never a zero bar. No data is fetched or derived beyond
 *           on-screen scaling (labeled "derived" where shown).
 * EXTEND:   new renderers go here; new MATH goes in studio-math.
 */

import type { ReactNode } from "react";
import { useI18n } from "@/stores/i18nStore";
import { moneyOrDash } from "./shared";


import { distStyle, scaleEmphBars, type EmphSegment, type Tone } from "./studio-math";
import "./account-studio.css";

/**
 * Emphasis bars — magnitude strips scaled to the largest on-screen value.
 * Used under equity plates (per-sample |Δ|) and the per-period PnL strip.
 * A null sample renders as an empty gap slot (never a synthetic bar).
 */
export function EmphBars({ bars, minPct, maxPct }: { bars: EmphSegment[]; minPct?: number; maxPct?: number }) {
  const t = useI18n((s) => s.t);
  const scaled = scaleEmphBars([{ segments: bars, minPct, maxPct }]);
  const segs = scaled[0] ?? [];
  return (
    <div className="acc-emph" role="img" aria-label={t("account.studio.emph_bars_aria", "magnitude bars scaled to the largest on-screen value")}>
      {segs.map((s, j) => (
        <i key={j} className={s.tone} style={s.h === null ? undefined : { height: `${s.h}%` }} />
      ))}
    </div>
  );
}

/** A level/emphasis strip for a single series of scalars (mono, zero-free scale). */
export function EmphLevel({ values, minPct, maxPct }: { values: Array<number | null>; minPct?: number; maxPct?: number }) {
  const peak = values.reduce<number>((m, v) => (v === null || !Number.isFinite(v) ? m : Math.max(m, Math.abs(v))), 0);
  const lo = values.reduce<number>((m, v) => (v === null || !Number.isFinite(v) ? m : Math.min(m, v)), Infinity);
  const hi = values.reduce<number>((m, v) => (v === null || !Number.isFinite(v) ? m : Math.max(m, v)), -Infinity);
  const lo2 = Number.isFinite(lo) ? lo : 0;
  const hi2 = Number.isFinite(hi) ? hi : 0;
  const span = hi2 - lo2;
  const min = minPct ?? 6;
  const max = maxPct ?? 100;
  return (
    <div className="acc-emph" role="img" aria-label="level strip, min to max of the shown samples">
      {values.map((v, i) => {
        if (v === null) return <i key={i} className="gap" />;
        if (peak <= 0 || span === 0) return <i key={i} className="mono" style={{ height: `${min}%` }} />;
        const t = span > 0 ? (v - lo2) / span : 0.5;
        return <i key={i} className="mono" style={{ height: `${Math.round(min + (max - min) * t)}%` }} />;
      })}
    </div>
  );
}

/* -------------------------------- plate ---------------------------------- */

export interface PlateProps {
  title: string;
  /** Right-aligned mono meta (sample counts, ranges — read-only facts). */
  meta?: ReactNode;
  footer?: ReactNode;
  children: ReactNode;
}

/**
 * Grid plate — the framing unit for charts and metric strips: recessed
 * surface, engineering-grid background, corner ticks. Pure decoration: the
 * children (shared viz kit) render the data exactly as before.
 */
export function Plate({ title, meta, footer, children }: PlateProps) {
  return (
    <section className="acc-plate">
      <div className="acc-plate-h">
        <span className="acc-plate-t">{title}</span>
        {meta !== undefined && meta !== null && <span className="acc-plate-m">{meta}</span>}
      </div>
      {children}
      {footer && <div className="acc-plate-f">{footer}</div>}
    </section>
  );
}

/* ------------------------------ distribution ----------------------------- */

export interface DistRowProps {
  label: string;
  value: number | null;
  /** The full scale this row is a fraction of (a loaded backend number). */
  full: number;
  tone?: Tone;
  title?: string;
  /**
   * Pre-formatted value label. The caller owns the unit: passing "1.42" for
   * Sharpe and "$1,240.00" for a PnL row is what keeps a ratio from ever
   * rendering as "+$1.42" (data-honesty law: never change a number's unit on
   * screen). Falls back to money formatting when omitted.
   */
  text?: string;
}

/** A single ranked distribution row (label, track, raw value). */
export function DistRow({ label, value, full, tone, title, text }: DistRowProps) {
  const s = distStyle(value, full, tone ? { tone } : undefined);
  const shown = text ?? moneyOrDash(value);
  return (
    <div className="acc-dist-row" title={title ?? `${label}: ${shown}`}>
      <span className="acc-dist-lab">{label}</span>
      <span className={`acc-dist-track ${s.unknown ? "unknown" : ""}`}>
        <i
          className={`acc-dist-fill ${s.tone}`}
          style={s.unknown ? undefined : { inlineSize: `${s.width}%` }}
        />
      </span>
      <span className={`acc-dist-val ${s.tone}`}>{shown}</span>
    </div>
  );
}

/** A panel of distribution rows with a header and an honesty footer. */
export function DistPanel({
  title,
  rows,
  scaleNote,
  footer,
}: {
  title: string;
  rows: DistRowProps[];
  scaleNote?: ReactNode;
  footer?: ReactNode;
}) {
  return (
    <div className="acc-plate">
      <div className="acc-plate-h">
        <span className="acc-plate-t">{title}</span>
        {scaleNote && <span className="acc-plate-m">{scaleNote}</span>}
      </div>
      <div className="acc-dist">{rows.map((r) => <DistRow key={r.label} {...r} />)}</div>
      {footer && <div className="acc-plate-f">{footer}</div>}
    </div>
  );
}

/* --------------------------------- KPI ----------------------------------- */

export interface KpiCardProps {
  label: string;
  value: ReactNode;
  tone?: Tone;
  /**
   * Optional signed delta line. `hint` states WHAT was subtracted (e.g. "vs
   * balance") so the derived badge is self-explanatory; the caller computes
   * the delta from loaded numbers only.
   */
  delta?: { text: string; tone: Tone; derived?: boolean; hint?: string };
  sub?: ReactNode;
  /** Marks the whole VALUE as client-computed (rare — prefer delta only). */
  derived?: boolean;
}

/** One hero KPI card: monospace value, token-colored by sign. */
export function KpiCard({ label, value, tone, delta, sub, derived }: KpiCardProps) {
  return (
    <div className="acc-kpi" data-tone={tone ?? "dim"}>
      <div className="acc-kpi-k">
        {label}
        {derived && <span className="acc-kpi-derived">derived</span>}
      </div>
      <div className={`acc-kpi-v ${tone ?? "dim"}`}>{value}</div>
      {delta && (
        <div className="acc-kpi-d">
          {delta.hint && <span className="faint">{delta.hint}</span>}
          <span className={delta.tone}>{delta.text}</span>
          {delta.derived && <span className="acc-kpi-derived">derived</span>}
        </div>
      )}
      {sub !== undefined && sub !== null && sub !== "" && <div className="acc-kpi-s">{sub}</div>}
    </div>
  );
}
