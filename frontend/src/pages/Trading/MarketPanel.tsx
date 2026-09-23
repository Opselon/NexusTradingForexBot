/**
 * PURPOSE:  Market / execution-state readout: symbol, bid / ask, spread, tick
 *           freshness, regime, AI proposal + block reason, proposal age.
 *           Extracted from the original TradingPage.
 * OWNER:    uiux-w6-trading  (future edits belong to this lane)
 * CONSUMES: EngineSnapshot (symbol, bid, ask, spread, price_digits, tick_stale,
 *           regime, ai_decision, ai_confidence, ai_reason, diagnostics),
 *           AgeNote, Panel, format* from lib/format.
 * PROVIDES: default MarketPanel component (props: snapshot).
 * INVARIANTS: reads snapshot fields only — no invented value, no meter.
 * EXTEND:   New quote fields extend the kv list below.
 */
import type { EngineSnapshot } from "@/types/domain";
import { AgeNote } from "@/pages/_shared/SectionState";
import { Panel } from "@/components/primitives";
import { formatNumber, formatPct, formatPrice } from "@/lib/format";

interface Props {
  snapshot: EngineSnapshot;
}

export default function MarketPanel({ snapshot }: Props) {
  return (
    <Panel
      title="Market / execution state"
      right={<AgeNote label="tick age" ageSec={snapshot.diagnostics.tick_age_sec} />}
    >
      <dl className="kv">
        <dt>symbol</dt>
        <dd>{snapshot.symbol ?? "—"}</dd>
        <dt>bid / ask</dt>
        <dd>{formatPrice(snapshot.bid, snapshot.price_digits ?? 2)} / {formatPrice(snapshot.ask, snapshot.price_digits ?? 2)}</dd>
        <dt>spread</dt>
        <dd>{snapshot.spread === null ? "—" : `${formatNumber(snapshot.spread)} pts`}</dd>
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
