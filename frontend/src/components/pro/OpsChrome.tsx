/**
 * OpsChrome — pro operational chrome for the alternative console.
 *
 * Six presentation widgets, ALL backend-truth driven (this repo's cardinal
 * rule: render what the backend said, mark everything else UNKNOWN):
 *
 *   KpiStrip        — hero metric row; trend arrows ONLY from a parent-
 *                     provided previous reading (opsChromeMath.computeTrend).
 *                     No prev → neutral dash. There is no hidden history.
 *   DecisionCard    — the large ai_decision word through lib/signal.ts
 *                     (explainSignal / the CHG-0048 humanizer): plain "why",
 *                     raw reason code kept verbatim in the detail layer, and
 *                     the NOT-CONSULTED confidence semantics preserved.
 *   QuoteTape       — bid / ask / spread + honest age of the last DATA frame.
 *                     Flash contract documented below (parent-owned key).
 *   FeedQualityChip — transport state WORD (echoed), last frame wall time,
 *                     data age. It makes NO latency promise: a browser cannot
 *                     measure broker→server latency, so none is shown. The
 *                     inter-frame gap (lastGapSec) is computed upstream.
 *   Watermark       — subtle fixed overlay ("STALE" / "LIVE FEED DOWN")
 *                     driven purely by parent truth flags.
 *   ErrorTriage     — failed-request card: message + request_id + Retry.
 *
 * No raw network calls in this file — every value arrives through props that
 * the parent fills from the api layer / realtime client.
 */

import { useEffect, useRef, useState, type ReactNode } from "react";
import type { EngineSnapshot } from "@/types/domain";
import type { ConnectionState } from "@/types/realtime";
import { explainSignal } from "@/lib/signal";
import { formatNumber, formatPrice, formatTime } from "@/lib/format";
import { useI18n } from "@/stores/i18nStore";
import {
  computeTrend,
  flashKeyDiffers,
  formatTickAge,
  trendDelta,
  watermarkWord,
  type TrendDirection,
} from "@/lib/opsChromeMath";
import "./pro-chrome.css";

// ---------------------------------------------------------------------------
// KpiStrip
// ---------------------------------------------------------------------------

export interface KpiItem {
  key: string;
  label: string;
  /** Pre-formatted display value (parent owns formatting). */
  value: ReactNode;
  sub?: ReactNode;
  /**
   * Numeric readings for the trend arrow. BOTH must be finite and the prev
   * must be PROVIDED BY THE PARENT (a previously rendered snapshot value).
   * No prev → "—" (neutral). This component keeps no history of its own.
   */
  cur?: number | null;
  prev?: number | null;
  /** "rising is bad" metrics (drawdown, spread): inverts the arrow tone. */
  goodWhenDown?: boolean;
  /** Fixed value tone (word-status tiles); numeric KPIs tone via trend. */
  tone?: "pos" | "neg" | "dim";
  /** Tooltip text (e.g. source path of the value). */
  title?: string;
}

const TREND_GLYPH: Record<TrendDirection, string> = { up: "▲", down: "▼", flat: "▬", neutral: "—" };

function trendTone(dir: TrendDirection, goodWhenDown: boolean | undefined): string {
  if (dir === "neutral") return "trend-neutral";
  if (dir === "flat") return "trend-flat";
  const risingIsGood = !goodWhenDown;
  const good = (dir === "up") === risingIsGood;
  return good ? "trend-good" : "trend-bad";
}

export function KpiStrip({ items }: { items: KpiItem[] }) {
  return (
    <div className="ops-kpi-strip" role="group" aria-label="Key operating metrics">
      {items.map((it) => {
        const dir = computeTrend(it.cur ?? null, it.prev ?? null);
        const delta = trendDelta(it.cur ?? null, it.prev ?? null);
        const trendTitle =
          dir === "neutral"
            ? "no previous reading provided — trend not computed"
            : `prev ${it.prev} → cur ${it.cur}${delta !== null ? ` (Δ ${delta > 0 ? "+" : ""}${Number(delta.toFixed(6))})` : ""}`;
        return (
          <div className="ops-kpi" key={it.key} title={it.title}>
            <div className="ops-kpi-label">{it.label}</div>
            <div className={`ops-kpi-value ${it.tone ?? ""}`}>
              <span>{it.value}</span>
              <span className={`ops-kpi-trend ${trendTone(dir, it.goodWhenDown)}`} title={trendTitle} aria-hidden="true">
                {TREND_GLYPH[dir]}
              </span>
            </div>
            {it.sub !== undefined && <div className="ops-kpi-sub">{it.sub}</div>}
          </div>
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------------------
// DecisionCard (humanizer-backed — lib/signal.ts / Web/ux_signal.js parity)
// ---------------------------------------------------------------------------

/**
 * Renders the current AI decision through `explainSignal`. Truth rules
 * inherited from the humanizer: unknown reason codes fall back to the RAW
 * code verbatim, and confidence 0.0 with a non-gate block is labelled
 * NOT CONSULTED — never a fake "0.0%". Absence of a decision renders UNKNOWN;
 * a "NO TRADE" is never fabricated from null.
 */
export function DecisionCard({ snapshot }: { snapshot: EngineSnapshot | undefined }) {
  const t = useI18n((s) => s.t);
  const explain = snapshot ? explainSignal(snapshot, t) : null;

  if (!explain) {
    return (
      <div className="ops-decision hold">
        <div className="ops-decision-head">
          <span className="ops-decision-label">AI DECISION</span>
        </div>
        <div className="ops-decision-word unknown">UNKNOWN</div>
        <div className="ops-decision-human muted">
          No decision payload in the current snapshot — nothing is inferred.
        </div>
      </div>
    );
  }

  const freshnessWord = explain.freshness ?? null;
  return (
    <div className={`ops-decision ${explain.tone}`}>
      <div className="ops-decision-head">
        <span className="ops-decision-label">AI DECISION</span>
        <span className={`ops-decision-freshness ${freshnessWord === "FRESH" ? "good" : freshnessWord === "STALE" ? "bad" : "warn"}`}>
          {freshnessWord ?? "FRESHNESS —"}
        </span>
      </div>
      <div className={`ops-decision-word ${explain.tone}`}>{explain.label}</div>
      <div className="ops-decision-confidence">
        {explain.confidenceKind === "not_consulted"
          ? t("ux.signal.not_available", "Signal not available")
          : `${t("ux.decision.confidence", "confidence")} ${explain.confidenceText}`}
      </div>
      {explain.human && <div className="ops-decision-human">{explain.human}</div>}
      {explain.detail && <div className="ops-decision-detail inline-mono">{explain.detail}</div>}
      {!explain.human && !explain.detail && (
        <div className="ops-decision-human muted">{t("ux.decision.no_reason", "Backend provided no reason code.")}</div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// QuoteTape
// ---------------------------------------------------------------------------

export interface QuoteTapeProps {
  symbol: string | null;
  bid: number | null;
  ask: number | null;
  /** Backend-provided spread (points). null renders "—"; never re-derived. */
  spread: number | null;
  priceDigits: number | null;
  /**
   * Age (ms) of the last accepted DATA frame, computed by the parent from
   * backend tick ages or the realtime client's frame wall-time. null → "—".
   */
  dataAgeMs: number | null;
  /**
   * FLASH CONTRACT (important): the PARENT must only advance this key when
   * `snapshot.state_version` ACTUALLY advanced (i.e. a newer data frame was
   * accepted by the realtime client's out-of-order guard). Heartbeats,
   * reconnect opens, and REST reseeds must NOT advance it — otherwise a
   * reconnect replay would render as a market move and the flash colour would
   * lie about price action. A transport blip therefore reads as a quiet tape
   * with a growing age, never as movement.
   */
  tickFlashKey: number | string | null;
  /** Backend truth flag (snapshot.tick_stale) — freezes the tape visually. */
  tickStale?: boolean;
}

/** Mid price from bid/ask for flash DIRECTION only (never displayed as a quote). */
function midPrice(bid: number | null, ask: number | null): number | null {
  if (bid === null || bid === undefined || ask === null || ask === undefined) return null;
  if (!Number.isFinite(bid) || !Number.isFinite(ask)) return null;
  return (bid + ask) / 2;
}

export function QuoteTape({ symbol, bid, ask, spread, priceDigits, dataAgeMs, tickFlashKey, tickStale }: QuoteTapeProps) {
  const digits = priceDigits ?? 2;
  const lastKeyRef = useRef<number | string | null>(tickFlashKey);
  const lastMidRef = useRef<number | null>(midPrice(bid, ask));
  const seqRef = useRef(0);
  const [flash, setFlash] = useState<{ seq: number; dir: "up" | "down" | "flat" } | null>(null);

  useEffect(() => {
    if (!flashKeyDiffers(tickFlashKey, lastKeyRef.current)) return;
    const mid = midPrice(bid, ask);
    const prevMid = lastMidRef.current;
    const dir: "up" | "down" | "flat" =
      mid === null || prevMid === null ? "flat" : mid > prevMid ? "up" : mid < prevMid ? "down" : "flat";
    lastKeyRef.current = tickFlashKey;
    lastMidRef.current = mid;
    seqRef.current += 1;
    setFlash({ seq: seqRef.current, dir });
    // bid/ask intentionally NOT deps: direction is sampled when the parent-
    // owned version key changes, which is the only legal moment to react.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tickFlashKey]);

  const flashClass = flash ? `flash-${flash.dir}` : "";
  // key= re-mounts the span so the CSS animation restarts on every advance.
  return (
    <div className={`ops-quote ${tickStale ? "stale" : ""}`} role="group" aria-label="Live quote tape">
      <span className="ops-quote-symbol">{symbol ?? "SYMBOL —"}</span>
      <span className="ops-quote-cell" key={flash ? `b${flash.seq}` : "b0"}>
        <span className="lab">BID</span>
        <span className={`val ${flashClass}`}>{formatPrice(bid, digits)}</span>
      </span>
      <span className="ops-quote-cell" key={flash ? `a${flash.seq}` : "a0"}>
        <span className="lab">ASK</span>
        <span className={`val ${flashClass}`}>{formatPrice(ask, digits)}</span>
      </span>
      <span className="ops-quote-cell">
        <span className="lab">SPREAD</span>
        <span className="val">{spread === null || spread === undefined ? "—" : `${formatNumber(spread)} pts`}</span>
      </span>
      <span className="ops-quote-age" title="age of the last accepted data frame (client wall-clock)">
        {tickStale ? "TICK STALE · " : ""}
        {formatTickAge(dataAgeMs)}
      </span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// FeedQualityChip
// ---------------------------------------------------------------------------

export interface FeedQualityChipProps {
  /** Transport state from the realtime client (the app's feed truth source). */
  state: ConnectionState;
  /** Wall-clock ms of the last accepted frame; null until the first one. */
  lastFrameAtMs: number | null;
  /** Data age (ms) computed upstream; null renders "—", never 0. */
  dataAgeMs: number | null;
  /**
   * Gap (seconds) between the two last accepted DATA frames — computed
   * upstream by the parent (it owns frame bookkeeping). null = not enough
   * frames yet. Exposed as detail text, never as a latency promise.
   */
  lastGapSec: number | null;
}

const FEED_WORD: Record<ConnectionState, string> = {
  connected: "LIVE",
  reconnecting: "RECONNECTING",
  disconnected: "OFFLINE",
  failed: "FEED FAILED",
};

export function FeedQualityChip({ state, lastFrameAtMs, dataAgeMs, lastGapSec }: FeedQualityChipProps) {
  const tone = state === "connected" ? "good" : state === "reconnecting" ? "warn" : "bad";
  return (
    <span
      className={`ops-feed-chip ${tone}`}
      title={`transport state: ${state} · last frame ${lastFrameAtMs !== null ? formatTime(lastFrameAtMs) : "never"} · frame gap ${
        lastGapSec === null ? "—" : `${lastGapSec.toFixed(1)}s`
      } (client-measured between frames; broker→server latency is NOT observable here)`}
    >
      <span className={`conn-dot ${state === "connected" ? "connected" : state === "reconnecting" ? "reconnecting" : "disconnected"}`} />
      <span className="ops-feed-word">{FEED_WORD[state]}</span>
      <span className="ops-feed-detail">
        {lastFrameAtMs !== null ? `frame ${formatTime(lastFrameAtMs)}` : "no frame yet"} · age {formatTickAge(dataAgeMs)}
      </span>
    </span>
  );
}

// ---------------------------------------------------------------------------
// Watermark
// ---------------------------------------------------------------------------

export interface WatermarkProps {
  /** Parent truth: backend marked the data stale (snapshot.is_stale / tick_stale). */
  stale: boolean;
  /** Parent truth: realtime transport is down (disconnected/failed). */
  feedDown: boolean;
}

/**
 * Subtle fixed overlay — never blocks interaction (pointer-events:none).
 * Word selection lives in opsChromeMath.watermarkWord (feedDown > stale);
 * this component adds no judgement of its own and hides entirely when both
 * flags are false.
 */
export function Watermark({ stale, feedDown }: WatermarkProps) {
  const word = watermarkWord({ stale, feedDown });
  if (word === null) return null;
  return (
    <div className={`ops-watermark ${feedDown ? "critical" : "warn"}`} role="status" aria-live="polite">
      {word}
    </div>
  );
}

// ---------------------------------------------------------------------------
// ErrorTriage
// ---------------------------------------------------------------------------

export interface ErrorTriageProps {
  message: string;
  requestId?: string | null;
  /** Backend/transport hint (ApiError.retryable). null = unknown. */
  retryable?: boolean | null;
  onRetry?: () => void;
  /** Optional context line (which endpoint/action failed). */
  context?: string;
}

/**
 * Failed-command card with the evidence an operator needs to file/triage:
 * the verbatim backend message, the request_id for log correlation, and a
 * Retry affordance that simply re-invokes whatever the parent wires (this
 * component never guesses at recovery beyond the provided callback).
 */
export function ErrorTriage({ message, requestId, retryable, onRetry, context }: ErrorTriageProps) {
  return (
    <div className="ops-triage" role="alert">
      <div className="ops-triage-head">
        <span className="ops-triage-glyph">⚠</span>
        <span>{context ?? "Request failed"}</span>
      </div>
      <div className="ops-triage-message">{message}</div>
      {requestId ? (
        <div className="ops-triage-rid inline-mono">request_id: {requestId}</div>
      ) : (
        <div className="ops-triage-rid muted">request_id: — (none returned by the backend)</div>
      )}
      {retryable === false && <div className="ops-triage-note">backend marked this failure non-retryable</div>}
      {onRetry && (
        <button className="btn small" onClick={onRetry}>
          Retry
        </button>
      )}
    </div>
  );
}
