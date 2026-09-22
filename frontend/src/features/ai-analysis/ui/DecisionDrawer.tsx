/**
 * AI-Analysis — decision drawer (per-decision gates / evidence / explanation).
 * All three v1 sub-resources are fetched independently and fail independently.
 */

import { useQuery } from "@tanstack/react-query";
import { EmptyState, ErrorState, MetricCard, Panel, ProbBar, Skeleton } from "@/components/primitives";
import { ApiError } from "@/types/api";
import { formatDateTime, formatPrice } from "@/lib/format";
import { Drawer, GateStepper, InfoRow, JsonBlock, StatusPill } from "../../research/ui/lane5Kit";
import { confidence01, str } from "../model";
import { aiAnalysisQueries } from "../useCases";

export default function DecisionDrawer({ decisionId, onClose }: { decisionId: string; onClose: () => void }) {
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
    <Drawer title={`Decision ${decisionId.slice(0, 16)}…`} onClose={onClose}>
      <div className="grid cols-3" style={{ marginBottom: 12 }}>
        <MetricCard label="Action" value={d?.action ?? "—"} tone={d?.action === "BUY" ? "pos" : d?.action === "SELL" ? "neg" : "dim"} />
        <MetricCard label="Stage / blocked_by" value={d?.decision_stage ?? "—"} sub={d?.blocked_by ? `blocked by ${d.blocked_by}` : undefined} />
        <MetricCard label="Generated" value={<span className="tiny">{formatDateTime(d?.generated_at)}</span>} sub={`reason ${d?.reason_code ?? "—"}`} />
      </div>

      {detailQ.isPending ? (
        <Skeleton count={3} />
      ) : d ? (
        <Panel title="Levels (recorded or none)" tight>
          <dl className="kv" style={{ marginBottom: 10 }}>
            <InfoRow label="proposed entry" value={formatPrice(d.proposed_entry, 2)} />
            <InfoRow label="stop loss" value={formatPrice(d.stop_loss, 2)} />
            <InfoRow label="take profit" value={formatPrice(d.take_profit, 2)} />
            <InfoRow label="execution mode" value={d.execution_mode ?? "—"} />
            <InfoRow label="regime" value={d.regime ?? "—"} />
          </dl>
          <ProbBar
            rows={[
              { label: "P (confidence)", value: confidence01(d.confidence), tone: d.action === "BUY" ? "buy" : d.action === "SELL" ? "sell" : "flat" },
              { label: "before filters", value: confidence01(d.confidence_before_filters), tone: "flat" },
              { label: "after filters", value: confidence01(d.confidence_after_filters), tone: "flat" },
            ]}
          />
        </Panel>
      ) : notFound(detailQ.error) ? (
        <EmptyState message="Decision not found in the ledger window." />
      ) : (
        <ErrorState
          message={detailQ.error instanceof Error ? detailQ.error.message : "detail unavailable"}
          onRetry={() => void detailQ.refetch()}
        />
      )}

      <div style={{ height: 12 }} />
      <Panel title="Gate trace" tight>
        {gatesQ.isPending ? (
          <Skeleton count={2} />
        ) : gatesQ.isError ? (
          <ErrorState
            message={gatesQ.error instanceof Error ? gatesQ.error.message : "gates unavailable"}
            onRetry={() => void gatesQ.refetch()}
          />
        ) : (
          <GateStepper
            gates={(gatesQ.data?.gates ?? []).map((g) => ({
              name: g.gate,
              status: g.passed ? "PASS" : "FAIL",
              reason: str(g.value) ?? "no value recorded",
            }))}
          />
        )}
      </Panel>

      <div style={{ height: 12 }} />
      <Panel title="Explanation (backend-generated)" tight>
        {explainQ.isPending ? (
          <Skeleton />
        ) : explainQ.isError ? (
          <ErrorState
            message={explainQ.error instanceof Error ? explainQ.error.message : "explanation endpoint failed"}
            onRetry={() => void explainQ.refetch()}
          />
        ) : (
          <div className="small">{explainQ.data?.explanation ?? "—"}</div>
        )}
      </Panel>

      <div style={{ height: 12 }} />
      <Panel title="Raw evidence payload (sanitized)" tight>
        {evidenceQ.isPending ? (
          <Skeleton count={3} />
        ) : evidenceQ.isError ? (
          <ErrorState
            message={evidenceQ.error instanceof Error ? evidenceQ.error.message : "evidence endpoint failed"}
            onRetry={() => void evidenceQ.refetch()}
          />
        ) : (
          <JsonBlock value={evidenceQ.data?.evidence} maxChars={6000} />
        )}
      </Panel>
      <div className="tiny muted" style={{ marginTop: 8 }}>
        status pills mirror backend values only; <StatusPill status={d?.execution_mode ?? "—"} /> is the recorded execution mode.
      </div>
    </Drawer>
  );
}
