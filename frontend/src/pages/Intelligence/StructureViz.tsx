/**
 * PURPOSE:  Structure visualisations for /alt/intelligence — variant="pools"
 *           vertical price ladder over LiquidityState.pools, variant="bands"
 *           mSLIE band map over MslieStatus.liquidity_map, plus the named
 *           RegimeEvidence tone rows for the regime panel.
 * OWNER:    ui/w2-lane-b (future edits to this file belong to lane B)
 * CONSUMES: the frozen StructureVizProps slices of LiquidityState.pools
 *           {side,price,state,source,confirmed_at} and MslieStatus.liquidity_map
 *           {low,high,type|kind,touches,strength} (pages/_shared/contracts.ts;
 *           producers features/liquidity_runtime.py report() and mslie engine
 *           get_debug_status()), and RegimePayload.evidence
 *           (web/api_v1/market.py market_regime -> {regime, evidence, note}).
 * PROVIDES: default StructureViz (props frozen by the wave-2 lane contract;
 *           the orchestrator wires it — this file never edits the page) and
 *           the named RegimeEvidence; all styling lives in ./structureViz.css
 *           (.ixviz-*), imported by IntelligencePage — no CSS import here.
 * INVARIANTS: BACKEND-GUARANTEE — every displayed figure traces to a payload
 *           field (cited in comments); bars/markers only SCALE/PLACE payload
 *           numbers and the RAW value always renders beside them; scale
 *           endpoints come from payload min/max only (clamped for display);
 *           missing -> "—", never zero-filled; tone chips only restate a
 *           backend word (whitelisted — an unknown word stays neutral, never
 *           guessed); no units, no thresholds, no frontend verdicts; empty
 *           array -> honest empty note, never a fake chart; only fields that
 *           exist in the frozen prop types are rendered (no `any`, no
 *           ts-ignore, no ts-expect-error).
 * EXTEND:   new structure visuals = new branch/helpers here + additive
 *           .ixviz-* rules in ./structureViz.css.
 */

import { formatDateTime } from "@/lib/format";

/** FROZEN wave-2 wiring contract — do not widen or rename (intel_wave2_contract.md). */
export interface StructureVizProps {
  pools: Array<{
    side?: string | null;
    price?: number | null;
    state?: string | null;
    source?: string | null;
    confirmed_at?: string | null;
  }>;
  bands: Array<{
    low?: number | string | null;
    high?: number | string | null;
    type?: string | null;
    kind?: string | null;
    touches?: number | string | null;
    strength?: number | string | null;
  }>;
}

/** Badge tone — a COLOUR CODE for a restated backend word, never a verdict. */
type Tone = "good" | "warn" | "bad" | "neutral" | "unknown";

/**
 * Trusted backend-word whitelist (same philosophy as primitives StatusBadge:
 * backend status strings are trusted; anything unrecognised renders UNKNOWN,
 * never guessed). Pool lifecycle words come from features/liquidity_engine.py
 * PoolState (CANDIDATE/CONFIRMED/APPROACHING/TOUCHED/SWEPT/RECLAIMED/
 * DISPLACED/INVALIDATED); governor words from features/liquidity_runtime.py.
 */
const TONE_GOOD = new Set([
  "READY", "IDLE", "PASS", "CONNECTED", "ACTIVE", "OK", "RUNNING", "NORMAL",
  "VALID", "CONFIRMED", "FRESH", "CLEAN", "RECLAIMED", "AVAILABLE", "SUCCESS",
]);
const TONE_WARN = new Set([
  "WARMING_UP", "STALE", "STALE_CACHE", "DEGRADED", "CONNECTING", "ELEVATED",
  "WARNING", "WAITING_TICK", "PENDING", "CANDIDATE", "APPROACHING",
  "TOUCHED", "DIRTY", "NOT_RUN", "NOT_ACTIVE",
]);
const TONE_BAD = new Set([
  "ERROR", "DISCONNECTED", "UNAVAILABLE", "FAILED", "BLOCKED", "INVALID",
  "INVALIDATED", "HALTED", "BREAKING", "CONFLICTED", "HIGH_IMPACT",
]);
const TONE_NEUTRAL = new Set([
  "STOPPED", "UNKNOWN", "DISABLED", "SWEPT", "DISPLACED", "HOLD", "NEUTRAL",
]);

/** Tone ONLY restates the backend word itself; unrecognised -> "unknown". */
function wordTone(v: string | null | undefined): Tone {
  if (v === null || v === undefined || v === "") return "unknown";
  const w = v.toUpperCase();
  if (TONE_GOOD.has(w)) return "good";
  if (TONE_WARN.has(w)) return "warn";
  if (TONE_BAD.has(w)) return "bad";
  if (TONE_NEUTRAL.has(w)) return "neutral";
  return "unknown";
}

/** BUY/SELL colour restates the side word (PositionSideBadge precedent). */
function sideTone(side: string | null | undefined): Tone {
  const s = (side ?? "").toUpperCase();
  if (s.includes("BUY")) return "good";
  if (s.includes("SELL")) return "bad";
  return "unknown";
}

/** Payload value usable for SCALING only — null when not numeric, never 0. */
function scaleNum(v: number | string | null | undefined): number | null {
  if (v === null || v === undefined || v === "") return null;
  const n = typeof v === "number" ? v : Number(v);
  return Number.isFinite(n) ? n : null;
}

/** Raw scalar render — the exact backend value; "—" for missing (never 0). */
function raw(v: number | string | null | undefined): string {
  return v === null || v === undefined ? "—" : String(v);
}

/**
 * variant="pools" — vertical price ladder of the order pools. Rows are
 * SORTED by the backend `price` (payload field LiquidityState.pools[].price):
 * high price at the top of the ladder; rows without a price sink to the end
 * and render "—" with no marker (a missing price is never positioned).
 */
function PoolLadder({ pools }: { pools: StructureVizProps["pools"] }) {
  if (pools.length === 0) {
    return (
      <p className="ixviz-empty">
        No order pools in the payload — price ladder omitted. An empty backend list is reported as empty, never drawn as a chart.
      </p>
    );
  }

  // Scale endpoints come ONLY from the payload (invariant: no invented scale max).
  const prices = pools.map((p) => scaleNum(p.price)).filter((n): n is number => n !== null);
  const minPrice = prices.length > 0 ? Math.min(...prices) : null;
  const maxPrice = prices.length > 0 ? Math.max(...prices) : null;
  const span = minPrice !== null && maxPrice !== null ? maxPrice - minPrice : 0;

  /** Display-only marker position on the payload min..max scale, clamped 0..100. */
  const markerPct = (price: number): number => {
    if (minPrice === null || maxPrice === null || span <= 0) return 0;
    return Math.min(100, Math.max(0, ((price - minPrice) / span) * 100));
  };

  const ordered = [...pools].sort((a, b) => {
    const pa = scaleNum(a.price);
    const pb = scaleNum(b.price);
    if (pa === null && pb === null) return 0;
    if (pa === null) return 1; // unpriced rows sink — never ordered by guess
    if (pb === null) return -1;
    return pb - pa;
  });

  return (
    <div className="ixviz ixviz-ladder">
      <ul className="ixviz-rows">
        {ordered.map((p, i) => {
          const price = scaleNum(p.price);
          const sideCls = `is-${sideTone(p.side)}`;
          return (
            <li className="ixviz-row" key={i}>
              {/* raw payload field: pools[].price — printed verbatim beside its marker */}
              <span className="ixviz-price">{price === null ? "—" : String(price)}</span>
              {/* position only (payload min..max); aria-hidden: the raw price above carries the value */}
              <span className="ixviz-rail" aria-hidden="true">
                {price !== null && (
                  <i className={`ixviz-dot ${sideCls}`} style={{ insetInlineStart: `calc(${markerPct(price)}% - 4px)` }} />
                )}
              </span>
              {/* payload fields: pools[].side / .state — verbatim words, tone restates the word */}
              <span className={`ixviz-chip ${sideCls}`}>{raw(p.side)}</span>
              <span className={`ixviz-chip is-${wordTone(p.state)}`}>{raw(p.state)}</span>
              <span className="ixviz-meta">{raw(p.source)}</span>
              <span className="ixviz-meta">{p.confirmed_at ? formatDateTime(p.confirmed_at) : "—"}</span>
            </li>
          );
        })}
      </ul>
      <div className="ixviz-scale">
        <span>
          payload min/max scale: <b>{raw(minPrice)}</b> – <b>{raw(maxPrice)}</b>
        </span>
        <span>marker = price placed on that scale (clamped); raw price printed per row</span>
      </div>
    </div>
  );
}

export default function StructureViz(props: StructureVizProps & { variant: "pools" | "bands" }) {
  if (props.variant === "pools") return <PoolLadder pools={props.pools} />;
  // variant="bands" (BandMap) is added in the next lane-B commit.
  return null;
}
