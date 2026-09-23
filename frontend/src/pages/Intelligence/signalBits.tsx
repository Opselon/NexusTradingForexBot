/**
 * PURPOSE:  Small presentational bits shared by the Intelligence page's
 *           signal/autopsy cards and the KPI strip: relative-time stamp, a
 *           0..1 backend score bar beside its RAW number, and verbatim
 *           backend-word badges whose tone only re-words what the payload said.
 * OWNER:    ui/w2-lane-c  (future edits to this file belong to lane C)
 * CONSUMES: @/lib/format (formatDateTime for absolute-time fallbacks),
 *           theme.css badge classes as styled by ./feed.css (`.itl-*`).
 * PROVIDES: relTime(), ScoreBar, importanceTone(), outcomeTone(), WordBadge
 * INVARIANTS: renders ONLY backend-supplied words/numbers/timestamps — the bar
 *           width is the backend 0..1 value * 100 (clamped for display only)
 *           and the number printed beside it is the RAW payload value (never
 *           re-rounded); tone maps RESTATE a backend word and never compute a
 *           verdict; a missing value renders the caller's honest fallback ("—"
 *           or "UNKNOWN"), never a guessed value or a made-up scale.
 * EXTEND:   new card-level pure helpers go here; never add fetch/state to this
 *           file. New tone words = extend the map, never a threshold.
 */

import { formatDateTime } from "@/lib/format";

/** Badge tone classes provided by theme.css (good/warn/bad/neutral/unknown). */
export type WordTone = "good" | "warn" | "bad" | "neutral" | "unknown";

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
 * Badge tone that only RESTATES a backend `importance` word. Producer words
 * (news/models.py NewsImportance): TRIVIAL / MINOR / MODERATE / HIGH /
 * CRITICAL; calendar words (EventImportance: "Low"/"Medium"/"High"/"Holiday")
 * normalize to the same letters. Anything else — including a missing value —
 * is `unknown`; the badge still prints the raw word.
 */
export function importanceTone(word: string | number | null | undefined): WordTone {
  if (word === null || word === undefined) return "unknown";
  const w = String(word).trim().toUpperCase();
  if (w === "CRITICAL" || w === "HIGH") return "bad";
  if (w === "MODERATE" || w === "MEDIUM") return "warn";
  if (w === "MINOR" || w === "LOW" || w === "TRIVIAL" || w === "HOLIDAY") return "neutral";
  return "unknown";
}

/**
 * Badge tone that only RESTATES a backend `outcome` word. Producer words
 * (accounting/models.py TradeOutcome): WIN / LOSS / BREAKEVEN; anything else
 * (including absent) reads `unknown` — never reclassified here.
 */
export function outcomeTone(word: string | number | null | undefined): WordTone {
  if (word === null || word === undefined) return "unknown";
  const w = String(word).trim().toUpperCase();
  if (w === "WIN") return "good";
  if (w === "LOSS") return "bad";
  if (w === "BREAKEVEN") return "neutral";
  return "unknown";
}

/**
 * Verbatim backend word in a tone badge. The text is the payload value exactly
 * as sent (trimmed); a missing value renders `fallback` ("—" / "UNKNOWN") with
 * the `unknown` tone supplied by the caller's tone map. `title` carries the
 * full raw string as a tooltip when the badge text could be truncated.
 */
export function WordBadge({
  word,
  fallback = "UNKNOWN",
  tone,
  title,
}: {
  word?: string | number | null | undefined;
  fallback?: string;
  tone: WordTone;
  title?: string;
}) {
  const raw = word === null || word === undefined ? "" : String(word).trim();
  const text = raw ? raw : fallback;
  return (
    <span
      className={`badge ${tone} itl-word`}
      title={title ?? (raw ? `backend value: ${raw}` : "backend sent no value")}
    >
      {text}
    </span>
  );
}

/**
 * Slim score bar for a backend 0..1 fraction (news confidence, article
 * importance_score — both fractions by construction in the backend).
 * Width = value * 100% (clamped to the track, display only); the label beside
 * the bar prints the RAW payload number so the scaled visual always sits next
 * to the exact value it represents. Null/absent → renders nothing (the card's
 * "—" figure already states the absence; an empty track must not read as zero).
 */
export function ScoreBar({ value, label }: { value: number | null | undefined; label: string }) {
  if (value === null || value === undefined || !Number.isFinite(value)) return null;
  const pct = Math.max(0, Math.min(1, value)) * 100;
  const raw = String(value);
  return (
    <span className="itl-score" title={`${label}: ${raw} — backend 0–1 score, bar clamped for display`}>
      <span className="itl-score__lab">{label}</span>
      <span className="itl-score__num">{raw}</span>
      <span className="itl-score__track" role="img" aria-label={`${label}: ${raw}`}>
        <i className="itl-score__fill" style={{ inlineSize: `${pct}%` }} />
      </span>
    </span>
  );
}
