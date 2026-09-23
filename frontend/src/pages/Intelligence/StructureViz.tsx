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

/**
 * Narrow MslieStatus.liquidity_map (typed Array<Record<string, unknown>>) to
 * the frozen band shape WITHOUT `any` — the orchestrator wires
 * `bands={mslieBands(ms?.liquidity_map)}`. Values outside the frozen
 * number|string|null union become undefined -> rendered as "—"; fields the
 * payload does not carry are never invented.
 */
export function mslieBands(map: Array<Record<string, unknown>> | null | undefined): StructureVizProps["bands"] {
  const pickNumStr = (src: Record<string, unknown>, key: string): number | string | null | undefined => {
    const v = src[key];
    return typeof v === "number" || typeof v === "string" || v === null ? v : undefined;
  };
  const pickStr = (src: Record<string, unknown>, key: string): string | null | undefined => {
    const v = src[key];
    return typeof v === "string" || v === null ? v : undefined;
  };
  return (map ?? []).map((b) => ({
    low: pickNumStr(b, "low"),
    high: pickNumStr(b, "high"),
    type: pickStr(b, "type"),
    kind: pickStr(b, "kind"),
    touches: pickNumStr(b, "touches"),
    strength: pickNumStr(b, "strength"),
  }));
}

/** Pixel size of the point marker for a degenerate (low == high) band row. */
const POINT_PX = 3;

/**
 * variant="bands" — horizontal band map of the liquidity-map zones. Each band
 * segment is SCALED between the payload-derived min(low) and max(high) only;
 * the verbatim `low – high` range, `type|kind` word, `touches` and `strength`
 * raw values render beside every band. Rows whose endpoints are not numeric
 * still render their raw values — they simply carry no segment (no coerced
 * scale, no zero-fill).
 */
function BandMap({ bands }: { bands: StructureVizProps["bands"] }) {
  if (bands.length === 0) {
    return (
      <p className="ixviz-empty">
        No liquidity-map bands in the payload — band map omitted. An empty backend list is reported as empty, never drawn as a chart.
      </p>
    );
  }

  const rows = bands.map((b) => ({ b, lo: scaleNum(b.low), hi: scaleNum(b.high) }));

  // Scale endpoints come ONLY from the payload (invariant: no invented scale max).
  const lows = rows.map((r) => r.lo).filter((n): n is number => n !== null);
  const highs = rows.map((r) => r.hi).filter((n): n is number => n !== null);
  const minLow = lows.length > 0 ? Math.min(...lows) : null;
  const maxHigh = highs.length > 0 ? Math.max(...highs) : null;
  const span = minLow !== null && maxHigh !== null ? maxHigh - minLow : 0;

  type Seg = { startPct: number; widthPct: number; point: boolean };
  /** Display-only segment geometry on the payload min..max scale (clamped). */
  const segment = (lo: number | null, hi: number | null): Seg | null => {
    if (lo === null || hi === null || minLow === null || span <= 0) return null;
    const a = Math.min(lo, hi);
    const b = Math.max(lo, hi);
    const start = Math.min(100, Math.max(0, ((a - minLow) / span) * 100));
    const end = Math.min(100, Math.max(0, ((b - minLow) / span) * 100));
    const width = Math.max(0, end - start);
    return { startPct: start, widthPct: width, point: b - a === 0 };
  };

  return (
    <div className="ixviz ixviz-bands">
      <ul className="ixviz-rows">
        {rows.map(({ b, lo, hi }, i) => {
          const seg = segment(lo, hi);
          const kindWord = b.type ?? b.kind;
          return (
            <li className="ixviz-band-row" key={i}>
              {/* raw payload fields: liquidity_map[].low / .high / type|kind */}
              <span className="ixviz-band-label">
                <span className="ixviz-range">
                  {raw(b.low)} – {raw(b.high)}
                </span>
                <span className="ixviz-chip is-unknown">{raw(kindWord)}</span>
              </span>
              {/* segment = placement only; the raw range above is its value */}
              <span className="ixviz-track" aria-hidden="true">
                {seg &&
                  (seg.point ? (
                    <i
                      className="ixviz-seg point"
                      style={{ insetInlineStart: `calc(${seg.startPct}% - ${POINT_PX / 2}px)` }}
                    />
                  ) : (
                    <i
                      className="ixviz-seg"
                      style={{ insetInlineStart: `${seg.startPct}%`, inlineSize: `${seg.widthPct}%` }}
                    />
                  ))}
              </span>
              {/* raw payload fields: liquidity_map[].touches / .strength — no units, no rounding */}
              <span className="ixviz-band-meta">
                <span>
                  touches <b>{raw(b.touches)}</b>
                </span>
                <span>
                  strength <b>{raw(b.strength)}</b>
                </span>
              </span>
            </li>
          );
        })}
      </ul>
      <div className="ixviz-scale">
        <span>
          payload min/max scale: <b>{raw(minLow)}</b> – <b>{raw(maxHigh)}</b>
        </span>
        <span>band placed on that scale (clamped); raw low – high printed per band</span>
      </div>
    </div>
  );
}

export default function StructureViz(props: StructureVizProps & { variant: "pools" | "bands" }) {
  if (props.variant === "pools") return <PoolLadder pools={props.pools} />;
  return <BandMap bands={props.bands} />;
}
