/**
 * TraceInspector — the forensic WHY panel (§30-35).
 *
 * Six question groups, every field fact-based from observed events
 * (§35 never infer a verdict from timing). `explainDecision` produces the
 * WHY prose deterministically from the bundle; labels that cannot be
 * answered from evidence render UNKNOWN / NOT OBSERVED / NOT REACHED /
 * INFORMATION GAP, never a guess. Opens for a selected live trace, a
 * historical row (bundle fetch) or a replay step — labelled LIVE /
 * HISTORICAL / REPLAY.
 */

import { useMemo } from "react";
import { useI18n } from "@/stores/i18nStore";
import { JsonBlock, StatusPill } from "@/features/research/ui/lane5Kit";
import {
  UNKNOWN,
  explainDecision,
  latencyBudget,
  mt5Reachability,
  modelContractEvidence,
} from "../traceGraph";
import type { TraceBundle, TraceEvent } from "../types";
import type { ViewMode } from "../store";

type T = (key: string, fallback: string, vars?: Record<string, string | number>) => string;

/**
 * Enum→label resolvers. DATA tokens stay verbatim in comparisons; these are
 * called only where the token is rendered as pixels. Unknown tokens (backend
 * strings, technical ids) pass through untouched.
 */
function statusLabel(t: T, status: string | null | undefined): string {
  switch (status) {
    case "PASS": return t("trace.verdict.pass", "PASS");
    case "REJECT": return t("trace.verdict.rejected", "REJECT");
    case "REJECTED": return t("trace.dstatus.rejected", "REJECTED");
    case "APPROVED": return t("trace.verdict.approved", "APPROVED");
    case "EXECUTED": return t("trace.verdict.executed", "EXECUTED");
    case "DISPATCHED": return t("trace.verdict.dispatched", "DISPATCHED");
    case "NO_TRADE": return t("trace.verdict.no_trade", "NO_TRADE");
    case "ERROR": return t("trace.verdict.error", "ERROR");
    case "FAILED": return t("trace.verdict.failed", "FAILED");
    case "TIMEOUT": return t("trace.verdict.timeout", "TIMEOUT");
    case "PENDING": return t("trace.dstatus.pending", "PENDING");
    case UNKNOWN: return t("trace.marker.unknown", "UNKNOWN");
    default: return status ?? "";
  }
}

function regimeLabel(t: T, regime: unknown): string {
  switch (regime) {
    case "MACRO_NEWS_FREEZE": return t("trace.regime.macro_news_freeze", "MACRO_NEWS_FREEZE");
    case "HIGH_SPREAD_CHOP": return t("trace.regime.high_spread_chop", "HIGH_SPREAD_CHOP");
    case "VOLATILITY_EXPANSION": return t("trace.regime.volatility_expansion", "VOLATILITY_EXPANSION");
    case "TRENDING_MOMENTUM": return t("trace.regime.trending_momentum", "TRENDING_MOMENTUM");
    case "RANGING_MEAN_REVERSION": return t("trace.regime.ranging_mean_reversion", "RANGING_MEAN_REVERSION");
    default: return typeof regime === "string" ? regime : "";
  }
}

interface Props {
  bundle: TraceBundle | null;
  source: ViewMode;
  replayEvent?: TraceEvent | null;
  onClose: () => void;
}

function Field({ label, value }: { label: string; value: unknown }) {
  const t = useI18n((s) => s.t);
  const text =
    value === undefined || value === null || (typeof value === "string" && !value.trim())
      ? t("trace.marker.unknown", "UNKNOWN")
      : String(value);
  const unknown = value === undefined || value === null || (typeof value === "string" && !value.trim());
  return (
    <div className={`dt-field ${unknown ? "unknown" : ""}`}>
      <span className="dt-field-label">{label}</span>
      <span className="dt-field-value">{text}</span>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="dt-insp-sec">
      <header className="dt-insp-sec-title">{title}</header>
      <div className="dt-insp-sec-body">{children}</div>
    </section>
  );
}

function stageDetail(events: TraceEvent[], stage: string): Record<string, unknown> {
  const ev = events.find((e) => e.stage === stage);
  return (ev?.detail as Record<string, unknown>) ?? {};
}

export function TraceInspector({ bundle, source, replayEvent, onClose }: Props) {
  const t = useI18n((s) => s.t);
  const events = bundle?.events ?? [];
  const why = useMemo(() => (bundle ? explainDecision(bundle) : null), [bundle]);
  const budget = useMemo(() => latencyBudget(events), [events]);
  const mt5 = useMemo(() => mt5Reachability(events), [events]);
  const contract = useMemo(() => modelContractEvidence(events), [events]);

  if (!bundle || !why) {
    return (
      <aside className="dt-inspector" aria-label={t("trace.inspector.aria", "Trace inspector")}>
        <div className="dt-insp-head">
          <span className="dt-insp-title">{t("trace.inspector.title", "Trace Inspector")}</span>
          <button className="dt-insp-close" onClick={onClose} aria-label={t("trace.inspector.close", "Close inspector")}>x</button>
        </div>
        <div className="dt-insp-empty">
          <div className="dt-insp-empty-title">{t("trace.inspector.empty_title", "NO TRACE SELECTED")}</div>
          <div className="dt-insp-empty-sub">
            {t("trace.inspector.empty_sub", "Select a decision row or an event to reconstruct its causal chain from observed events only.")}
          </div>
        </div>
      </aside>
    );
  }

  const summary = bundle.summary;
  const market = stageDetail(events, "MARKET");
  const model = stageDetail(events, "INFERENCE");
  const regime = stageDetail(events, "REGIME");
  const policy = stageDetail(events, "POLICY");
  const risk = stageDetail(events, "RISK");
  const exec = stageDetail(events, "EXECUTION");
  const order = stageDetail(events, "ORDER");
  const sourceLabel =
    source === "LIVE"
      ? t("trace.source.live", "LIVE (runtime)")
      : source === "REPLAY"
        ? t("trace.source.replay", "REPLAY")
        : t("trace.source.historical", "HISTORICAL (archive)");

  return (
    <aside className="dt-inspector" aria-label={t("trace.inspector.aria", "Trace inspector")}>
      <div className="dt-insp-head">
        <span className="dt-insp-title">{t("trace.inspector.title", "Trace Inspector")}</span>
        <span className={`dt-src-badge ${source.toLowerCase()}`}>{sourceLabel}</span>
        <button className="dt-insp-close" onClick={onClose} aria-label={t("trace.inspector.close", "Close inspector")}>x</button>
      </div>

      <div className="dt-insp-id">
        <span className="dt-insp-id-label">{t("trace.inspector.decision", "DECISION")}</span>
        <code>{bundle.decision_id ?? bundle.trace_id ?? t("trace.marker.unknown", "UNKNOWN")}</code>
        <StatusPill status={statusLabel(t, why.verdict)} />
      </div>

      <Section title={t("trace.inspector.sec_identity", "1 · Decision identity")}>
        <Field label={t("trace.inspector.f_decision_id", "decision_id")} value={bundle.decision_id} />
        <Field label={t("trace.inspector.f_trace_id", "trace_id")} value={bundle.trace_id} />
        <Field label={t("trace.inspector.f_symbol", "symbol")} value={summary?.symbol ?? market.symbol} />
        <Field label={t("trace.inspector.f_recorded_at", "recorded_at")} value={summary?.recorded_at} />
        <Field label={t("trace.inspector.f_verdict", "verdict")} value={statusLabel(t, why.verdict)} />
        <Field label={t("trace.inspector.f_e2e_latency", "end-to-end latency")} value={budget.total_us != null ? `${(budget.total_us / 1000).toFixed(2)}ms` : null} />
      </Section>

      <Section title={t("trace.inspector.sec_model", "2 · Model provenance")}>
        <Field label={t("trace.inspector.f_model_id", "model_id")} value={contract.modelId} />
        <Field label={t("trace.inspector.f_model_version", "model_version")} value={contract.modelVersion} />
        <Field label={t("trace.inspector.f_artifact", "artifact fingerprint")} value={contract.artifact} />
        <Field label={t("trace.inspector.f_contract_evidence", "contract evidence")} value={t("trace.inspector.contract_value", "{contract} (source: {source})", { contract: contract.contract, source: contract.source })} />
        <Field label={t("trace.inspector.f_feature_dim", "feature_dim")} value={contract.featureDim} />
        <Field label={t("trace.inspector.f_schema_hash", "schema_hash")} value={contract.schemaHash} />
        <Field label={t("trace.inspector.f_prediction", "prediction")} value={model.prediction ?? model.signal} />
        <Field label={t("trace.inspector.f_probabilities", "probabilities")} value={contract.probabilities ? JSON.stringify(contract.probabilities) : model.probability} />
      </Section>

      <Section title={t("trace.inspector.sec_regime", "3 · Regime & gates")}>
        <Field label={t("trace.inspector.f_regime", "regime")} value={regimeLabel(t, regime.regime)} />
        <Field label={t("trace.inspector.f_policy_branch", "policy branch")} value={policy.branch ?? policy.signal} />
        <Field label={t("trace.inspector.f_rule", "rule")} value={why.rule} />
        <Field label={t("trace.inspector.f_actual_vs_required", "actual vs required")} value={why.actualValue != null && why.requiredValue != null ? `${why.actualValue} ${why.operator ?? "?"} ${why.requiredValue}` : null} />
        <Field label={t("trace.inspector.f_not_reached", "not reached")} value={why.provenanceGaps.length ? why.provenanceGaps.join(", ") : null} />
      </Section>

      <Section title={t("trace.inspector.sec_risk", "4 · Risk verdict")}>
        <Field label={t("trace.inspector.f_risk_verdict", "risk verdict")} value={risk.verdict ? statusLabel(t, String(risk.verdict)) : risk.reason} />
        <Field label={t("trace.inspector.f_rejection_stage", "rejection stage")} value={why.rejectionStage} />
        <Field label={t("trace.inspector.f_cause", "cause")} value={why.cause} />
      </Section>

      <Section title={t("trace.inspector.sec_exec", "5 · Execution evidence")}>
        <Field label={t("trace.inspector.f_exec_status", "execution status")} value={statusLabel(t, (order.status ?? exec.status) as string | null | undefined)} />
        <Field label={t("trace.inspector.f_order_id", "order_id")} value={order.order_id} />
        <Field label={t("trace.inspector.f_gateway_reached", "gateway reached")} value={mt5.reached ? t("trace.inspector.gateway_reached_yes", "YES (response evidence observed)") : t("trace.inspector.gateway_reached_no", "NO RESPONSE OBSERVED")} />
        <Field label={t("trace.inspector.f_gateway", "gateway")} value={mt5.gateway} />
        <Field label={t("trace.inspector.f_gateway_state", "gateway state")} value={mt5.state} />
        <Field label={t("trace.inspector.f_ticket", "ticket")} value={mt5.ticket} />
        <Field label={t("trace.inspector.f_latency_by_stage", "latency by stage")} value={budget.byStage.map((b) => `${b.stage} ${(b.us / 1000).toFixed(2)}ms`).join(" · ") || null} />
      </Section>

      <Section title={t("trace.inspector.sec_why", "6 · Outcome (deterministic WHY)")}>
        <div className={`dt-why ${why.verdict === "REJECTED" ? "reject" : why.verdict === "APPROVED" || why.verdict === "EXECUTED" ? "pass" : "neutral"}`}>
          <div className="dt-why-status">{statusLabel(t, why.verdict)}</div>
          <ul className="dt-why-list">
            {why.steps.map((s) => (
              <li key={s.stage}>
                <code>{s.stage}</code>
                {s.status ? ` → ${statusLabel(t, s.status)}` : ""}
                {s.reason ? ` — ${s.reason}` : ""}
                {s.notReached.length ? t("trace.inspector.why_notreached", " · NOT REACHED: {stages}", { stages: s.notReached.join(", ") }) : ""}
              </li>
            ))}
            {why.cause ? <li>{t("trace.inspector.why_cause", "CAUSE: {cause}", { cause: why.cause })}</li> : null}
          </ul>
        </div>
      </Section>

      {why.provenanceGaps.length ? (
        <Section title={t("trace.inspector.sec_gaps", "Why downstream was NOT reached")}>
          <div className="dt-notreached">
            {why.provenanceGaps.map((s) => (
              <div key={s} className="dt-notreached-row">
                <code>{s}</code>
                <span>{t("trace.inspector.notreached_hint", "no observed event for this stage (PROVENANCE GAP / not reached)")}</span>
              </div>
            ))}
          </div>
        </Section>
      ) : null}

      <Section title={t("trace.inspector.sec_source", "SOURCE (fact-based, not inferred)")}>
        <div className="dt-source">
          <Field label={t("trace.inspector.evidence_basis", "evidence basis")} value={t("trace.inspector.evidence_count", "{n} observed event(s)", { n: why.evidenceCount })} />
          <Field label={t("trace.inspector.selected_frame", "selected frame")} value={replayEvent ? `#${replayEvent.sequence}` : t("trace.inspector.frame_live", "live")} />
        </div>
        <JsonBlock
          value={events.map((e) => ({ stage: e.stage, at: e.timestamp, detail: e.detail }))}
          maxChars={3000}
        />
      </Section>
    </aside>
  );
}
