/**
 * PURPOSE:  Hero summary strip — derived breach/ok counts summed over the
 *           visible limit rows, shown as a labelled chip row at the top of
 *           the Risk page; hidden when there are no rows to count.
 * OWNER:    uiux-wave5-risk  (future edits to this file belong to this lane)
 * CONSUMES: ./riskThresholds (RiskLimitRow, pressureOf, SOFT/HARD thresholds)
 * PROVIDES: <HeroStrip rows={…} />
 * INVARIANTS: every count is a sum over the rows actually rendered below —
 *             stamped "derived"; zero rows ⇒ nothing is rendered (an empty
 *             strip must never read as "0 breaches = safe").
 * EXTEND:   new buckets belong in the COUNTS array with the same
 *           pressure-based arithmetic; never hardcode a number.
 */

import { useMemo } from "react";
import { useI18n } from "@/stores/i18nStore";
import { pressureOf, SOFT_UTIL, HARD_UTIL, type RiskLimitRow } from "./riskThresholds";

interface Bucket {
  id: string;
  label: string;
  tone: "ok" | "warn" | "down" | "unknown";
  test: (p: number | null) => boolean;
}

/**
 * Bucket definitions over `pressureOf` (1.00 = exactly at the backend limit):
 *  down    — past the backend limit (p > HARD_UTIL)
 *  warn    — inside the limit but past the soft threshold (SOFT < p ≤ HARD)
 *  ok      — at/below the soft threshold (p ≤ SOFT)
 *  unknown — no comparable pair in the payload (p = null)
 */
const COUNTS: Array<Omit<Bucket, "label" | "n"> & { key: string; fallback: string }> = [
  { id: "down", key: "risk.strip.breaches", fallback: "breaches", tone: "down", test: (p) => p !== null && p > HARD_UTIL },
  { id: "warn", key: "risk.strip.near_limit", fallback: "near limit", tone: "warn", test: (p) => p !== null && p > SOFT_UTIL && p <= HARD_UTIL },
  { id: "ok", key: "risk.strip.ok", fallback: "ok", tone: "ok", test: (p) => p !== null && p <= SOFT_UTIL },
  { id: "unknown", key: "risk.strip.unknown", fallback: "unknown", tone: "unknown", test: (p) => p === null },
];

export function HeroStrip({ rows }: { rows: RiskLimitRow[] }) {
  const t = useI18n((s) => s.t);
  const pressures = useMemo(() => rows.map((r) => pressureOf(r.value, r.limit, r.direction)), [rows]);
  const buckets = useMemo(
    () => COUNTS.map((b) => ({ ...b, label: t(b.key, b.fallback), n: pressures.filter(b.test).length })),
    [pressures, t],
  );
  if (rows.length === 0) return null;
  const visible = buckets.filter((b) => b.n > 0);
  if (visible.length === 0) return null;

  return (
    <div className="rsk-strip" role="status" aria-label={t("risk.strip.summary_aria", "Derived limit summary")}>
      <span className="rsk-strip__tag" title={t("risk.strip.tag_title", "Sums over the limit rows rendered on this page — client-side arithmetic, no backend verdict.")}>
        {t("risk.strip.derived", "derived")}
      </span>
      {buckets.map((b) => (
        <span key={b.id} className={`rsk-chip tone-${b.tone} ${b.n === 0 ? "zero" : ""}`}>
          <b>{b.n}</b> {t(b.key, b.fallback)}
        </span>
      ))}
      <span className="rsk-strip__src">{t("risk.strip.src", "sums over {n} visible limit rows · value vs its own backend limit", { n: rows.length })}</span>
    </div>
  );
}
