/**
 * FeedQualityChip — connection-quality chrome for the realtime console.
 *
 * Renders ONLY the derived feed-health view model (stores/feedStore.ts,
 * throttled ≤1Hz) — never a second judgement of the feed. The authoritative
 * state machine stays `RealtimeStatus` in the websocket layer; this chip is a
 * projection of it for cheap renders: tone · server version · data age · gaps.
 *
 * Label/tone/age text all come from the pure selectors in lib/feedHealth.ts
 * (unit-tested in tests/js/pro_feed.test.mjs). No polling, no fetch here.
 */

import {
  FEED_TONE_LABEL,
  formatFeedLabel,
  selectFeedTone,
  useFeedHealth,
  type FeedTone,
} from "@/stores/feedStore";
import { formatAgeMs } from "@/lib/format";

const DOT_CLASS: Record<FeedTone, string> = {
  live: "connected",
  reconnecting: "reconnecting",
  stale: "reconnecting",
  down: "disconnected",
};

interface Props {
  /** Shared 1Hz clock (AppShell ticker) so age text ticks without own timers. */
  nowMs: number;
  /** Compact = tone + version only (topbars/dense rows). */
  compact?: boolean;
}

export function FeedQualityChip({ nowMs, compact = false }: Props) {
  const feed = useFeedHealth();
  const tone = selectFeedTone(feed, nowMs);
  const label = compact ? FEED_TONE_LABEL[tone] : formatFeedLabel(feed, nowMs);
  const title =
    `feed: ${tone.toUpperCase()} · state_version ${feed.lastVersion ?? "—"} · ` +
    `data age ${formatAgeMs(feed.dataAgeMs)} · gaps ${feed.gapCount} · ` +
    `reconnects ${feed.reconnectAttempts} — derived from the realtime client (source of truth)`;
  return (
    <span className={`conn-chip feed-chip ${tone}`} title={title} data-feed-tone={tone}>
      <span className={`conn-dot ${DOT_CLASS[tone]}`} />
      <span>{label || "FEED —"}</span>
    </span>
  );
}
