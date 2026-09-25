/**
 * indicatorKit — shared display atoms for the indicators console (lane I).
 *
 * Presentation only: verdict/action classification maps the backend's own
 * vocabulary (service.py gauge labels + ports.py actions) verbatim to CSS
 * classes; an unrecognized string renders as "unknown", never a guess.
 * Rendered verdict WORDS are localized through the t() seam; an unrecognized
 * backend label is still shown verbatim.
 */

import type { GaugeVerdict } from "../model";
import { useI18n } from "@/stores/i18nStore";

/** t() signature (mirrors i18nStore) so plain helpers can take it as an arg. */
export type TranslateFn = (key: string, fallback: string, vars?: Record<string, string | number>) => string;

/** Recognized backend verdict -> localized display word (literal keys only). */
export function verdictName(v: GaugeVerdict, t: TranslateFn): string {
  if (v === "strong sell") return t("ai-analysis.verdict.strong_sell", "Strong sell");
  if (v === "sell") return t("ai-analysis.verdict.sell", "Sell");
  if (v === "neutral") return t("ai-analysis.verdict.neutral", "Neutral");
  if (v === "buy") return t("ai-analysis.verdict.buy", "Buy");
  return t("ai-analysis.verdict.strong_buy", "Strong buy");
}

/** Verdict pill — Buy green / Sell red / Neutral dim, Strong shades (verbatim label). */
export function VerdictPill({ label, big = false }: { label: string | null; big?: boolean }) {
  const t = useI18n((s) => s.t);
  const v = label ? verdictOfSafe(label) : null;
  const cls = v ? `ic-verdict is-${v === "neutral" ? "neutral" : verdictCss(v)}` : "ic-verdict is-unknown";
  return (
    <span className={`${cls} ${big ? "is-big" : ""}`}>
      {v ? <i aria-hidden="true">{verdictGlyph(v)}</i> : <i aria-hidden="true">◌</i>}
      {v ? verdictName(v, t) : (label ?? "—")}
    </span>
  );
}

export function CountChip({ kind, value }: { kind: "sell" | "neutral" | "buy"; value: number | null }) {
  const t = useI18n((s) => s.t);
  const word = kind === "sell" ? verdictName("sell", t) : kind === "neutral" ? verdictName("neutral", t) : verdictName("buy", t);
  return (
    <div className={`ic-count is-${kind}`}>
      <span className="ic-count-k">{word}</span>
      <span className="ic-count-v">{value === null || !Number.isFinite(value) ? "—" : value}</span>
    </div>
  );
}

/** Map a backend label to the display vocabulary (null = unrecognized). */
export function verdictOfSafe(label: string | null | undefined): GaugeVerdict | null {
  const a = (label ?? "").trim().toLowerCase();
  if (a === "strong sell") return "strong sell";
  if (a === "sell") return "sell";
  if (a === "buy") return "buy";
  if (a === "strong buy") return "strong buy";
  if (a === "neutral") return "neutral";
  return null;
}

/** CSS suffix for a verdict: sells share the sell ramp, buys the buy ramp. */
export function verdictCss(v: GaugeVerdict): "buy" | "sell" | "neutral" {
  if (v === "buy" || v === "strong buy") return "buy";
  if (v === "sell" || v === "strong sell") return "sell";
  return "neutral";
}

export function verdictGlyph(v: GaugeVerdict): string {
  const c = verdictCss(v);
  return c === "buy" ? "▲" : c === "sell" ? "▼" : "●";
}

/** Action cell classes — Strong buy/Strong sell get the intensified shade. */
export function actionClasses(action: string | null | undefined): string {
  const v = verdictOfSafe(action);
  if (!v) return "ic-pill is-unknown";
  if (v === "strong buy") return "ic-pill is-buy is-strong";
  if (v === "strong sell") return "ic-pill is-sell is-strong";
  return `ic-pill is-${verdictCss(v)}`;
}

export function actionGlyph(action: string | null | undefined): string {
  const v = verdictOfSafe(action);
  if (!v) return "•";
  const c = verdictCss(v);
  return c === "buy" ? "▲" : c === "sell" ? "▼" : "●";
}

/** Legacy tv_widget.fmt — trading display for indicator values. */
export function fmtIndicatorValue(v: number | null | undefined): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return "—";
  if (Math.abs(v) >= 1000) return v.toLocaleString("en-US", { minimumFractionDigits: 3, maximumFractionDigits: 3 });
  const s = Math.abs(v) >= 100 ? v.toFixed(2) : v.toFixed(3);
  return s.replace(/(\.\d*?)0+$/, "$1").replace(/\.$/, "");
}

/** Pivot prices: 2-4 decimals by magnitude (display-only formatting). */
export function fmtPivotValue(v: number | null | undefined): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return "—";
  const a = Math.abs(v);
  const digits = a >= 100 ? 2 : a >= 10 ? 3 : 4;
  return v.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}
