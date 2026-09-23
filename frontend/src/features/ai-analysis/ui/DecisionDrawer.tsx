/**
 * AI-Analysis — decision drawer (per-decision gates / evidence / explanation).
 * All three v1 sub-resources are fetched independently and fail independently.
 */

import { useQuery } from "@tanstack/react-query";
import { EmptyState, ErrorState, MetricCard, Panel, ProbBar, Skeleton } from "@/components/primitives";
import { ApiError } from "@/types/api";
import { formatDateTime, formatPrice } from "@/lib/format";
import { useI18n } from "@/stores/i18nStore";
import { Drawer, GateStepper, InfoRow, JsonBlock, StatusPill } from "../../research/ui/lane5Kit";
import { confidence01, str } from "../model";
import { aiAnalysisQueries } from "../useCases";

export default function DecisionDrawer({ decisionId, onClose }: { decisionId: string; onClose: () => void }) {
  const t = useI18n((s) => s.t);
  const detailQ = useQuery({
    queryKey: ["ai-analysis", "decision", decisionId],
    queryFn: ({ signal }) => aiAnalysisQueries.decisionDetail(decisionId, signal),
    retry: false,
  });
  const gatesQ = useQuery({
    queryKey: ["ai-analysis", "decision", decisionId, "gates"],
    queryFn: ({ signal }) => aiAnalysisQueries.decisionGates(decisionId, signal),
    retry: false,
  });
  const evidenceQ = useQuery({
    queryKey: ["ai-analysis", "decision", decisionId, "evidence"],
    queryFn: ({ signal }) => aiAnalysisQueries.decisionEvidence(decisionId, signal),
    retry: false,
  });
  const explainQ = useQuery({
    queryKey: ["ai-analysis", "decision", decisionId, "explanation"],
    queryFn: ({ signal }) => aiAnalysisQueries.decisionExplanation(decisionId, signal),
    retry: false,
  });

  const notFound = (e: unknown) => e instanceof ApiError && e.status === 404;
  const d = detailQ.data;

  return (
    <Drawer title={t("ai-analysis.decision.title", "Decision {id}…", { id: decisionId.slice(0, 16) })} onClose={onClose}>
      <div className="grid cols-3" style={{ marginBottom: 12 }}>
        <MetricCard label={t("ai-analysis.decision.action", "Action")} value={d?.action ?? "—"} tone={d?.action === "BUY" ? "pos" : d?.action === "SELL" ? "neg" : "dim"} />
        <MetricCard label={t("ai-analysis.decision.stage_blocked", "Stage / blocked_by")} value={d?.decision_stage ?? "—"} sub={d?.blocked_by ? t("ai-analysis.decision.blocked_by", "blocked by {b}", { b: d.blocked_by }) : undefined} />
        <MetricCard label={t("ai-analysis.decision.generated", "Generated")} value={<span className="tiny">{formatDateTime(d?.generated_at)}</span>} sub={t("ai-analysis.decision.reason", "reason {r}", { r: d?.reason_code ?? "—" })} />
      </div>

      {detailQ.isPending ? (
        <Skeleton count={3} />
      ) : d ? (
        <Panel title={t("ai-analysis.decision.levels", "Levels (recorded or none)")} tight>
          <dl className="kv" style={{ marginBottom: 10 }}>
            <InfoRow label={t("ai-analysis.decision.proposed_entry", "proposed entry")} value={formatPrice(d.proposed_entry, 2)} />
            <InfoRow label={t("ai-analysis.decision.stop_loss", "stop loss")} value={formatPrice(d.stop_loss, 2)} />
            <InfoRow label={t("ai-analysis.decision.take_profit", "take profit")} value={formatPrice(d.take_profit, 2)} />
            <InfoRow label={t("ai-analysis.decision.execution_mode", "execution mode")} value={d.execution_mode ?? "—"} />
            <InfoRow label={t("ai-analysis.decision.regime", "regime")} value={d.regime ?? "—"} />
          </dl>
          <ProbBar
            rows={[
              { label: t("ai-analysis.decision.p_confidence", "P (confidence)"), value: confidence01(d.confidence), tone: d.action === "BUY" ? "buy" : d.action === "SELL" ? "sell" : "flat" },
              { label: t("ai-analysis.card.before_filters", "before filters"), value: confidence01(d.confidence_before_filters), tone: "flat" },
              { label: t("ai-analysis.card.after_filters", "after filters"), value: confidence01(d.confidence_after_filters), tone: "flat" },
            ]}
          />
        </Panel>
      ) : notFound(detailQ.error) ? (
        <EmptyState message={t("ai-analysis.decision.not_found", "Decision not found in the ledger window.")} />
      ) : (
        <ErrorState
          message={detailQ.error instanceof Error ? detailQ.error.message : t("ai-analysis.decision.detail_unavailable", "detail unavailable")}
          onRetry={() => void detailQ.refetch()}
        />
      )}

      <div style={{ height: 12 }} />
      <Panel title={t("ai-analysis.decision.gate_trace", "Gate trace")} tight>
        {gatesQ.isPending ? (
          <Skeleton count={2} />
        ) : gatesQ.isError ? (
          <ErrorState
            message={gatesQ.error instanceof Error ? gatesQ.error.message : t("ai-analysis.decision.gates_unavailable", "gates unavailable")}
            onRetry={() => void gatesQ.refetch()}
          />
        ) : (
          (() => {
            const gates = gatesQ.data?.gates ?? [];
            if (gates.length === 0) return <EmptyState message={t("ai-analysis.decision.no_gate_trace", "No gate trace recorded for this decision.")} />;
            const pass = gates.filter((g) => g.passed).length;
            const fail = gates.length - pass;
            const firstFail = gates.find((g) => !g.passed);
            return (
              <>
                <div className="aa-gate-sum">
                  <span className="badge good">{t("ai-analysis.decision.pass_count", "✓ {n} pass", { n: pass })}</span>
                  <span className={`badge ${fail > 0 ? "bad" : ""}`}>{t("ai-analysis.decision.fail_count", "✕ {n} fail", { n: fail })}</span>
                  {firstFail && (
                    <span className="tiny aa-firstfail" title={str(firstFail.value) ?? ""}>
                      {t("ai-analysis.decision.first_failure", "first failure: {g}", { g: firstFail.gate })}
                    </span>
                  )}
                </div>
                <GateStepper
                  gates={gates.map((g) => ({
                    name: g.gate,
                    status: g.passed ? "PASS" : "FAIL",
                    reason: str(g.value) ?? t("ai-analysis.decision.no_value", "no value recorded"),
                  }))}
                />
              </>
            );
          })()
        )}
      </Panel>

      <div style={{ height: 12 }} />
      <Panel title={t("ai-analysis.decision.explanation", "Explanation (backend-generated)")} tight>
        {explainQ.isPending ? (
          <Skeleton />
        ) : explainQ.isError ? (
          <ErrorState
            message={explainQ.error instanceof Error ? explainQ.error.message : t("ai-analysis.decision.explanation_failed", "explanation endpoint failed")}
            onRetry={() => void explainQ.refetch()}
          />
        ) : (
          <div className="small">{explainQ.data?.explanation ?? "—"}</div>
        )}
      </Panel>

      <div style={{ height: 12 }} />
      <Panel title={t("ai-analysis.decision.evidence", "Raw evidence payload (sanitized)")} tight>
        {evidenceQ.isPending ? (
          <Skeleton count={3} />
        ) : evidenceQ.isError ? (
          <ErrorState
            message={evidenceQ.error instanceof Error ? evidenceQ.error.message : t("ai-analysis.decision.evidence_failed", "evidence endpoint failed")}
            onRetry={() => void evidenceQ.refetch()}
          />
        ) : (
          <JsonBlock value={evidenceQ.data?.evidence} maxChars={6000} />
        )}
      </Panel>
      <div className="tiny muted" style={{ marginTop: 8 }}>
        {t("ai-analysis.decision.pills_note_pre", "status pills mirror backend values only;")} <StatusPill status={d?.execution_mode ?? "—"} />{" "}
        {t("ai-analysis.decision.pills_note_post", "is the recorded execution mode.")}
      </div>
    </Drawer>
  );
}
