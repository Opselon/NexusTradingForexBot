/**
 * PURPOSE:  Freshness chip — relative age of a backend probe/timestamp
 *           ("updated 3m ago") with warn styling once the age crosses the
 *           documented staleness thresholds.
 * OWNER:    uiux-wave5-risk  (future edits to this file belong to this lane)
 * CONSUMES: ./riskThresholds (ageWord, freshnessTone, FRESH_* constants)
 * PROVIDES: <FreshnessChip label atMs nowMs /> (returns null with no timestamp)
 * INVARIANTS: the real age is always printed; a missing/unparsable timestamp
 *             renders NO chip (never an invented "0s ago" = fresh), and the
 *             raw timestamp stays reachable via the title attribute.
 * EXTEND:   pass another backend timestamp — never compute freshness from
 *           client-side guesswork.
 */

import { ageWord, freshnessTone, type RskTone } from "./riskThresholds";
import { useI18n } from "@/stores/i18nStore";

export function FreshnessChip({
  label,
  atMs,
  nowMs,
}: {
  /** Short probe name, e.g. "risk status". */
  label: string;
  /** Epoch ms of the backend timestamp (probed_at / query dataUpdatedAt). */
  atMs: number | null | undefined;
  /** Page render clock (AppShell ticker when present). */
  nowMs: number;
}) {
  const t = useI18n((s) => s.t);
  if (atMs === null || atMs === undefined || !Number.isFinite(atMs) || atMs <= 0) return null;
  const ageSec = (nowMs - atMs) / 1000;
  const word = ageWord(ageSec, t);
  if (word === null) return null;
  const tone: RskTone = freshnessTone(ageSec);
  return (
    <span
      className={`rsk-chip rsk-fresh tone-${tone}`}
      title={t("risk.chip.title", "{label} probe — {iso} ({age}s ago); warn > 30s, stale > 120s", { label, iso: new Date(atMs).toISOString(), age: ageSec.toFixed(0) })}
    >
      <span className="rsk-fresh__lab">{label}</span> {word}
    </span>
  );
}
