/**
 * PURPOSE:  Structure visualisations for /alt/intelligence — variant="pools"
 *           vertical price ladder over LiquidityState.pools, variant="bands"
 *           mSLIE band map over MslieStatus.liquidity_map, plus the named
 *           RegimeEvidence tone rows for the regime panel.
 * OWNER:    ui/w2-lane-b (future edits to this file belong to lane B)
 * CONSUMES: the StructureVizProps slices of LiquidityState.pools
 *           {side,price,state,source,confirmed_at} (features/liquidity_runtime
 *           report()) and MslieStatus.liquidity_map = LiquidityZone.to_dict()
 *           {price,side,strength_score,timeframe,age_bars,number_of_tests,
 *           distance_from_price,probability_as_target,rank,sources}
 *           (mslie/models.py:149 via engine get_debug_status(); same mapping
 *           the Liquidity page uses — features/liquidity/model.ts toZoneRow),
 *           and RegimePayload.evidence ({regime, evidence, note}).
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

import { useMemo } from "react";
import { formatDateTime } from "@/lib/format";

/** Wiring contract (amended by the orchestrator at integration: the band
 *  shape now mirrors the REAL LiquidityZone.to_dict fields — see CONSUMES). */
export interface StructureVizProps {
  pools: Array<{
    side?: string | null;
    price?: number | null;
    state?: string | null;
    source?: string | null;
    confirmed_at?: string | null;
  }>;
  bands: Array<{
    price?: number | string | null;
    side?: string | null;
    timeframe?: string | null;
    strength_score?: number | string | null;
    number_of_tests?: number | string | null;
    distance_from_price?: number | string | null;
    probability_as_target?: number | string | null;
    rank?: string | null;
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

/** Tone ONLY restates the backend word itself; unrecognised -> "unknown".
 *  typeof guard: pool `state` originates from a PoolState IntEnum upstream, so
 *  a non-string runtime value must degrade to "unknown", never throw. */
function wordTone(v: string | null | undefined): Tone {
  if (typeof v !== "string" || v === "") return "unknown";
  const w = v.toUpperCase();
  if (TONE_GOOD.has(w)) return "good";
  if (TONE_WARN.has(w)) return "warn";
  if (TONE_BAD.has(w)) return "bad";
  if (TONE_NEUTRAL.has(w)) return "neutral";
  return "unknown";
}

/** BUY/SELL colour restates the side word (PositionSideBadge precedent).
 *  typeof guard: a non-string runtime side degrades to "unknown", never throws. */
function sideTone(side: string | null | undefined): Tone {
  const s = typeof side === "string" ? side.toUpperCase() : "";
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
  // Narrow the REAL LiquidityZone.to_dict keys only (no any, no invented fields).
  return (map ?? []).map((b) => ({
    price: pickNumStr(b, "price"),
    side: pickStr(b, "side"),
    timeframe: pickStr(b, "timeframe"),
    strength_score: pickNumStr(b, "strength_score"),
    number_of_tests: pickNumStr(b, "number_of_tests"),
    distance_from_price: pickNumStr(b, "distance_from_price"),
    probability_as_target: pickNumStr(b, "probability_as_target"),
    rank: pickStr(b, "rank"),
  }));
}

/** Pixel size of the point marker for a degenerate (low == high) band row. */
const POINT_PX = 3;

/**
 * variant="bands" — band map of the liquidity-map zones. A zone is a single
 * PRICE level, so each band renders as a POINT placed between the payload-
 * derived min(price) and max(price) only; the verbatim price, side/rank
 * words, number_of_tests/strength_score/probability_as_target raw values
 * render beside every band. Zones without a numeric price still render their
 * raw values — they simply carry no marker (no coerced scale, no zero-fill).
 */
function BandMap({ bands }: { bands: StructureVizProps["bands"] }) {
  if (bands.length === 0) {
    return (
      <p className="ixviz-empty">
        No liquidity-map bands in the payload — band map omitted. An empty backend list is reported as empty, never drawn as a chart.
      </p>
    );
  }

  // Scale endpoints come ONLY from the payload prices (no invented scale max).
  const prices = bands.map((b) => scaleNum(b.price)).filter((n): n is number => n !== null);
  const minPrice = prices.length > 0 ? Math.min(...prices) : null;
  const maxPrice = prices.length > 0 ? Math.max(...prices) : null;
  const span = minPrice !== null && maxPrice !== null ? maxPrice - minPrice : 0;

  /** Display-only point position on the payload min..max price scale. */
  const markerPct = (price: number): number | null => {
    if (minPrice === null || maxPrice === null) return null;
    if (span <= 0) return 50; // all prices equal — centred, still not a verdict
    return Math.min(100, Math.max(0, ((price - minPrice) / span) * 100));
  };

  return (
    <div className="ixviz ixviz-bands">
      <ul className="ixviz-rows">
        {bands.map((b, i) => {
          const price = scaleNum(b.price);
          const pct = price !== null ? markerPct(price) : null;
          return (
            <li className="ixviz-band-row" key={i}>
              {/* raw payload fields: liquidity_map[].price / .side / .rank */}
              <span className="ixviz-band-label">
                <span className="ixviz-range">{raw(b.price)}</span>
                <span className={`ixviz-chip is-${sideTone(b.side)}`}>{raw(b.side)}</span>
                <span className="ixviz-chip is-unknown">{raw(b.rank)}</span>
              </span>
              {/* marker = placement only; the raw price above is its value */}
              <span className="ixviz-track" aria-hidden="true">
                {pct !== null && <i className="ixviz-seg point" style={{ insetInlineStart: `calc(${pct}% - ${POINT_PX / 2}px)` }} />}
              </span>
              {/* raw payload fields — verbatim, no units, no rounding */}
              <span className="ixviz-band-meta">
                <span>
                  tests <b>{raw(b.number_of_tests)}</b>
                </span>
                <span>
                  strength <b>{raw(b.strength_score)}</b>
                </span>
                <span>
                  prob <b>{raw(b.probability_as_target)}</b>
                </span>
                <span className="ixviz-meta">
                  {raw(b.timeframe)} · dist {raw(b.distance_from_price)}
                </span>
              </span>
            </li>
          );
        })}
      </ul>
      <div className="ixviz-scale">
        <span>
          payload min/max scale: <b>{raw(minPrice)}</b> – <b>{raw(maxPrice)}</b>
        </span>
        <span>zone price placed on that scale (clamped); raw price printed per band</span>
      </div>
    </div>
  );
}

/**
 * Named regime-evidence component for the regime panel — the orchestrator may
 * wire `<RegimeEvidence evidence={regime?.evidence} />`. `evidence` is
 * RegimePayload.evidence (Record<string, unknown> | null, web/api_v1/market.py
 * market_regime): every payload entry renders RAW (scalars verbatim, objects
 * and arrays as the backend's own JSON); the row tone only restates a
 * whitelisted backend WORD in a string value — numbers, booleans and
 * unrecognised words stay untinted (never a frontend verdict). Null/empty
 * evidence is an honest empty note, never filled.
 */
export function RegimeEvidence({ evidence }: { evidence: Record<string, unknown> | null | undefined }) {
  // One memo per payload (NOT inside the map — a hook in a loop would vary
  // with entry count): the serialized text is computed only when the payload
  // reference changes, byte-identical to the previous inline expression.
  const rows = useMemo(
    () =>
      Object.entries(evidence ?? {}).map(([k, v]) => ({
        k,
        v,
        tone: (typeof v === "string" ? wordTone(v) : "neutral") as Tone,
        text:
          v === null || v === undefined
            ? "—" // missing field -> em dash, never zero-filled
            : typeof v === "object"
              ? (JSON.stringify(v) ?? "—") // raw payload serialization, no re-derivation
              : typeof v === "function" || typeof v === "symbol"
                ? "—"
                : String(v), // scalars verbatim (no rounding, no units)
      })),
    [evidence],
  );
  if (rows.length === 0) {
    return <p className="ixviz-empty">No regime evidence in the payload — shown empty, never inferred.</p>;
  }
  return (
    <dl className="ixviz ixviz-evidence">
      {rows.map(({ k, tone, text }) => (
        <div className={`ixviz-ev is-${tone}`} key={k}>
          {/* verbatim payload key — cited, never renamed or judged */}
          <dt className="ixviz-ev-k">{k}</dt>
          <dd className="ixviz-ev-v">{text}</dd>
        </div>
      ))}
    </dl>
  );
}

export default function StructureViz(props: StructureVizProps & { variant: "pools" | "bands" }) {
  if (props.variant === "pools") return <PoolLadder pools={props.pools} />;
  return <BandMap bands={props.bands} />;
}
