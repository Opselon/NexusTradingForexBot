/**
 * Research — strategy detail drawer (one-click trace: runs -> gates -> events
 * -> evidence, per TASK-21 /api/research/detail/{id}). Data stays
 * backend-authoritative; a missing block renders as "backend returned none".
 */

import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { ConfirmModal, DataTable, EmptyState, ErrorState, MetricCard, Panel, Skeleton, StatusBadge } from "@/components/primitives";
import { useMutationFeedback } from "@/hooks/useMutationFeedback";
import { formatDateTime, formatNumber } from "@/lib/format";
import { CommandResultLine, Drawer, GateStepper, JsonBlock, StatusPill } from "./lane5Kit";
import { GATE_CHAIN } from "../handbook";
import StrategyPlaybook from "./StrategyPlaybook";
import "./research.css";
import { commandVerdict, obj, str, toGateVo, type Row } from "../model";
import { researchQueries, researchUseCases } from "../useCases";

export default function StrategyDrawer({ strategyId, onClose }: { strategyId: string; onClose: () => void }) {
  const [tab, setTab] = useState<"trace" | "gates" | "events" | "evidence" | "raw" | "playbook">("trace");
  const [confirmGate, setConfirmGate] = useState<string | null>(null);
  const [confirmRun, setConfirmRun] = useState<string | null>(null);
  const cmd = useMutationFeedback();
  const queryClient = useQueryClient();

  const detailQ = useQuery({
    queryKey: ["research", "detail", strategyId],
    queryFn: ({ signal }) => researchQueries.detail(strategyId, signal),
    retry: false,
  });
  const gatesQ = useQuery({
    queryKey: ["research", "gates", strategyId],
    queryFn: ({ signal }) => researchQueries.gates(strategyId, undefined, signal),
    retry: false,
  });
  const runsQ = useQuery({
    queryKey: ["research", "runs", strategyId],
    queryFn: ({ signal }) => researchQueries.runs(strategyId, signal),
    retry: false,
  });
  const eventsQ = useQuery({
    queryKey: ["research", "events", strategyId],
    queryFn: ({ signal }) => researchQueries.events(strategyId, undefined, signal),
    retry: false,
  });
  const evidenceQ = useQuery({
    queryKey: ["research", "evidence", strategyId],
    queryFn: ({ signal }) => researchQueries.evidence(strategyId, undefined, signal),
    retry: false,
  });
  const preflightQ = useQuery({
    queryKey: ["research", "preflight", strategyId],
    queryFn: ({ signal }) => researchQueries.preflight(strategyId, signal),
    retry: false,
    enabled: tab === "trace",
  });

  const afterCommand = () => {
    void queryClient.invalidateQueries({ queryKey: ["research"] });
  };

  const detail = obj(detailQ.data?.detail);
  const trace = detailQ.data?.available === true ? detail : null;
  // perf: gate VO map derived only when the query data changes
  // (deps: gatesQ.data — the only reactive value read).
  const gates = useMemo(() => (gatesQ.data?.gates ?? []).map(toGateVo), [gatesQ.data]);
  const runs = runsQ.data?.runs ?? [];
  const events = eventsQ.data?.events ?? [];
  const evidence = evidenceQ.data?.evidence ?? [];

  /**
   * Compact chain rail (docs vocabulary from handbook GATE_CHAIN): class per
   * step derives ONLY from the backend's own gate rows — passed/failed/running —
   * never inferred. Name match is case-insensitive; unknown steps rest neutral.
   */
  const stepClass = (chainGate: string): string => {
    const mine = gates.filter((g) => g.name.toUpperCase() === chainGate);
    const last = mine.at(-1);
    if (!last) return "";
    const s = (last.status ?? "").toUpperCase();
    if (s === "PASSED") return "passed";
    if (s === "FAILED" || s === "ERROR" || s === "CANCELLED") return "failed";
    if (s === "RUNNING" || s === "QUEUED") return "running";
    return "";
  };

  const tabs: Array<[typeof tab, string]> = [
    ["trace", "Trace"],
    ["gates", `Gates (${gates.length})`],
    ["events", `Events (${events.length})`],
    ["evidence", `Evidence (${evidence.length})`],
    ["raw", "Raw invariant"],
    ["playbook", "Playbook"],
  ];

  return (
    <Drawer title={`Strategy trace — ${strategyId}`} onClose={onClose}>
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 10 }}>
        {tabs.map(([id, label]) => (
          <button key={id} className={`btn small ${tab === id ? "primary" : "ghost"}`} aria-pressed={tab === id} onClick={() => setTab(id)}>
            {label}
          </button>
        ))}
      </div>
      <CommandResultLine state={cmd.state} />

      {tab === "trace" &&
        (detailQ.isPending ? (
          <Skeleton count={4} />
        ) : detailQ.isError ? (
          <ErrorState
                      message={detailQ.error instanceof Error ? detailQ.error.message : "detail request failed"}
                      onRetry={() => void detailQ.refetch()}
                    />
        ) : detailQ.data?.available === false ? (
          <EmptyState message="Research subsystem unavailable" hint={detailQ.data.reason ?? "backend answered available:false"} />
        ) : (
          <div style={{ display: "grid", gap: 10 }}>
            <div className="grid cols-3">
              <MetricCard label="Lifecycle" value={<StatusBadge status={str(trace?.lifecycle)} />} sub={str(trace?.blocked_reason) ?? undefined} />
              <MetricCard label="Gate records" value={String(gates.length)} tone="dim" sub="from /api/research/gates" />
              <MetricCard
                label="Preflight"
                value={<StatusPill status={str(obj(preflightQ.data?.preflight).status)} />}
                sub={(obj(preflightQ.data?.preflight).blockers as string[] | undefined)?.join(", ") ?? "no blockers reported"}
              />
            </div>
            <div className="rs-rail-drawer" aria-label="Gate chain position">
              {GATE_CHAIN.map((g, i) => (
                <span key={g} style={{ display: "contents" }}>
                  {i > 0 && <span className="rs-rail-arrow" aria-hidden="true">→</span>}
                  <span className={`rs-step ${stepClass(g)}`} title={`chain step ${i + 1}: ${g}`}>
                    {i + 1}. {g}
                  </span>
                </span>
              ))}
            </div>
            <Panel title="Gate pipeline (backend verdicts)" tight>
              <GateStepper
                gates={gates.map((g) => ({
                  name: g.name + (g.gateId ? ` · ${g.gateId.slice(0, 8)}` : ""),
                  status: g.status,
                  reason: g.reason ?? (g.failureClass ? `class: ${g.failureClass}` : null),
                  detail:
                    g.gateId && (g.failureClass === "TECHNICAL" || g.failureClass === "DATA" || g.retryable) ? (
                      <button className="btn small ghost" disabled={cmd.state.running} onClick={() => setConfirmGate(g.gateId)}>
                        retry gate
                      </button>
                    ) : undefined,
                }))}
              />
              {gates.length === 0 && gatesQ.isPending && <Skeleton count={3} />}
            </Panel>
            <Panel title="Validation runs (reproducibility lineage)" tight>
              {runs.length === 0 ? (
                runsQ.isPending ? <Skeleton /> : <EmptyState message="No research runs recorded for this strategy." />
              ) : (
                <DataTable headers={[{ label: "run" }, { label: "dataset" }, { label: "executed" }, { label: "result" }, { label: "" }]}>
                  {runs.slice(0, 12).map((r: Row, i: number) => (
                    <tr key={str(r.research_run_id) ?? i}>
                      <td className="inline-mono tiny">{str(r.research_run_id)?.slice(0, 12) ?? "—"}</td>
                      <td className="inline-mono tiny">{str(r.dataset_id) ?? "—"}</td>
                      <td className="tiny">{formatDateTime(str(r.executed_at))}</td>
                      <td className="tiny">{str(r.outcome) ?? str(r.result) ?? "—"}</td>
                      <td>
                        <button className="btn small ghost" onClick={() => setConfirmRun(str(r.research_run_id))}>
                          cancel run
                        </button>
                      </td>
                    </tr>
                  ))}
                </DataTable>
              )}
            </Panel>
          </div>
        ))}

      {tab === "gates" && (
        <Panel title="Gate ledger" tight>
          {gates.length === 0 ? (
            gatesQ.isPending ? <Skeleton /> : <EmptyState message="No gate records returned." />
          ) : (
            <DataTable headers={[{ label: "gate" }, { label: "status" }, { label: "class" }, { label: "reason" }, { label: "ms", num: true }, { label: "" }]}>
              {gates.map((g, i) => (
                <tr key={g.gateId ?? i}>
                  <td className="small">{g.name}</td>
                  <td>
                    <StatusPill status={g.status} />
                  </td>
                  <td className="tiny muted">{g.failureClass ?? "—"}</td>
                  <td className="tiny" style={{ maxWidth: 260, overflow: "hidden", textOverflow: "ellipsis" }} title={g.reason ?? ""}>
                    {g.reason ?? "—"}
                  </td>
                  <td className="tiny num">{g.durationMs === null ? "—" : formatNumber(g.durationMs, 0)}</td>
                  <td>
                    {g.retryable || g.failureClass === "TECHNICAL" || g.failureClass === "DATA" ? (
                      <button className="btn small ghost" disabled={cmd.state.running} onClick={() => setConfirmGate(g.gateId)}>
                        retry
                      </button>
                    ) : null}
                  </td>
                </tr>
              ))}
            </DataTable>
          )}
        </Panel>
      )}

      {tab === "events" && (
        <Panel title="Persisted gate timeline" tight>
          {events.length === 0 ? (
            eventsQ.isPending ? <Skeleton /> : <EmptyState message="No archived/live events for this strategy." />
          ) : (
            <DataTable headers={[{ label: "at" }, { label: "event" }, { label: "detail" }]}>
              {events.slice(0, 100).map((e: Row, i: number) => (
                <tr key={i}>
                  <td className="tiny">{formatDateTime(str(e.timestamp) ?? str(e.created_at))}</td>
                  <td className="small">{str(e.event_type) ?? "—"}</td>
                  <td className="tiny muted">{str(e.message) ?? str(e.detail) ?? ""}</td>
                </tr>
              ))}
            </DataTable>
          )}
        </Panel>
      )}

      {tab === "evidence" && (
        <Panel title="Immutable evidence vault" tight>
          {evidence.length === 0 ? (
            evidenceQ.isPending ? <Skeleton /> : <EmptyState message="No evidence rows returned." />
          ) : (
            <div style={{ display: "grid", gap: 8 }}>
              {evidence.slice(0, 25).map((e: Row, i: number) => (
                <details key={i} style={{ border: "1px solid var(--border)", borderRadius: 6, padding: "6px 10px" }}>
                  <summary className="small">
                    {str(e.evidence_id)?.slice(0, 14) ?? "evidence"} · {str(e.kind) ?? str(e.evidence_type) ?? "—"} ·{" "}
                    <span className="muted">{formatDateTime(str(e.created_at))}</span>
                  </summary>
                  <div style={{ marginTop: 6 }}>
                    <JsonBlock value={e.payload ?? e.data ?? e} maxChars={2500} />
                  </div>
                </details>
              ))}
            </div>
          )}
        </Panel>
      )}

      {tab === "playbook" && (
        <div className="rs-pane" key="playbook">
          <StrategyPlaybook compact focusId="topic/gates" />
        </div>
      )}

      {tab === "raw" && (
        <Panel title="Invariant check (backend)" tight>
          <JsonBlock value={trace?.invariant ?? obj(trace)} />
        </Panel>
      )}

      {confirmGate && (
        <ConfirmModal
          title="Retry gate"
          danger={false}
          confirmLabel="Retry"
          busy={cmd.state.running}
          onCancel={() => setConfirmGate(null)}
          onConfirm={async () => {
            const gid = confirmGate;
            setConfirmGate(null);
            await cmd.run(async () => {
              const v = commandVerdict(await researchUseCases.retryGate(gid));
              return { ok: v.ok, success: v.ok, message: `retry-gate: ${v.message}`, status: v.ok ? 200 : 500 };
            });
            afterCommand();
          }}
        >
          <div className="small">
            Only TECHNICAL/DATA failures are retryable — a statistical RESEARCH failure is never retried by the backend. Gate{" "}
            <b className="inline-mono">{confirmGate.slice(0, 12)}…</b>
          </div>
        </ConfirmModal>
      )}

      {confirmRun && (
        <ConfirmModal
          title="Cancel research run"
          danger
          confirmLabel="Cancel run"
          busy={cmd.state.running}
          onCancel={() => setConfirmRun(null)}
          onConfirm={async () => {
            const rid = confirmRun;
            setConfirmRun(null);
            await cmd.run(async () => {
              const v = commandVerdict(await researchUseCases.cancelRun(rid));
              return { ok: v.ok, success: v.ok, message: `cancel: ${v.message}`, status: v.ok ? 200 : 500 };
            });
            afterCommand();
          }}
        >
          <div className="small">
            Run <b className="inline-mono">{confirmRun?.slice(0, 12) ?? ""}…</b> becomes CANCELLED
            (never FAILED); completed gate results are preserved.
          </div>
        </ConfirmModal>
      )}
    </Drawer>
  );
}
