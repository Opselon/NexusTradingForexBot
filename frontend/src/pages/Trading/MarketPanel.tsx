/**
 * PURPOSE:  Market / execution-state readout: bid / ask / spread / tick freshness
           / regime / AI proposal + block reason — restyled as a quote-board
           strip with a small spread meter whose width is plain arithmetic on
           the two backend prices.
 * OWNER:    uiux-w6-trading  (future edits belong to this lane)
 * CONSUMES: EngineSnapshot (symbol, bid, ask, spread, price_digits, tick_stale,
           regime, ai_decision, ai_confidence, ai_reason, diagnostics),
           AgeNote, Panel, format* from lib/format.
 * PROVIDES: default MarketPanel component (props: snapshot).
 * INVARIANTS: reads snapshot fields only; the spread bar visualizes
             max(0, ask-bid) as a share of the visible quote width and never
             asserts a configured limit — missing prices render indeterminate.
 * EXTEND:   New quote fields extend the kv list; the meter must stay arithmetic
          over two backend values.
 */

import type { EngineSnapshot } from "@/types/domain";
import { AgeNote } from "@/pages/_shared/SectionState";
import { Panel } from "@/components/primitives";
import { formatNumber, formatPct, formatPrice } from "@/lib/format";

interface Props {
  snapshot: EngineSnapshot;
}

/** Spread bar fraction: |ask−bid| vs |ask|, clamped 0..1; null when either price
 *  is missing (no invented width, no implied limit). */
function spreadFraction(snapshot: EngineSnapshot): number | null {
  const bid = snapshot.bid;
  const ask = snapshot.ask;
  if (bid === null || ask === null) return null;
  const base = Math.abs(ask);
  if (!Number.isFinite(base) || base <= 0) return null;
  const raw = (Math.abs(ask - bid) / base) * 100;
  if (!Number.isFinite(raw)) return null;
  return Math.max(0, Math.min(1, raw));
}

export default function MarketPanel({ snapshot }: Props) {
  const digits = snapshot.price_digits ?? 2;
  const frac = spreadFraction(snapshot);

  return (
    <Panel
      title="Market / execution state"
      right={<AgeNote label="tick age" ageSec={snapshot.diagnostics.tick_age_sec} />}
    >
      <dl className="kv trd-quote">
        <dt>symbol</dt>
        <dd>{snapshot.symbol ?? "—"}</dd>
        <dt>bid / ask</dt>
        <dd className="trd-quote__px">
          <span className="trd-quote__bid">{formatPrice(snapshot.bid, digits)}</span>
          <span className="trd-quote__sep" aria-hidden="true">/</span>
          <span className="trd-quote__ask">{formatPrice(snapshot.ask, digits)}</span>
        </dd>
        <dt>spread</dt>
        <dd className="trd-spread">
          <span className="trd-spread__val">{snapshot.spread === null ? "—" : `${formatNumber(snapshot.spread)} pts`}</span>
          <span
            className="trd-spread__track"
            role="img"
            aria-label={snapshot.spread === null ? "spread unknown" : `spread ${snapshot.spread} points`}
          >
            <i
              className="trd-spread__fill"
              style={{ inlineSize: frac === null ? "0%" : `${frac}%` }}
              data-known={frac === null ? "false" : "true"}
            />
          </span>
        </dd>
        <dt>tick stale</dt>
        <dd>{snapshot.tick_stale ? <span className="badge warn">STALE</span> : <span className="badge good">FRESH</span>}</dd>
        <dt>regime</dt>
        <dd>{snapshot.regime ?? "—"}</dd>
        <dt>AI proposal</dt>
        <dd>{snapshot.ai_decision ?? "—"} {snapshot.ai_confidence !== null ? `(${formatPct(snapshot.ai_confidence * 100, 1)})` : ""}</dd>
        <dt>proposal blocked by</dt>
        <dd>{snapshot.ai_reason ?? "—"}</dd>
        <dt>proposal age</dt>
        <dd>{snapshot.diagnostics.proposal_age_sec === null ? "—" : `${snapshot.diagnostics.proposal_age_sec.toFixed(1)}s`}</dd>
      </dl>
    </Panel>
  );
}
