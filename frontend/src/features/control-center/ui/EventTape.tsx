/**
 * PURPOSE:  Console-style event tape — decision-ledger rows (text + timestamp)
 *           rendered monospace with a sticky header and ticking relative age.
 * OWNER:    uiux-wave5-control
 * CONSUMES: ../useCases (controlCenterQueries.decisions — existing GET only),
 *           ../model, @/components/primitives, @/lib/format, lane5Kit
 *           (useNow, CommandResult… not used), ./DecisionInspector handoff via callback
 * PROVIDES: EventTape
 * INVARIANTS: NO dedicated events/logs DTO exists — the tape shows verbatim
 *             decision rows (generated_at / action / stage / gate / reason) in
 *             backend order, window and limit chosen here and disclosed in the
 *             header; loading/error/empty render honest states; payload_ok:false
 *             rows are kept and flagged (inspect disabled, legacy rule).
 * EXTEND:   new column = an existing OperatorDecisionRow field only; never a
 *           synthesized event stream or fabricated timestamp.
 */
import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { EmptyState, ErrorState, Panel, Skeleton, StatusBadge } from "@/components/primitives";
import { formatDateTime } from "@/lib/format";
import { useNow } from "../../research/ui/lane5Kit";
import { num, type OperatorDecisionRow } from "../model";
import { controlCenterQueries, controlCenterUseCases } from "../useCases";

/** Window/limit are choices made here (not backend defaults) and are disclosed in the UI. */
const TAPE_HOURS = 24;
const TAPE_LIMIT = 12;

/** Client-side relative age from a backend timestamp; unparsable → "—". */
function relAge(ts: string | null | undefined, now: number): string {
  if (!ts) return "—";
  const t = Date.parse(ts);
  if (Number.isNaN(t)) return "—";
  const s = Math.max(0, (now - t) / 1000);
  if (s < 5) return "just now";
  if (s < 60) return `${Math.floor(s)}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

export function EventTape({ onInspect }: { onInspect: (id: number | null) => void }) {
  const now = useNow(1000);
  const tapeQ = useQuery({
    queryKey: ["control-center", "tape", TAPE_HOURS, TAPE_LIMIT],
    queryFn: ({ signal }) => controlCenterQueries.decisions({ hours: TAPE_HOURS, limit: TAPE_LIMIT }, signal),
    retry: false,
    refetchInterval: 30_000,
  });
  // perf: memo the row view-model per payload identity — the 1s useNow tick
  // re-renders this panel every second; decisionKey + the row cells derive
  // once per payload, only the ticking age cell recomputes.
  const rows = useMemo(() => tapeQ.data?.rows ?? [], [tapeQ.data]);

  return (
    <Panel
      title="Event tape — decision ledger rows"
      subtitle={`/api/operator/decisions · window ${TAPE_HOURS}h · up to ${TAPE_LIMIT} rows · backend order`}
      right={tapeQ.data ? <span className="tiny muted">{rows.length} rows</span> : undefined}
      tight
    >
      {tapeQ.isPending ? (
        <Skeleton count={6} />
      ) : tapeQ.isError ? (
        <ErrorState
          message={tapeQ.error instanceof Error ? tapeQ.error.message : "tape query failed"}
          onRetry={() => void tapeQ.refetch()}
        />
      ) : tapeQ.data?.available === false ? (
        <EmptyState message="ledger unavailable" hint="operator/decisions answered available:false" />
      ) : rows.length === 0 ? (
        <EmptyState message="No decision rows in this window." />
      ) : (
        <div className="ctl-tape-wrap">
          <table className="ctl-tape">
            <thead>
              <tr>
                <th scope="col">age</th>
                <th scope="col">timestamp</th>
                <th scope="col">action</th>
                <th scope="col">stage</th>
                <th scope="col">gate</th>
                <th scope="col">reason</th>
                <th scope="col">
                  <span className="sr-only">inspect</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r: OperatorDecisionRow) => {
                const id = num(r.id);
                const at = r.generated_at ?? null;
                return (
                  <tr key={controlCenterUseCases.decisionKey(r)}>
                    <td className="ctl-rel" title={formatDateTime(at)}>
                      {relAge(at, now)}
                    </td>
                    <td className="ctl-ts">{formatDateTime(at)}</td>
                    <td className="ctl-act">
                      <StatusBadge status={r.action} />
                    </td>
                    <td>{r.decision_stage ?? "—"}</td>
                    <td>{r.blocked_by ?? ""}</td>
                    <td className="ctl-reason" title={r.reason_code ?? ""}>
                      {r.reason_code ?? "—"}
                    </td>
                    <td className="ctl-open">
                      <button className="btn small ghost" disabled={r.payload_ok === false || id === null} onClick={() => onInspect(id)}>
                        {r.payload_ok === false ? "payload ✗" : "inspect"}
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      <div className="tiny faint ctl-tape-foot">
        rows with unparseable payload are kept and flagged (never silently dropped) — age is computed client-side from the backend timestamp;
        ordering is the backend&apos;s own.
      </div>
    </Panel>
  );
}
