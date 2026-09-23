/**
 * Inspector drawer — snapshot + safety + timeline + debug intelligence
 * (extracted from CommandCenterPage during the BUG-312 analysis wave;
 * behavior unchanged).
 */

import { useQuery } from "@tanstack/react-query";
import { DataTable, EmptyState, ErrorState, Panel, Skeleton, StatusBadge } from "@/components/primitives";
import { formatDateTime, formatNumber } from "@/lib/format";
import { Drawer, GateStepper, InfoRow, JsonBlock } from "../../research/ui/lane5Kit";
import { arr, num, obj, str } from "../model";
import { ccRetry, ccRetryDelay, commandCenterQueries } from "../useCases";
import { useI18n } from "@/stores/i18nStore";

export function InspectorDrawer({ strategyId, onClose }: { strategyId: string; onClose: () => void }) {
  const t = useI18n((s) => s.t);
  const inspectorQ = useQuery({
    queryKey: ["command-center", "inspector", strategyId],
    queryFn: ({ signal }) => commandCenterQueries.inspector(strategyId, signal),
    retry: ccRetry,
    retryDelay: ccRetryDelay,
  });
  const safetyQ = useQuery({
    queryKey: ["command-center", "safety", strategyId],
    queryFn: ({ signal }) => commandCenterQueries.safety(strategyId, signal),
    retry: ccRetry,
    retryDelay: ccRetryDelay,
  });
  const timelineQ = useQuery({
    queryKey: ["command-center", "timeline", strategyId],
    queryFn: ({ signal }) => commandCenterQueries.timeline(strategyId, signal),
    retry: ccRetry,
    retryDelay: ccRetryDelay,
  });

  const snap = inspectorQ.data;
  const debug = obj(snap?.debug_intelligence);
  const attribution = obj(snap?.ai_attribution);
  const completeness = obj(snap?.evidence_completeness);
  const ee = obj(safetyQ.data);
  const events = arr(timelineQ.data?.events);
  const gates = obj(obj(snap?.evaluation).gates);

  return (
    <Drawer title={t("command-center.inspector.title", "Inspector — {id}", { id: strategyId })} onClose={onClose}>
      {inspectorQ.isPending ? (
        <Skeleton count={5} />
      ) : inspectorQ.data?.available === false ? (
        <EmptyState message={str(inspectorQ.data.error) ?? t("command-center.empty.strategy_not_found", "strategy not found")} hint={t("command-center.empty.strategy_not_found_hint", "inspector answers STRATEGY_NOT_FOUND for unknown ids")} />
      ) : (
        <div style={{ display: "grid", gap: 12 }}>
          <Panel title={t("command-center.panel.execution_safety", "Execution safety (CAN-THIS-TRADE)")} accent tight>
            {safetyQ.isPending ? (
              <Skeleton />
            ) : safetyQ.isError ? (
              <ErrorState message={t("command-center.err.safety", "execution-safety endpoint failed")} onRetry={() => void safetyQ.refetch()} />
            ) : (
              <div className="decision-card">
                <div>
                  <div className={`big ${ee.can_trade === true ? "buy" : "sell"}`}>{ee.can_trade === true ? t("command-center.decision.yes", "YES") : ee.can_trade === false ? t("command-center.decision.no", "NO") : "—"}</div>
                  <div className="why-detail tiny">{str(ee.lifecycle) ?? "—"}</div>
                </div>
                <div style={{ flex: 1 }}>
                  <dl className="kv">
                    <InfoRow label="eligibility_state" value={<StatusBadge status={str(ee.eligibility_state)} />} />
                    <InfoRow label={t("command-center.label.reason", "reason")} value={str(ee.reason) ?? "—"} />
                    <InfoRow label="required_gate" value={str(ee.required_gate) ?? "—"} />
                    <InfoRow label={t("command-center.label.blockers", "blockers")} value={arr(ee.blockers).length === 0 ? t("command-center.label.none", "none") : t("command-center.label.listed", "{n} listed", { n: arr(ee.blockers).length })} />
                  </dl>
                  {arr(ee.blockers).map((b, i) => (
                    <div key={i} className="tiny muted">
                      • {typeof b === "string" ? b : JSON.stringify(b)}
                    </div>
                  ))}
                </div>
              </div>
            )}
          </Panel>

          <Panel title={t("command-center.panel.evaluation", "Evaluation (transient telemetry — not lifecycle)")} tight>
            {Object.keys(gates).length === 0 ? (
              <EmptyState message={t("command-center.empty.no_evaluation", "No evaluation running / recorded.")} />
            ) : (
              <GateStepper gates={Object.entries(gates).map(([k, v]) => ({ name: k, status: str(v) ?? "UNKNOWN" }))} />
            )}
          </Panel>

          <div className="grid cols-2">
            <Panel title={t("command-center.panel.debug_intel", "Debug intelligence (backend-computed)")} tight>
              <dl className="kv">
                <InfoRow label={t("command-center.label.anomaly_score", "anomaly score")} value={formatNumber(num(debug.anomaly_score) ?? NaN, 3)} />
                <InfoRow label={t("command-center.label.validation_consistency", "validation consistency")} value={formatNumber(num(debug.validation_consistency) ?? NaN, 3)} />
                <InfoRow label={t("command-center.label.debug_priority", "debug priority")} value={formatNumber(num(debug.debug_priority) ?? NaN, 3)} />
              </dl>
              {arr(debug.hints).length > 0 && (
                <ul className="tiny muted" style={{ margin: "6px 0 0", paddingInlineStart: 16 }}>
                  {arr(debug.hints).map((h, i) => (
                    <li key={i}>{typeof h === "string" ? h : JSON.stringify(h)}</li>
                  ))}
                </ul>
              )}
            </Panel>
            <Panel title={t("command-center.panel.evidence", "Evidence completeness")} tight>
              {Object.keys(completeness).length === 0 ? (
                <EmptyState message={t("command-center.empty.completeness_missing", "completeness not reported")} />
              ) : (
                <dl className="kv">
                  {Object.entries(completeness).slice(0, 10).map(([k, v]) => (
                    <InfoRow key={k} label={k} value={typeof v === "object" ? "…" : String(v)} />
                  ))}
                </dl>
              )}
            </Panel>
          </div>

          {Object.keys(attribution).length > 0 && (
            <Panel title={t("command-center.panel.ai_attribution", "AI attribution (explainability)")} tight>
              <JsonBlock value={attribution} maxChars={2500} />
            </Panel>
          )}

          <Panel title={t("command-center.panel.decision_timeline", "Decision timeline ({n})", { n: events.length })} tight>
            {events.length === 0 ? (
              timelineQ.isPending ? <Skeleton /> : <EmptyState message={t("command-center.empty.no_timeline", "No timeline events.")} />
            ) : (
              <DataTable headers={[{ label: t("command-center.th.at", "at") }, { label: t("command-center.th.event", "event") }, { label: t("command-center.th.from", "from") }, { label: t("command-center.th.to", "to") }, { label: t("command-center.th.actor", "actor") }]}>
                {events.slice(0, 60).map((e, i) => (
                  <tr key={i}>
                    <td className="tiny">{formatDateTime(str(e.timestamp) ?? str(e.at))}</td>
                    <td className="tiny">{str(e.event_type) ?? str(e.event) ?? "—"}</td>
                    <td className="tiny">{str(e.from_state) ?? str(e.from) ?? ""}</td>
                    <td className="tiny">{str(e.to_state) ?? str(e.to) ?? ""}</td>
                    <td className="tiny muted">{str(e.actor) ?? "—"}</td>
                  </tr>
                ))}
              </DataTable>
            )}
          </Panel>

          <Panel title={t("command-center.panel.invariant", "Invariant check")} tight>
            <JsonBlock value={snap?.invariant_check} maxChars={1500} />
          </Panel>
        </div>
      )}
    </Drawer>
  );
}
