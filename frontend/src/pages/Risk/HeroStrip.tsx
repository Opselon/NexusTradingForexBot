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

import { pressureOf, SOFT_UTIL, HARD_UTIL, type RiskLimitRow, type Translator } from "./riskThresholds";
import { useI18n } from "@/stores/i18nStore";

interface Bucket {
  id: string;
  tone: "ok" | "warn" | "down" | "unknown";
  test: (p: number | null) => boolean;
}

/** Bucket captions are render copy — literal t() keys (parity gate). */
function stripLabelT(id: string, t: Translator): string {
  switch (id) {
    case "down": return t("risk.strip.breaches", "breaches");
    case "warn": return t("risk.strip.near_limit", "near limit");
    case "ok": return t("risk.strip.ok", "ok");
    default: return t("risk.strip.unknown", "unknown");
  }
}

/**
 * Bucket definitions over `pressureOf` (1.00 = exactly at the backend limit):
 *  down    — past the backend limit (p > HARD_UTIL)
 *  warn    — inside the limit but past the soft threshold (SOFT < p ≤ HARD)
 *  ok      — at/below the soft threshold (p ≤ SOFT)
 *  unknown — no comparable pair in the payload (p = null)
 */
const COUNTS: Bucket[] = [
  { id: "down", tone: "down", test: (p) => p !== null && p > HARD_UTIL },
  { id: "warn", tone: "warn", test: (p) => p !== null && p > SOFT_UTIL && p <= HARD_UTIL },
  { id: "ok", tone: "ok", test: (p) => p !== null && p <= SOFT_UTIL },
  { id: "unknown", tone: "unknown", test: (p) => p === null },
];

export function HeroStrip({ rows }: { rows: RiskLimitRow[] }) {
  const t = useI18n((s) => s.t);
  if (rows.length === 0) return null;
  const pressures = rows.map((r) => pressureOf(r.value, r.limit, r.direction));
  const buckets = COUNTS.map((b) => ({ ...b, n: pressures.filter(b.test).length }));
  const visible = buckets.filter((b) => b.n > 0);
  if (visible.length === 0) return null;

  return (
    <div className="rsk-strip" role="status" aria-label={t("risk.strip.aria", "Derived limit summary")}>
      <span className="rsk-strip__tag" title={t("risk.strip.title", "Sums over the limit rows rendered on this page — client-side arithmetic, no backend verdict.")}>
        {t("risk.strip.derived", "derived")}
      </span>
      {buckets.map((b) => (
        <span key={b.id} className={`rsk-chip tone-${b.tone} ${b.n === 0 ? "zero" : ""}`}>
          <b>{b.n}</b> {stripLabelT(b.id, t)}
        </span>
      ))}
      <span className="rsk-strip__src">{t("risk.strip.src", "sums over {n} visible limit rows · value vs its own backend limit", { n: rows.length })}</span>
    </div>
  );
}
