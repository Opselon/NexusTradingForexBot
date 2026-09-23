/**
 * PURPOSE:  Small presentational bits shared by the Intelligence page's
 *           signal cards: relative-time stamp and a 0..1 backend score bar.
 * OWNER:    uiux-wave5-intel  (future edits to this file belong to this lane)
 * CONSUMES: @/lib/format (formatDateTime for absolute-time fallbacks)
 * PROVIDES: relTime(), ScoreBar
 * INVARIANTS: renders ONLY backend-supplied numbers/timestamps — the bar width
 *           is the backend 0..1 value * 100 (clamped for display only), the
 *           label always shows the raw number; a missing value renders nothing
 *           or "—" upstream, never a guessed value or a made-up scale.
 * EXTEND:   new card-level pure helpers go here; never add fetch/state to this file.
 */

import { formatDateTime } from "@/lib/format";

/**
 * Relative time for a backend timestamp ("just now", "5m ago", "3h ago"…).
 * Returns null when the value is missing/unparseable so callers can fall back
 * to the absolute backend timestamp (never an invented age).
 * Far-future timestamps (clock skew) render absolutely rather than as "now".
 */
export function relTime(iso: string | number | null | undefined): string | null {
  if (iso === null || iso === undefined || iso === "") return null;
  const t = typeof iso === "number" ? (iso > 1e12 ? iso : iso * 1000) : Date.parse(iso);
  if (!Number.isFinite(t)) return null;
  const diff = Date.now() - t;
  if (diff < -60_000) return formatDateTime(iso); // future → absolute, honestly
  if (diff < 60_000) return "just now";
  const s = Math.floor(diff / 1000);
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86_400) return `${Math.floor(s / 3600)}h ago`;
  if (s < 86_400 * 30) return `${Math.floor(s / 86_400)}d ago`;
  return formatDateTime(iso);
}

/**
 * Slim score bar for a backend 0..1 fraction (news confidence, article
 * importance_score — both fractions by construction in the backend).
 * Width = value * 100% (clamped to the track), label shows the RAW number the
 * backend sent. Null/absent → renders nothing (the card's "—" figure already
 * states the absence; an empty track must not read as a zero score).
 */
export function ScoreBar({ value, label }: { value: number | null | undefined; label: string }) {
  if (value === null || value === undefined || !Number.isFinite(value)) return null;
  const pct = Math.max(0, Math.min(1, value)) * 100;
  return (
    <span className="itl-score" title={`${label}: ${value} (backend 0–1 score)`}>
      <span className="itl-score__lab">{label}</span>
      <span className="itl-score__num">{value.toFixed(3)}</span>
      <span className="itl-score__track" role="img" aria-label={`${label}: ${value}`}>
        <i className="itl-score__fill" style={{ inlineSize: `${pct}%` }} />
      </span>
    </span>
  );
}
