/**
 * PURPOSE:  Trade-autopsy card grid for /api/intelligence/autopsies — each
 *           autopsy as a compact card (outcome, signed realized R, exit
 *           reason) with honest loading/empty/error states.
 * OWNER:    uiux-wave5-intel  (future edits to this file belong to this lane)
 * CONSUMES: a TanStack query result for /api/intelligence/autopsies
 *           (structurally typed via _shared/SectionState QueryLike),
 *           AutopsyRow declared fields only, primitives (Panel) + kit states.
 * PROVIDES: default AutopsyFeed — rendered inside IntelligencePage.
 * INVARIANTS: renders only the declared AutopsyRow fields (ticket,
 *           strategy_id, outcome, realized_r, exit_reason) — no timestamp or
 *           score is shown because the type carries none; realized-R color
 *           reuses the theme's existing pnl-pos/pnl-neg sign convention.
 * EXTEND:   extra verified fields go into the figures row; keep the states.
 */

import { Panel } from "@/components/primitives";
import { SectionState, type QueryLike } from "@/pages/_shared/SectionState";
import type { AutopsyRow } from "@/types/domain";

type AutopsyPayload = { available: boolean; autopsies?: AutopsyRow[] };

function AutopsyCard({ row }: { row: AutopsyRow }) {
  const r = typeof row.realized_r === "number" ? row.realized_r : null;
  return (
    <div className="itl-autopsy">
      <div className="itl-card__head">
        <span className="itl-type">trade autopsy</span>
        <span className="itl-ticket">#{String(row.ticket ?? "—")}</span>
      </div>
      <div className="itl-autopsy__figures">
        <span className="itl-fig">
          <span className="k">strategy</span>
          <span className="v">{row.strategy_id ?? "—"}</span>
        </span>
        <span className="itl-fig">
          <span className="k">outcome</span>
          <span className="v">{row.outcome ?? "—"}</span>
        </span>
        <span className="itl-fig">
          <span className="k">realized r</span>
          <span className={`v ${r === null ? "" : r >= 0 ? "pnl-pos" : "pnl-neg"}`}>
            {r === null ? "—" : r.toFixed(2)}
          </span>
        </span>
      </div>
      <div className="itl-autopsy__exit">
        <span className="k">exit</span> {row.exit_reason ?? "—"}
      </div>
    </div>
  );
}

export default function AutopsyFeed({ query }: { query: QueryLike<AutopsyPayload> }) {
  return (
    <Panel title="Recent trade autopsies (why trades won/lost)" tight>
      <SectionState
        query={query}
        skeletonRows={3}
        emptyWhen={(d) => !(d.available && (d.autopsies?.length ?? 0) > 0)}
        emptyMessage="No autopsies recorded."
        errorFallback="Autopsy endpoint failed."
      >
        {(d) => (
          <div className="itl-autopsies">
            {(d.autopsies ?? []).map((a, i) => (
              <AutopsyCard key={String(a.ticket ?? i)} row={a} />
            ))}
          </div>
        )}
      </SectionState>
    </Panel>
  );
}
