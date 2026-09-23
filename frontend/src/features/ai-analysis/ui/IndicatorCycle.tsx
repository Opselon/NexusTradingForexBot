/**
 * IndicatorCycle — TradingView-style semicircle gauge (React port of the
 * legacy `drawCycle()` in Web/tv_widget.js, TV-REDESIGN-1).
 *
 * Pure display: the arc bands, active-label highlights and needle geometry
 * are functions of the backend verdict label + `angle_deg` only. Neutral or
 * missing angle parks the needle at 90° exactly like the legacy default —
 * no value is ever invented.
 * Arc labels are localized at render (t() seam); edge labels sit inset from
 * the viewBox so longer translated words (de/es) cannot clip.
 */

import { useId } from "react";
import type { GaugeVerdict } from "../model";
import { useI18n } from "@/stores/i18nStore";
import { verdictCss, verdictName } from "./indicatorKit";

const CX = 100;
const CY = 100;
const R = 80;

function pt(deg: number): { x: number; y: number } {
  const th = ((180 - deg) * Math.PI) / 180;
  return { x: CX + R * Math.cos(th), y: CY - R * Math.sin(th) };
}

function arc(a0: number, a1: number): string {
  const p0 = pt(a0);
  const p1 = pt(a1);
  const large = Math.abs(a1 - a0) > 180 ? 1 : 0;
  const sweep = a1 > a0 ? 1 : 0;
  return `M ${p0.x.toFixed(1)} ${p0.y.toFixed(1)} A ${R} ${R} 0 ${large} ${sweep} ${p1.x.toFixed(1)} ${p1.y.toFixed(1)}`;
}

/** Verdict -> gradient band on the 0..180° dial (legacy band() ranges). */
function bandFor(label: GaugeVerdict | null): { from: number; to: number; side: "sell" | "buy" } | null {
  if (label === "strong sell") return { from: 0, to: 22, side: "sell" };
  if (label === "sell") return { from: 0, to: 55, side: "sell" };
  if (label === "buy") return { from: 125, to: 180, side: "buy" };
  if (label === "strong buy") return { from: 158, to: 180, side: "buy" };
  return null;
}

/** Geometry only — the visible word comes from verdictName() at render. */
const LABELS: Array<{ v: GaugeVerdict; x: number; y: number }> = [
  { v: "strong sell", x: 36, y: 108 },
  { v: "sell", x: 45, y: 34 },
  { v: "neutral", x: 90, y: 8 },
  { v: "buy", x: 135, y: 34 },
  { v: "strong buy", x: 164, y: 108 },
];

export function IndicatorCycle({ label, angleDeg }: { label: GaugeVerdict | null; angleDeg: number | null }) {
  const t = useI18n((s) => s.t);
  const uid = useId().replace(/[^a-zA-Z0-9_-]/g, "");
  const sellGrad = `ic-grad-sell-${uid}`;
  const buyGrad = `ic-grad-buy-${uid}`;
  const b = bandFor(label);
  const ang = angleDeg !== null && Number.isFinite(angleDeg) ? Math.max(0, Math.min(180, angleDeg)) : 90;
  const th = ((180 - ang) * Math.PI) / 180;
  const nx = CX + 66 * Math.cos(th);
  const ny = CY - 66 * Math.sin(th);
  return (
    <svg viewBox="0 0 200 115" className="ic-cycle-svg" aria-hidden="true">
      <defs>
        <linearGradient id={sellGrad} x1="0%" y1="0%" x2="100%" y2="0%">
          <stop offset="0%" stopColor="#DC2626" />
          <stop offset="100%" stopColor="#F472B6" />
        </linearGradient>
        <linearGradient id={buyGrad} x1="0%" y1="0%" x2="100%" y2="0%">
          <stop offset="0%" stopColor="#3b82f6" />
          <stop offset="100%" stopColor="#38bdf8" />
        </linearGradient>
      </defs>
      <path className="ic-arc-idle" d={arc(0, 180)} fill="none" strokeWidth={11} strokeLinecap="round" />
      {b && <path d={arc(b.from, b.to)} fill="none" stroke={`url(#${b.side === "sell" ? sellGrad : buyGrad})`} strokeWidth={11} strokeLinecap="butt" />}
      {LABELS.map((l) => (
        <text
          key={l.v}
          className={`ic-arc-label ${label === l.v ? `is-active is-${l.v === "neutral" ? "neutral" : verdictCss(l.v)}` : ""}`}
          x={l.x}
          y={l.y}
          fontSize={8.5}
          textAnchor="middle"
        >
          {verdictName(l.v, t)}
        </text>
      ))}
      <line className="ic-needle" x1={CX} y1={CY} x2={nx.toFixed(1)} y2={ny.toFixed(1)} strokeWidth={2.4} strokeLinecap="round" />
      <circle className="ic-hub" cx={CX} cy={CY} r={5} strokeWidth={1.4} />
    </svg>
  );
}
