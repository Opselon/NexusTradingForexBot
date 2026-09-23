/**
 * Governance — model governance + experience ledger (legacy tab-governance).
 *
 * Promotion ladder (status-derived), decision ledger (governance events with
 * filters), calibration review, immutable audits, guarded self-heal +
 * emergency freeze/unfreeze/disable. Every command needs an explicit actor:
 * the backend refuses without one, and the refusal is rendered verbatim.
 */

import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import type { ShellPageProps } from "@/app/featureModule";
import {
  ConfirmModal,
  DataTable,
  EmptyState,
  MetricCard,
  Panel,
  Segmented,
  Skeleton,
  StatusBadge,
} from "@/components/primitives";
import { useMutationFeedback } from "@/hooks/useMutationFeedback";
import { formatDateTime, formatNumber } from "@/lib/format";
import { CommandResultLine, DistBars, FreshnessCaption, GateStepper, InfoRow, JsonBlock, StatusPill } from "../../research/ui/lane5Kit";
import { arr, bool, commandVerdict, num, obj, str, type Row } from "../model";
import { governanceQueries, governanceUseCases } from "../useCases";

type Tab = "ladder" | "ledger" | "experience" | "review";

interface PendingCommand {
  title: string;
  danger: boolean;
  needsModel: boolean;
  run: (actor: string, modelId: string) => Promise<unknown>;
  label: string;
}

export default function GovernancePage(props: ShellPageProps) {
  void props;
  const [tab, setTab] = useState<Tab>("ladder");
  const [eventFilter, setEventFilter] = useState("");
  const [actor, setActor] = useState("operator");
  const [pending, setPending] = useState<PendingCommand | null>(null);
  const cmd = useMutationFeedback();
  const qc = useQueryClient();

  const statusQ = useQuery({
    queryKey: ["governance", "status"],
    queryFn: ({ signal }) => governanceQueries.status(signal),
    refetchInterval: 30_000,
    retry: false,
  });
  const eventsQ = useQuery({
    queryKey: ["governance", "events", eventFilter],
    queryFn: ({ signal }) => governanceQueries.events(eventFilter || undefined, signal),
    retry: false,
    enabled: tab === "ledger",
  });
  const expSummaryQ = useQuery({
    queryKey: ["governance", "experience-summary"],
    queryFn: ({ signal }) => governanceQueries.experienceSummary(signal),
    retry: false,
  });
  const expDecisionQ = useQuery({
    queryKey: ["governance", "experience-decision"],
    queryFn: ({ signal }) => governanceQueries.experienceDecision(signal),
    retry: false,
    enabled: tab === "experience",
  });
  const expModelsQ = useQuery({
    queryKey: ["governance", "experience-models"],
    queryFn: ({ signal }) => governanceQueries.experienceModels(signal),
    retry: false,
    enabled: tab === "experience",
  });
  const expStrategiesQ = useQuery({
    queryKey: ["governance", "experience-strategies"],
    queryFn: ({ signal }) => governanceQueries.experienceStrategies(signal),
    retry: false,
    enabled: tab === "experience",
  });
  const reviewQ = useQuery({
    queryKey: ["governance", "review"],
    queryFn: ({ signal }) => governanceQueries.review(signal),
    retry: false,
    enabled: tab === "review",
  });
  const auditsQ = useQuery({
    queryKey: ["governance", "audits"],
    queryFn: ({ signal }) => governanceQueries.audits(signal),
    retry: false,
    enabled: tab === "ledger",
  });

  const after = () => void qc.invalidateQueries({ queryKey: ["governance"] });

  // ConfigPage house standard: while a mutation is in flight the toolbar is
  // inert and each button names its own running operation (no new calls or
  // payloads — busy mirrors cmd.state.running only).
  const busy = cmd.state.running;
  const busyLabel = (idle: string, running: string) => (busy ? running : idle);

  const ask = (title: string, danger: boolean, needsModel: boolean, label: string, run: PendingCommand["run"]) =>
    setPending({ title, danger, needsModel, label, run });

  const status = statusQ.data?.available === true ? statusQ.data : null;
  const ladder = status ? governanceUseCases.ladder(status) : [];
  const candModel = status?.candidate?.model_id ?? "";
  const frozen = bool(status?.promotion?.frozen) ?? false;
  const events = governanceUseCases.ledgerRows(arr(eventsQ.data?.events));

  return (
    <div>
      <div className="page-head" style={{ display: "flex", alignItems: "baseline", gap: 10, flexWrap: "wrap" }}>
        <h2>Governance</h2>
        <span className="muted small">model promotion ladder · decision ledger · experience intelligence</span>
        <FreshnessCaption
          timestamp={expSummaryQ.data?.fetched_at ?? undefined}
          source="experience summary"
          isFetching={statusQ.isFetching}
          error={statusQ.isError}
        />
      </div>

      <div className="grid cols-4">
        <MetricCard
          label="Champion"
          value={<span className="tiny inline-mono">{status?.champion?.model_id ?? "—"}</span>}
          sub={`schema ${status?.champion?.schema ?? "—"} · v${status?.champion?.version ?? "—"}`}
        />
        <MetricCard label="Candidate" value={<StatusPill status={status?.candidate?.status ?? "NONE"} />} sub={status?.candidate?.model_id ?? "no challenger recorded"} />
        <MetricCard
          label="Promotion state"
          value={<StatusBadge status={frozen ? "BLOCKED" : status?.promotion?.approved ? "PENDING" : status?.promotion?.eligible ? "ACTIVE" : "IDLE"} label={frozen ? "frozen by emergency gate" : undefined} />}
          tone={frozen ? "neg" : "dim"}
          sub={frozen ? "EMERGENCY FREEZE ACTIVE" : "eligible/approved from governance/status"}
        />
        <MetricCard label="Active strategies" value={String(expSummaryQ.data?.active_strategies ?? 0)} sub={`recorded experiences: ${formatNumber(expSummaryQ.data?.recorded_experiences ?? 0, 0)}`} />
      </div>

      <Panel title="Promotion ladder (backend-decided)" accent tight>
        {statusQ.isPending ? (
          <Skeleton count={2} />
        ) : !status ? (
          <EmptyState message="Model governance engine unavailable" hint="/api/models/governance/status answered available:false — no ladder to draw." />
        ) : (
          <GateStepper
            gates={ladder.map((s) => ({
              name: s.label,
              status: s.active ? (s.id === "promotion" ? s.state : "PASS") : s.state === "NONE" ? "NOT_RUN" : s.state,
              reason: s.state,
            }))}
          />
        )}
      </Panel>

      <div style={{ marginBlock: 12, display: "grid", gap: 8 }}>
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center" }}>
          <label className="tiny muted" htmlFor="gov-actor">
            actor (required by backend)
          </label>
          <input id="gov-actor" className="input" style={{ width: 170 }} value={actor} onChange={(e) => setActor(e.target.value)} />
          <button
            className="btn small danger"
            disabled={busy || !status}
            title={busy ? "a command is already running" : undefined}
            onClick={() =>
              ask("Freeze promotions", true, false, "Freeze", (a) => governanceUseCases.freeze(a, "ui:lane5 emergency freeze"))
            }
          >
            {busyLabel("❄ Freeze promotions", "❄ freezing…")}
          </button>
          <button
            className="btn small"
            disabled={busy || !frozen}
            title={busy ? "a command is already running" : undefined}
            onClick={() => ask("Unfreeze promotions", true, false, "Unfreeze", (a) => governanceUseCases.unfreeze(a, "ui:lane5 release freeze"))}
          >
            {busyLabel("⚑ Unfreeze", "⚑ unfreezing…")}
          </button>
          <button
            className="btn small danger"
            disabled={busy || !candModel}
            title={busy ? "a command is already running" : undefined}
            onClick={() =>
              ask(`Disable candidate ${candModel}`, true, false, "Disable candidate", (a) =>
                governanceUseCases.disableCandidate(a, candModel, "ui:lane5 operator disable"),
              )
            }
          >
            {busyLabel("⛔ Disable candidate", "⛔ disabling…")}
          </button>
          <button className="btn small" disabled={busy} title={busy ? "a command is already running" : undefined} onClick={() => ask("Reconcile model registry", false, false, "Reconcile", () => governanceUseCases.reconcileRegistry())}>
            {busyLabel("Reconcile registry", "reconciling…")}
          </button>
          <button className="btn small danger" disabled={busy} title={busy ? "a command is already running" : undefined} onClick={() => ask("Self-heal experience intelligence", true, false, "Rebuild", () => governanceUseCases.selfHealExperience())}>
            {busyLabel("Self-heal experience", "self-healing…")}
          </button>
        </div>
        <div className="tiny muted">
          Approve/execute promotion intentionally stays behind the preview + token flow (approval_token is minted by the approve transition); this
          console exposes the emergency set + rebuild commands only. The backend decides every outcome.
        </div>
        <CommandResultLine state={cmd.state} />
      </div>

      <Segmented
        options={[
          { id: "ladder" as const, label: "Registry & ladder" },
          { id: "ledger" as const, label: "Decision ledger" },
          { id: "experience" as const, label: "Experience" },
          { id: "review" as const, label: "Calibration review" },
        ]}
        value={tab}
        onChange={setTab}
      />

      <div style={{ marginTop: 12, display: "grid", gap: 12 }}>
        {tab === "ladder" && (
          <Panel title="Gate matrix (governance/status.gates)" tight>
            {status ? (
              <DataTable headers={[{ label: "gate" }, { label: "verdict" }]}>
                {Object.entries(obj(status.gates)).map(([k, v]) => (
                  <tr key={k}>
                    <td className="small">{k}</td>
                    <td>
                      <StatusPill status={str(v)} />
                    </td>
                  </tr>
                ))}
              </DataTable>
            ) : (
              <EmptyState message="no governance status" />
            )}
            <div style={{ marginTop: 10 }}>
              <Panel title="Registry reconciliation snapshot" tight>
                <RegistryInline />
              </Panel>
            </div>
          </Panel>
        )}

        {tab === "ledger" && (
          <>
            <Panel
              title="Governance event ledger (append-only)"
              right={
                <input
                  className="input"
                  style={{ width: 180 }}
                  aria-label="Event type filter" placeholder="filter by event type"
                  value={eventFilter}
                  onChange={(e) => setEventFilter(e.target.value)}
                />
              }
              tight
            >
              {eventsQ.isPending ? (
                <Skeleton count={4} />
              ) : eventsQ.isError ? (
                <EmptyState message="events endpoint failed" />
              ) : events.length === 0 ? (
                <EmptyState message="No governance events recorded." />
              ) : (
                <DataTable headers={[{ label: "at" }, { label: "event" }, { label: "model" }, { label: "actor" }, { label: "reason" }]}>
                  {events.slice(0, 100).map((r, i) => (
                    <tr key={i}>
                      <td className="tiny">{formatDateTime(r.at)}</td>
                      <td>
                        <StatusPill status={r.event} />
                      </td>
                      <td className="inline-mono tiny">{r.modelId ?? "—"}</td>
                      <td className="tiny">{r.actor ?? "—"}</td>
                      <td className="tiny muted" title={r.reason ?? ""}>
                        {r.reason?.slice(0, 60) ?? "—"}
                      </td>
                    </tr>
                  ))}
                </DataTable>
              )}
            </Panel>
            <Panel title="Immutable promotion/rollback audits" tight>
              {auditsQ.isPending ? (
                <Skeleton count={2} />
              ) : (arr(auditsQ.data?.audits).length === 0 ? <EmptyState message="No audit rows." /> : (
                <DataTable headers={[{ label: "at" }, { label: "action" }, { label: "model" }, { label: "actor" }]}>
                  {arr(auditsQ.data?.audits)
                    .slice(0, 40)
                    .map((a: Row, i: number) => (
                      <tr key={i}>
                        <td className="tiny">{formatDateTime(str(a.created_at) ?? str(a.at))}</td>
                        <td className="small">{str(a.action) ?? str(a.kind) ?? "—"}</td>
                        <td className="inline-mono tiny">{str(a.model_id) ?? "—"}</td>
                        <td className="tiny">{str(a.actor) ?? "—"}</td>
                      </tr>
                    ))}
                </DataTable>
              ))}
            </Panel>
          </>
        )}

        {tab === "experience" && (
          <div className="grid cols-2">
            <Panel title="Last pre-trade experience verdict" tight>
              {expDecisionQ.isPending ? (
                <Skeleton count={2} />
              ) : expDecisionQ.data?.available === false || !expDecisionQ.data?.decision ? (
                <EmptyState message="No experience decision recorded yet." hint="engine answers {available:false} until the first pre-trade evaluation" />
              ) : (
                <JsonBlock value={expDecisionQ.data.decision} maxChars={2500} />
              )}
            </Panel>
            <Panel title="Experience ledger census" tight>
              <dl className="kv" style={{ marginBottom: 10 }}>
                <InfoRow label="enabled" value={expSummaryQ.data?.enabled ? "true" : "false"} />
                <InfoRow label="recorded" value={formatNumber(expSummaryQ.data?.recorded_experiences ?? 0, 0)} />
                <InfoRow label="retired strategies" value={String(expSummaryQ.data?.retired_strategies ?? 0)} />
              </dl>
              <DistBars
                rows={Object.entries(obj(expSummaryQ.data?.lifecycle_counts)).map(([k, v]) => ({ label: k, count: num(v) ?? 0 }))}
                tone="var(--green)"
              />
            </Panel>
            <Panel title={`Model provenance (${arr(expModelsQ.data).length})`} tight>
              {expModelsQ.isPending ? (
                <Skeleton count={2} />
              ) : arr(expModelsQ.data).length === 0 ? (
                <EmptyState message="No registered model provenance rows." />
              ) : (
                <DataTable headers={[{ label: "model" }, { label: "schema" }, { label: "dim", num: true }, { label: "created" }]}>
                  {arr(expModelsQ.data)
                    .slice(0, 25)
                    .map((m: Row, i: number) => (
                      <tr key={i}>
                        <td className="inline-mono tiny">{str(m.model_id) ?? str(m.id) ?? "—"}</td>
                        <td className="tiny">{str(m.feature_schema_id) ?? "—"}</td>
                        <td className="num tiny">{num(m.feature_dimension) ?? "—"}</td>
                        <td className="tiny">{formatDateTime(str(m.created_at))}</td>
                      </tr>
                    ))}
                </DataTable>
              )}
            </Panel>
            <Panel title={`Derived strategy scores (${arr(expStrategiesQ.data).length})`} tight>
              {expStrategiesQ.isPending ? (
                <Skeleton count={2} />
              ) : arr(expStrategiesQ.data).length === 0 ? (
                <EmptyState message="No derived intelligence rows — run experience self-heal if the ledger is non-empty." />
              ) : (
                <DataTable headers={[{ label: "strategy" }, { label: "state" }, { label: "score", num: true }, { label: "updated" }]}>
                  {arr(expStrategiesQ.data)
                    .slice(0, 25)
                    .map((s: Row, i: number) => (
                      <tr key={i}>
                        <td className="inline-mono tiny" title={str(s.strategy_id) ?? ""}>
                          {(str(s.strategy_id) ?? "—").slice(0, 16)}
                        </td>
                        <td>
                          <StatusPill status={str(s.lifecycle_state) ?? str(s.state)} />
                        </td>
                        <td className="num tiny">{num(s.score) === null ? "—" : formatNumber(num(s.score)!, 3)}</td>
                        <td className="tiny">{formatDateTime(str(s.updated_at))}</td>
                      </tr>
                    ))}
                </DataTable>
              )}
            </Panel>
          </div>
        )}

        {tab === "review" && (
          <Panel title="Calibration + drift review (governance evidence)" right={<span className="tiny muted">/api/models/governance/review</span>} tight>
            {reviewQ.isPending ? (
              <Skeleton count={3} />
            ) : reviewQ.isError || reviewQ.data?.available === false ? (
              <EmptyState message="governance engine unavailable" />
            ) : (
              <div className="grid cols-2">
                <div>
                  <dl className="kv">
                    <InfoRow label="brier" value={formatNumber(reviewQ.data?.calibration?.brier ?? null, 4)} />
                    <InfoRow label="ece" value={formatNumber(reviewQ.data?.calibration?.ece ?? null, 4)} />
                    <InfoRow label="samples" value={String(reviewQ.data?.samples ?? 0)} />
                  </dl>
                  <div className="section-title" style={{ marginTop: 8 }}>
                    calibration buckets
                  </div>
                  <DataTable headers={[{ label: "bucket" }, { label: "n", num: true }, { label: "acc", num: true }]}>
                    {(reviewQ.data?.calibration?.buckets ?? []).map((b: Row, i: number) => (
                      <tr key={i}>
                        <td className="tiny">{str(b.bucket) ?? str(b.label) ?? `#${i}`}</td>
                        <td className="num tiny">{num(b.count) ?? num(b.n) ?? "—"}</td>
                        <td className="num tiny">{formatNumber(num(b.accuracy) ?? NaN, 3)}</td>
                      </tr>
                    ))}
                  </DataTable>
                </div>
                <div>
                  <div className="section-title">drift alerts</div>
                  {(reviewQ.data?.drift ?? []).length === 0 ? (
                    <EmptyState message="No drift alerts." />
                  ) : (
                    <JsonBlock value={reviewQ.data?.drift} maxChars={1800} />
                  )}
                  <div className="section-title" style={{ marginTop: 8 }}>
                    backtest-vs-live divergence
                  </div>
                  <JsonBlock value={reviewQ.data?.divergence} maxChars={1200} />
                </div>
              </div>
            )}
          </Panel>
        )}
      </div>

      {pending && (
        <ConfirmModal
          title={`Confirm: ${pending.title}`}
          danger={pending.danger}
          confirmLabel={pending.label}
          busy={cmd.state.running}
          onCancel={() => setPending(null)}
          onConfirm={async () => {
            const p = pending;
            setPending(null);
            await cmd.run(async () => {
              const v = commandVerdict(await p.run(actor.trim(), candModel));
              return { ok: v.ok, success: v.ok, message: `${p.label}: ${v.message}`, status: v.ok ? 200 : 500 };
            });
            after();
          }}
        >
          <div className="confirm-box">
            <div className="small">{pending.title}</div>
            <div className="row">
              <label className="tiny muted" htmlFor="pending-actor">
                actor
              </label>
              <input id="pending-actor" className="input" style={{ width: 200 }} value={actor} onChange={(e) => setActor(e.target.value)} />
            </div>
            <div className="note">Recorded in the governance event ledger. The backend refuses commands without an actor.</div>
          </div>
        </ConfirmModal>
      )}
    </div>
  );
}

/** Registry snapshot inline block (own query; keeps parent render cheap). */
function RegistryInline() {
  const regQ = useQuery({
    queryKey: ["governance", "registry"],
    queryFn: ({ signal }) => governanceQueries.registry(signal),
    retry: false,
  });
  if (regQ.isPending) return <Skeleton count={2} />;
  if (regQ.data?.available !== true) return <EmptyState message={regQ.data?.reason ?? "registry snapshot unavailable"} />;
  const cats = obj(obj(regQ.data.registry).categories);
  return (
    <div style={{ display: "grid", gap: 8 }}>
      {Object.entries(cats).map(([k, v]) => {
        const o = obj(v);
        return (
          <div key={k} style={{ border: "1px solid var(--border)", borderRadius: 6, padding: "6px 8px" }}>
            <div className="tiny muted">{k}</div>
            <div style={{ display: "flex", gap: 8, alignItems: "baseline", flexWrap: "wrap" }}>
              <span className="inline-mono tiny">{str(o.model_id) ?? "—"}</span>
              <StatusPill status={str(o.lifecycle_state)} />
              <span className="tiny faint">{str(o.artifact_hash)?.slice(0, 12) ?? ""}</span>
            </div>
          </div>
        );
      })}
      {Object.keys(cats).length === 0 && <JsonBlock value={regQ.data.registry} maxChars={1500} />}
    </div>
  );
}
