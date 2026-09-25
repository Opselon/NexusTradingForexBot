/**
 * PURPOSE:  Trade-autopsy card grid for the autopsies query — pro cards with
 *           a VERBATIM outcome badge (tone only restates the backend word),
 *           raw ticket/strategy/realized-r/exit-reason figures, an outcome-
 *           toned start border, endpoint provenance caption, and honest
 *           loading/empty/error states.
 * OWNER:    ui/w2-lane-c  (future edits to this file belong to lane C)
 * CONSUMES: a TanStack query result for /api/intelligence/autopsies
 *           (structurally typed via _shared/SectionState QueryLike),
 *           AutopsyRow declared fields only, primitives (Panel) + kit states,
 *           ./signalBits (WordBadge, outcomeTone), ./feed.css (`itl-*`).
 * PROVIDES: default AutopsyFeed — rendered inside IntelligencePage.
 * INVARIANTS: renders only the declared AutopsyRow fields (ticket,
 *           strategy_id, outcome, realized_r, exit_reason) — no timestamp or
 *           score is shown because the type carries none; every value is the
 *           raw payload string (missing → "—"); the outcome badge prints the
 *           backend word verbatim (WIN/LOSS/BREAKEVEN …) and its tone/border
 *           only restate that word — no verdict is computed here;
 *           realized-R color reuses the theme's pnl-pos/pnl-neg sign
 *           convention; '/api/intelligence/autopsies' is the static provenance text — the
 *           the lane brief; the honest empty state is unchanged.
 * EXTEND:   extra verified fields go into the figures row; keep the states.
 */

import { Panel } from "@/components/primitives";
import { SectionState, type QueryLike } from "@/pages/_shared/SectionState";
import type { AutopsyRow } from "@/types/domain";
import { WordBadge, outcomeTone } from "./signalBits";
import { useI18n } from "@/stores/i18nStore";

type AutopsyPayload = { available: boolean; autopsies?: AutopsyRow[] };

/** Raw payload text with the kit's honest missing marker (never zero-filled). */
function raw(value: string | number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  const s = String(value).trim();
  return s ? s : "—";
}

function AutopsyCard({ row, index }: { row: AutopsyRow; index: number }) {
  const t = useI18n((s) => s.t);
  const r = typeof row.realized_r === "number" ? row.realized_r : null;
  const tone = outcomeTone(row.outcome);
  const outcomeRaw = row.outcome === null || row.outcome === undefined ? "" : String(row.outcome).trim();
  return (
    // tone class only re-words the backend outcome; index → entry stagger
    <div
      className={`itl-autopsy itl-autopsy--tone-${tone}`}
      style={{ animationDelay: `${Math.min(index, 8) * 30}ms` }}
    >
      <div className="itl-card__head">
        <span className="itl-type">{t("intelligence.card.autopsy", "trade autopsy")}</span>
        <WordBadge
          word={row.outcome}
          fallback="—"
          tone={tone}
          title={outcomeRaw ? t("intelligence.autopsy.outcome_verb", "outcome (verbatim): {v}", { v: outcomeRaw }) : t("intelligence.autopsy.no_outcome", "backend sent no outcome")}
        />
        <span className="itl-ticket" title={t("intelligence.autopsy.ticket_title", "ticket (raw payload value)")}>
          #{raw(row.ticket)}
        </span>
      </div>
      <div className="itl-autopsy__figures">
        <span className="itl-fig" title={t("intelligence.autopsy.strategy_title", "strategy_id (raw payload value)")}>
          <span className="k">{t("intelligence.th.strategy", "strategy")}</span>
          <span className="v">{raw(row.strategy_id)}</span>
        </span>
        <span
          className="itl-fig"
          title={r === null ? t("intelligence.autopsy.realized_missing", "realized_r missing in payload") : t("intelligence.autopsy.realized_title", "realized_r (raw payload value, sign-coloured)")}
        >
          <span className="k">{t("intelligence.th.realized_r", "realized r")}</span>
          <span className={`v ${r === null ? "" : r >= 0 ? "pnl-pos" : "pnl-neg"}`}>{raw(row.realized_r)}</span>
        </span>
      </div>
      <div
        className="itl-autopsy__exit"
        title={row.exit_reason ? t("intelligence.autopsy.exit_title", "exit_reason (raw payload value)") : t("intelligence.autopsy.no_exit", "backend sent no exit_reason")}
      >
        <span className="k">{t("intelligence.autopsy.exit_label", "exit")}</span> {raw(row.exit_reason)}
      </div>
    </div>
  );
}

export default function AutopsyFeed({ query }: { query: QueryLike<AutopsyPayload> }) {
  const t = useI18n((s) => s.t);
  return (
    <Panel
      title={t("intelligence.panel.autopsies", "Recent trade autopsies (why trades won/lost)")}
      tight
      right={
        <span
          className="itl-epcap"
          title={t("intelligence.feed.provenance_title", "static provenance — every figure below is loaded from this endpoint")}
        >
          /api/intelligence/autopsies
        </span>
      }
    >
      <SectionState
        query={query}
        skeletonRows={3}
        emptyWhen={(d) => !(d.available && (d.autopsies?.length ?? 0) > 0)}
        emptyMessage={t("intelligence.autopsy.empty", "No autopsies recorded.")}
        errorFallback={t("intelligence.error.autopsy", "Autopsy endpoint failed.")}
      >
        {(d) => (
          <div className="itl-autopsies">
            {(d.autopsies ?? []).map((a, i) => (
              <AutopsyCard key={String(a.ticket ?? i)} row={a} index={i} />
            ))}
          </div>
        )}
      </SectionState>
    </Panel>
  );
}
