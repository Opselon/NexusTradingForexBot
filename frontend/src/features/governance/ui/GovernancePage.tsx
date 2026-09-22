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
import { useI18n } from "@/stores/i18nStore";
import { CommandResultLine, DistBars, FreshnessCaption, GateStepper, InfoRow, JsonBlock, StatusPill } from "../../research/ui/lane5Kit";
import { arr, bool, commandVerdict, num, obj, str, type Row } from "../model";
import { governanceQueries, governanceUseCases } from "../useCases";

type Tab = "ladder" | "ledger" | "experience" | "review";

/** Command ids — titles/labels resolve at render time so language switches apply. */
type CmdId = "freeze" | "unfreeze" | "disable" | "reconcile" | "selfheal";

interface PendingCommand {
  id: CmdId;
  danger: boolean;
  needsModel: boolean;
  run: (actor: string, modelId: string) => Promise<unknown>;
}

export default function GovernancePage(props: ShellPageProps) {
  void props;
  const [tab, setTab] = useState<Tab>("ladder");
  const [eventFilter, setEventFilter] = useState("");
  const [actor, setActor] = useState("operator");
  const [pending, setPending] = useState<PendingCommand | null>(null);
  const t = useI18n((s) => s.t);
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

  const ask = (id: CmdId, danger: boolean, needsModel: boolean, run: PendingCommand["run"]) =>
    setPending({ id, danger, needsModel, run });

  const status = statusQ.data?.available === true ? statusQ.data : null;
  const ladder = status ? governanceUseCases.ladder(status) : [];
  const candModel = status?.candidate?.model_id ?? "";
  const frozen = bool(status?.promotion?.frozen) ?? false;
  const events = governanceUseCases.ledgerRows(arr(eventsQ.data?.events));
  // i18n lookups (literal keys; rebuilt every render so a language switch applies)
  const ladderLabels: Record<string, string> = {
    champion: t("governance.ladder.champion", "Champion"),
    challenger: t("governance.ladder.challenger", "Challenger"),
    gates: t("governance.ladder.gates", "Gates"),
    approved: t("governance.ladder.approved", "Approved"),
    promotion: t("governance.ladder.promotion", "Promotion eligible"),
  };
  const cmdText: Record<CmdId, { title: string; label: string }> = {
    freeze: { title: t("governance.cmd.freeze_title", "Freeze promotions"), label: t("governance.cmd.freeze_label", "Freeze") },
    unfreeze: { title: t("governance.cmd.unfreeze_title", "Unfreeze promotions"), label: t("governance.cmd.unfreeze_label", "Unfreeze") },
    disable: {
      title: t("governance.cmd.disable_title", "Disable candidate {model}", { model: candModel }),
      label: t("governance.cmd.disable_label", "Disable candidate"),
    },
    reconcile: { title: t("governance.cmd.reconcile_title", "Reconcile model registry"), label: t("governance.cmd.reconcile_label", "Reconcile") },
    selfheal: { title: t("governance.cmd.selfheal_title", "Self-heal experience intelligence"), label: t("governance.cmd.selfheal_label", "Rebuild") },
  };

  return (
    <div>
      <div className="page-head" style={{ display: "flex", alignItems: "baseline", gap: 10, flexWrap: "wrap" }}>
        <h2>{t("nav.feature.governance", "Governance")}</h2>
        <span className="muted small">{t("governance.page.subtitle", "model promotion ladder · decision ledger · experience intelligence")}</span>
        <FreshnessCaption
          timestamp={expSummaryQ.data?.fetched_at ?? undefined}
          source={t("governance.page.source", "experience summary")}
          isFetching={statusQ.isFetching}
          error={statusQ.isError}
        />
      </div>

      <div className="grid cols-4">
        <MetricCard
          label={t("governance.metric.champion", "Champion")}
          value={<span className="tiny inline-mono">{status?.champion?.model_id ?? "—"}</span>}
          sub={t("governance.metric.champion_sub", "schema {schema} · v{version}", {
            schema: status?.champion?.schema ?? "—",
            version: status?.champion?.version ?? "—",
          })}
        />
        <MetricCard
          label={t("governance.metric.candidate", "Candidate")}
          value={<StatusPill status={status?.candidate?.status ?? "NONE"} />}
          sub={status?.candidate?.model_id ?? t("governance.metric.no_challenger", "no challenger recorded")}
        />
        <MetricCard
          label={t("governance.metric.promotion_state", "Promotion state")}
          value={<StatusBadge status={frozen ? "BLOCKED" : status?.promotion?.approved ? "PENDING" : status?.promotion?.eligible ? "ACTIVE" : "IDLE"} label={frozen ? t("governance.metric.frozen_label", "frozen by emergency gate") : undefined} />}
          tone={frozen ? "neg" : "dim"}
          sub={frozen ? t("governance.metric.freeze_active", "EMERGENCY FREEZE ACTIVE") : t("governance.metric.eligible_source", "eligible/approved from governance/status")}
        />
        <MetricCard
          label={t("governance.metric.active_strategies", "Active strategies")}
          value={String(expSummaryQ.data?.active_strategies ?? 0)}
          sub={t("governance.metric.recorded_experiences", "recorded experiences: {n}", { n: formatNumber(expSummaryQ.data?.recorded_experiences ?? 0, 0) })}
        />
      </div>

      <Panel title={t("governance.panel.ladder", "Promotion ladder (backend-decided)")} accent tight>
        {statusQ.isPending ? (
          <Skeleton count={2} />
        ) : !status ? (
          <EmptyState
            message={t("governance.empty.engine_unavailable", "Model governance engine unavailable")}
            hint={t("governance.empty.engine_unavailable_hint", "/api/models/governance/status answered available:false — no ladder to draw.")}
          />
        ) : (
          <GateStepper
            gates={ladder.map((s) => ({
              name: ladderLabels[s.id] ?? s.label,
              status: s.active ? (s.id === "promotion" ? s.state : "PASS") : s.state === "NONE" ? "NOT_RUN" : s.state,
              reason: s.state,
            }))}
          />
        )}
      </Panel>

      <div style={{ marginBlock: 12, display: "grid", gap: 8 }}>
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center" }}>
          <label className="tiny muted" htmlFor="gov-actor">
            {t("governance.field.actor_required", "actor (required by backend)")}
          </label>
          <input id="gov-actor" className="input" style={{ width: 170 }} value={actor} onChange={(e) => setActor(e.target.value)} />
          <button
            className="btn small danger"
            disabled={cmd.state.running || !status}
            onClick={() => ask("freeze", true, false, (a) => governanceUseCases.freeze(a, "ui:lane5 emergency freeze"))}
          >
            ❄ {t("governance.cmd.freeze_title", "Freeze promotions")}
          </button>
          <button
            className="btn small"
            disabled={cmd.state.running || !frozen}
            onClick={() => ask("unfreeze", true, false, (a) => governanceUseCases.unfreeze(a, "ui:lane5 release freeze"))}
          >
            ⚑ {t("governance.cmd.unfreeze_label", "Unfreeze")}
          </button>
          <button
            className="btn small danger"
            disabled={cmd.state.running || !candModel}
            onClick={() =>
              ask("disable", true, false, (a) => governanceUseCases.disableCandidate(a, candModel, "ui:lane5 operator disable"))
            }
          >
            ⛔ {t("governance.cmd.disable_label", "Disable candidate")}
          </button>
          <button className="btn small" disabled={cmd.state.running} onClick={() => ask("reconcile", false, false, () => governanceUseCases.reconcileRegistry())}>
            {t("governance.cmd.reconcile_btn", "Reconcile registry")}
          </button>
          <button className="btn small danger" disabled={cmd.state.running} onClick={() => ask("selfheal", true, false, () => governanceUseCases.selfHealExperience())}>
            {t("governance.cmd.selfheal_btn", "Self-heal experience")}
          </button>
        </div>
        <div className="tiny muted">
          {t(
            "governance.note.approve",
            "Approve/execute promotion intentionally stays behind the preview + token flow (approval_token is minted by the approve transition); this console exposes the emergency set + rebuild commands only. The backend decides every outcome.",
          )}
        </div>
        <CommandResultLine state={cmd.state} />
      </div>

      <Segmented
        options={[
          { id: "ladder" as const, label: t("governance.tab.registry_ladder", "Registry & ladder") },
          { id: "ledger" as const, label: t("governance.tab.decision_ledger", "Decision ledger") },
          { id: "experience" as const, label: t("governance.tab.experience", "Experience") },
          { id: "review" as const, label: t("governance.tab.calibration", "Calibration review") },
        ]}
        value={tab}
        onChange={setTab}
      />

      <div style={{ marginTop: 12, display: "grid", gap: 12 }}>
        {tab === "ladder" && (
          <Panel title={t("governance.panel.gate_matrix", "Gate matrix (governance/status.gates)")} tight>
            {status ? (
              <DataTable headers={[{ label: t("governance.th.gate", "gate") }, { label: t("governance.th.verdict", "verdict") }]}>
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
              <EmptyState message={t("governance.empty.no_status", "no governance status")} />
            )}
            <div style={{ marginTop: 10 }}>
              <Panel title={t("governance.panel.registry_snapshot", "Registry reconciliation snapshot")} tight>
                <RegistryInline />
              </Panel>
            </div>
          </Panel>
        )}

        {tab === "ledger" && (
          <>
            <Panel
              title={t("governance.panel.event_ledger", "Governance event ledger (append-only)")}
              right={
                <input
                  className="input"
                  style={{ width: 180 }}
                  placeholder={t("governance.field.event_filter", "filter by event type")}
                  value={eventFilter}
                  onChange={(e) => setEventFilter(e.target.value)}
                />
              }
              tight
            >
              {eventsQ.isPending ? (
                <Skeleton count={4} />
              ) : eventsQ.isError ? (
                <EmptyState message={t("governance.empty.events_failed", "events endpoint failed")} />
              ) : events.length === 0 ? (
                <EmptyState message={t("governance.empty.events_none", "No governance events recorded.")} />
              ) : (
                <DataTable
                  headers={[
                    { label: t("governance.th.at", "at") },
                    { label: t("governance.th.event", "event") },
                    { label: t("governance.th.model", "model") },
                    { label: t("governance.th.actor", "actor") },
                    { label: t("governance.th.reason", "reason") },
                  ]}
                >
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
            <Panel title={t("governance.panel.audits", "Immutable promotion/rollback audits")} tight>
              {auditsQ.isPending ? (
                <Skeleton count={2} />
              ) : (
                arr(auditsQ.data?.audits).length === 0 ? (
                  <EmptyState message={t("governance.empty.audit_rows", "No audit rows.")} />
                ) : (
                <DataTable
                  headers={[
                    { label: t("governance.th.at", "at") },
                    { label: t("governance.th.action", "action") },
                    { label: t("governance.th.model", "model") },
                    { label: t("governance.th.actor", "actor") },
                  ]}
                >
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
            <Panel title={t("governance.panel.experience_verdict", "Last pre-trade experience verdict")} tight>
              {expDecisionQ.isPending ? (
                <Skeleton count={2} />
              ) : expDecisionQ.data?.available === false || !expDecisionQ.data?.decision ? (
                <EmptyState
                  message={t("governance.empty.experience_decision", "No experience decision recorded yet.")}
                  hint={t("governance.empty.experience_decision_hint", "engine answers {available:false} until the first pre-trade evaluation")}
                />
              ) : (
                <JsonBlock value={expDecisionQ.data.decision} maxChars={2500} />
              )}
            </Panel>
            <Panel title={t("governance.panel.experience_census", "Experience ledger census")} tight>
              <dl className="kv" style={{ marginBottom: 10 }}>
                <InfoRow label={t("governance.kv.enabled", "enabled")} value={expSummaryQ.data?.enabled ? "true" : "false"} />
                <InfoRow label={t("governance.kv.recorded", "recorded")} value={formatNumber(expSummaryQ.data?.recorded_experiences ?? 0, 0)} />
                <InfoRow label={t("governance.kv.retired", "retired strategies")} value={String(expSummaryQ.data?.retired_strategies ?? 0)} />
              </dl>
              <DistBars
                rows={Object.entries(obj(expSummaryQ.data?.lifecycle_counts)).map(([k, v]) => ({ label: k, count: num(v) ?? 0 }))}
                tone="var(--green)"
              />
            </Panel>
            <Panel title={t("governance.panel.provenance", "Model provenance ({n})", { n: arr(expModelsQ.data).length })} tight>
              {expModelsQ.isPending ? (
                <Skeleton count={2} />
              ) : arr(expModelsQ.data).length === 0 ? (
                <EmptyState message={t("governance.empty.provenance", "No registered model provenance rows.")} />
              ) : (
                <DataTable
                  headers={[
                    { label: t("governance.th.model", "model") },
                    { label: t("governance.th.schema", "schema") },
                    { label: "dim", num: true },
                    { label: t("governance.th.created", "created") },
                  ]}
                >
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
            <Panel title={t("governance.panel.strategy_scores", "Derived strategy scores ({n})", { n: arr(expStrategiesQ.data).length })} tight>
              {expStrategiesQ.isPending ? (
                <Skeleton count={2} />
              ) : arr(expStrategiesQ.data).length === 0 ? (
                <EmptyState message={t("governance.empty.strategy_rows", "No derived intelligence rows — run experience self-heal if the ledger is non-empty.")} />
              ) : (
                <DataTable
                  headers={[
                    { label: t("governance.th.strategy", "strategy") },
                    { label: t("governance.th.state", "state") },
                    { label: "score", num: true },
                    { label: t("governance.th.updated", "updated") },
                  ]}
                >
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
          <Panel
            title={t("governance.panel.review", "Calibration + drift review (governance evidence)")}
            right={<span className="tiny muted">/api/models/governance/review</span>}
            tight
          >
            {reviewQ.isPending ? (
              <Skeleton count={3} />
            ) : reviewQ.isError || reviewQ.data?.available === false ? (
              <EmptyState message={t("governance.empty.review_unavailable", "governance engine unavailable")} />
            ) : (
              <div className="grid cols-2">
                <div>
                  <dl className="kv">
                    <InfoRow label="brier" value={formatNumber(reviewQ.data?.calibration?.brier ?? null, 4)} />
                    <InfoRow label="ece" value={formatNumber(reviewQ.data?.calibration?.ece ?? null, 4)} />
                    <InfoRow label={t("governance.kv.samples", "samples")} value={String(reviewQ.data?.samples ?? 0)} />
                  </dl>
                  <div className="section-title" style={{ marginTop: 8 }}>
                    {t("governance.section.calibration_buckets", "calibration buckets")}
                  </div>
                  <DataTable headers={[{ label: t("governance.th.bucket", "bucket") }, { label: "n", num: true }, { label: "acc", num: true }]}>
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
                  <div className="section-title">{t("governance.section.drift_alerts", "drift alerts")}</div>
                  {(reviewQ.data?.drift ?? []).length === 0 ? (
                    <EmptyState message={t("governance.empty.drift", "No drift alerts.")} />
                  ) : (
                    <JsonBlock value={reviewQ.data?.drift} maxChars={1800} />
                  )}
                  <div className="section-title" style={{ marginTop: 8 }}>
                    {t("governance.section.divergence", "backtest-vs-live divergence")}
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
          title={t("governance.confirm.title", "Confirm: {title}", { title: cmdText[pending.id].title })}
          danger={pending.danger}
          confirmLabel={cmdText[pending.id].label}
          busy={cmd.state.running}
          onCancel={() => setPending(null)}
          onConfirm={async () => {
            const p = pending;
            setPending(null);
            await cmd.run(async () => {
              const v = commandVerdict(await p.run(actor.trim(), candModel));
              return {
                ok: v.ok,
                success: v.ok,
                message: t("governance.result.line", "{label}: {message}", { label: cmdText[p.id].label, message: v.message }),
                status: v.ok ? 200 : 500,
              };
            });
            after();
          }}
        >
          <div className="confirm-box">
            <div className="small">{cmdText[pending.id].title}</div>
            <div className="row">
              <label className="tiny muted" htmlFor="pending-actor">
                {t("governance.field.actor", "actor")}
              </label>
              <input id="pending-actor" className="input" style={{ width: 200 }} value={actor} onChange={(e) => setActor(e.target.value)} />
            </div>
            <div className="note">{t("governance.confirm.note", "Recorded in the governance event ledger. The backend refuses commands without an actor.")}</div>
          </div>
        </ConfirmModal>
      )}
    </div>
  );
}

/** Registry snapshot inline block (own query; keeps parent render cheap). */
function RegistryInline() {
  const t = useI18n((s) => s.t);
  const regQ = useQuery({
    queryKey: ["governance", "registry"],
    queryFn: ({ signal }) => governanceQueries.registry(signal),
    retry: false,
  });
  if (regQ.isPending) return <Skeleton count={2} />;
  if (regQ.data?.available !== true)
    return <EmptyState message={regQ.data?.reason ?? t("governance.empty.registry", "registry snapshot unavailable")} />;
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
