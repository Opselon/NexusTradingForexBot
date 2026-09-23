/**
 * PURPOSE:  One-decision inspector drawer — ledger row, probabilities, correlated
 *           orders (method disclosed) and the raw payload.
 * OWNER:    uiux-wave5-control
 * CONSUMES: ../useCases (controlCenterQueries.decisionDetail), ../model,
 *           @/components/primitives, ../../research/ui/lane5Kit (Drawer/InfoRow/JsonBlock)
 * PROVIDES: DecisionInspector
 * INVARIANTS: NOT RECORDED for every absent value; payload_ok:false rows keep the
 *             empty-state explanation (never fabricated probabilities); the
 *             correlation method is always shown before the orders table.
 * EXTEND:   new evidence block = a field that already exists on
 *           OperatorDecisionDetailDto.decision.
 */
import { useQuery } from "@tanstack/react-query";
import { DataTable, EmptyState, Panel, Skeleton } from "@/components/primitives";
import { formatDateTime } from "@/lib/format";
import { Drawer, InfoRow, JsonBlock } from "../../research/ui/lane5Kit";
import { arr, bool, notRecorded, num, obj, str } from "../model";
import { controlCenterQueries } from "../useCases";
import { useI18n } from "@/stores/i18nStore";

export function DecisionInspector({ id, onClose }: { id: number; onClose: () => void }) {
  const t = useI18n((s) => s.t);
  const detailQ = useQuery({
    queryKey: ["control-center", "decision", id],
    queryFn: ({ signal }) => controlCenterQueries.decisionDetail(id, signal),
    retry: false,
  });
  const d = obj(detailQ.data?.decision);
  const probs = obj(d.probabilities);
  return (
    <Drawer title={t("control-center.inspector.title", "Decision #{id} — evidence", { id })} onClose={onClose}>
      {detailQ.isPending ? (
        <Skeleton count={4} />
      ) : detailQ.data?.available === false ? (
        <EmptyState message={str(obj(detailQ.data?.error).reason) ?? t("control-center.empty.decision_not_found", "decision not found")} />
      ) : (
        <div style={{ display: "grid", gap: 10 }}>
          <Panel title={t("control-center.panel.ledger_row", "Ledger row")} tight>
            <dl className="kv">
              <InfoRow label={t("control-center.label.action_mode", "action / mode")} value={`${notRecorded(str(d.action))} / ${notRecorded(str(d.execution_mode))}`} />
              <InfoRow label={t("control-center.label.confidence", "confidence")} value={notRecorded(str(d.confidence))} />
              <InfoRow label="stage / blocked_by" value={`${notRecorded(str(d.decision_stage))} / ${notRecorded(str(d.blocked_by))}`} />
              <InfoRow label={t("control-center.label.reason", "reason")} value={notRecorded(str(d.reason_code))} />
              <InfoRow label="request_id" value={<span className="inline-mono tiny">{notRecorded(str(d.request_id))}</span>} />
            </dl>
          </Panel>
          <Panel title={t("control-center.panel.model_probs", "Model probabilities (NOT RECORDED when absent)")} tight>
            {bool(d.payload_ok) === false ? (
              <EmptyState message={t("control-center.empty.payload_unparseable", "payload unparseable — kept with payload_ok:false (never fabricated)")} />
            ) : (
              <dl className="kv">
                <InfoRow label="P(buy)" value={notRecorded(str(probs.buy))} />
                <InfoRow label="P(sell)" value={notRecorded(str(probs.sell))} />
                <InfoRow label="P(no_trade)" value={notRecorded(str(probs.no_trade))} />
                <InfoRow label="P(wait)" value={notRecorded(str(probs.wait))} />
                <InfoRow label="model_action" value={notRecorded(str(probs.model_action))} />
                <InfoRow label="confidence_source" value={notRecorded(str(obj(probs.raw).source))} />
              </dl>
            )}
          </Panel>
          <Panel title={t("control-center.inspector.correlated_orders", "Correlated orders ({n})", { n: arr(d.orders).length })} tight>
            <div className="tiny muted" style={{ marginBottom: 6 }}>
              {t("control-center.inspector.correlation_method", "correlation method: {v}", { v: notRecorded(str(d.correlation_method)) })}
            </div>
            {arr(d.orders).length === 0 ? (
              <EmptyState message={t("control-center.empty.no_correlated", "No correlated dispatch rows.")} />
            ) : (
              <DataTable headers={[{ label: t("control-center.th.ts", "ts") }, { label: t("control-center.th.ticket", "ticket") }, { label: t("control-center.th.action", "action") }, { label: t("control-center.th.latency", "latency") }]}>
                {arr(d.orders).map((o, i) => (
                  <tr key={i}>
                    <td className="tiny">{formatDateTime(str(o.timestamp))}</td>
                    <td className="num tiny">{num(o.ticket) ?? "—"}</td>
                    <td className="tiny">{str(o.action) ?? "—"}</td>
                    <td className="num tiny">{num(o.latency) === null ? "—" : `${String(num(o.latency))}ms`}</td>
                  </tr>
                ))}
              </DataTable>
            )}
          </Panel>
          <Panel title={t("control-center.panel.raw_payload", "Raw evidence payload")} tight>
            <JsonBlock value={d.payload ?? d} maxChars={5000} />
          </Panel>
        </div>
      )}
    </Drawer>
  );
}
