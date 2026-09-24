/**
 * PredictionsTable — React port of legacy updatePredictionsTable (app.js
 * ~L5684): the real audit_signals model-decision ledger on the canonical
 * snapshot (server.py: `predictions` = last 40 immutable per-M1 decisions
 * with actual softmax probabilities; empty DB → explicit waiting state).
 *
 * Legacy semantics preserved:
 *  - time · action (bold) · confidence (1-decimal %) · regime (truncated) ·
 *    reason chip (truncated) — plus the NT/B/S probability values the legacy
 *    row carried in its title, promoted here to real per-class prob bars
 *    (ProbBar primitive = display of backend values only).
 *  - the legacy "accuracy" counters were literal zeros (outcomes are not
 *    evaluated in this payload) — instead of rendering a fake 0% bar, the
 *    panel states that honestly.
 *  - null probability → "—"; nothing is interpolated, nothing is dropped in
 *    silence (rows come through in backend order).
 */

import { memo } from "react";
import type { PredictionRow } from "@/types/domain";
import { EmptyState, Panel, ProbBar } from "@/components/primitives";
import { formatPct } from "@/lib/format";
import { useI18n } from "@/stores/i18nStore";
import "./market-console.css";

/**
 * React.memo: the 40-row ledger array is reference-stable between the 1s
 * dashboard clock ticks (it only changes when the socket delivers a new
 * snapshot), so the whole 12-row table skips re-render until the data does.
 */
export const PredictionsTable = memo(function PredictionsTable({
  predictions,
  limit = 12,
}: {
  predictions: PredictionRow[];
  limit?: number;
}) {
  const t = useI18n((s) => s.t);
  return (
    <Panel
      title={t("dash.preds.title", "Recent model decisions ({n})", { n: predictions.length })}
      subtitle={t("dash.preds.subtitle", "real audit_signals rows from the ledger — never fabricated")}
      right={<span className="timestamp-note">{t("dash.preds.order_note", "backend order, newest first")}</span>}
    >
      {predictions.length === 0 ? (
        <EmptyState
          message={t("dash.preds.empty", "No AI predictions recorded yet.")}
          hint={t("dash.preds.empty_hint", "audit_signals is empty — waiting for live engine decisions. Not rendered as zeros.")}
        />
      ) : (
        <div className="mc-preds">
          {predictions.slice(0, limit).map((p, i) => {
            const probs = p.probabilities ?? { no_trade: null, buy: null, sell: null };
            const hasProbs = probs.no_trade !== null || probs.buy !== null || probs.sell !== null;
            const conf = typeof p.confidence === "number" ? formatPct(p.confidence * 100, 1) : "--";
            const action = (p.action ?? "—").toUpperCase();
            const tone = action.includes("BUY") ? "buy" : action.includes("SELL") ? "sell" : "flat";
            return (
              <div className="mc-pred" key={p.request_id ?? `${p.time}-${i}`} title={hasProbs ? `NT ${pct0(probs.no_trade)}% B ${pct0(probs.buy)}% S ${pct0(probs.sell)}% ${p.reason ?? ""}` : (p.reason ?? "")}>
                <div className="mc-pred__head">
                  <span className="t">{(p.time ?? "—").replace("T", " ")}</span>
                  <span className={`a a--${tone}`}>{p.action ?? "—"}</span>
                  <span className="c">{conf}</span>
                  <span className="r">{(p.regime ?? "—").slice(0, 14)}</span>
                  {p.reason ? <span className="rsn" title={p.reason}>{p.reason.slice(0, 18)}</span> : null}
                </div>
                {hasProbs ? (
                  <ProbBar
                    rows={[
                      { label: "P(NO_TRADE)", value: probs.no_trade, tone: "flat" },
                      { label: "P(BUY)", value: probs.buy, tone: "buy" },
                      { label: "P(SELL)", value: probs.sell, tone: "sell" },
                    ]}
                  />
                ) : (
                  <div className="mc-pred__noprobs">
                    {t("dash.preds.noprobs", "softmax probabilities not sent for this row — shown as unknown, not zeros")}
                  </div>
                )}
              </div>
            );
          })}
          {predictions.length > limit && (
            <div className="mc-pred__more muted">
              {t("dash.preds.more", "+{o} older rows on the snapshot (first {l} shown)", {
                o: predictions.length - limit,
                l: limit,
              })}
            </div>
          )}
          <div className="mc-pred__note">
            {t(
              "dash.preds.note",
              "Outcome accuracy is not evaluated in this payload (the legacy ledger carried literal 0/0) — no fake accuracy bar is rendered.",
            )}
          </div>
        </div>
      )}
    </Panel>
  );
});

function pct0(v: number | null): string {
  return v === null ? "--" : (v * 100).toFixed(0);
}
