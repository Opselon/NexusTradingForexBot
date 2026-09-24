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

interface Props {
  bundle: TraceBundle | null;
  source: ViewMode;
  replayEvent?: TraceEvent | null;
  onClose: () => void;
}

function Field({ label, value }: { label: string; value: unknown }) {
  const text =
    value === undefined || value === null || (typeof value === "string" && !value.trim())
      ? UNKNOWN
      : String(value);
  const unknown = text === UNKNOWN;
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
  const events = bundle?.events ?? [];
  const why = useMemo(() => (bundle ? explainDecision(bundle) : null), [bundle]);
  const budget = useMemo(() => latencyBudget(events), [events]);
  const mt5 = useMemo(() => mt5Reachability(events), [events]);
  const contract = useMemo(() => modelContractEvidence(events), [events]);

  if (!bundle || !why) {
    return (
      <aside className="dt-inspector" aria-label="Trace inspector">
        <div className="dt-insp-head">
          <span className="dt-insp-title">Trace Inspector</span>
          <button className="dt-insp-close" onClick={onClose} aria-label="Close inspector">x</button>
        </div>
        <div className="dt-insp-empty">
          <div className="dt-insp-empty-title">NO TRACE SELECTED</div>
          <div className="dt-insp-empty-sub">
            Select a decision row or an event to reconstruct its causal chain from observed
            events only.
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
    source === "LIVE" ? "LIVE (runtime)" : source === "REPLAY" ? "REPLAY" : "HISTORICAL (archive)";

  return (
    <aside className="dt-inspector" aria-label="Trace inspector">
      <div className="dt-insp-head">
        <span className="dt-insp-title">Trace Inspector</span>
        <span className={`dt-src-badge ${source.toLowerCase()}`}>{sourceLabel}</span>
        <button className="dt-insp-close" onClick={onClose} aria-label="Close inspector">x</button>
      </div>

      <div className="dt-insp-id">
        <span className="dt-insp-id-label">DECISION</span>
        <code>{bundle.decision_id ?? bundle.trace_id ?? UNKNOWN}</code>
        <StatusPill status={why.verdict} />
      </div>

      <Section title="1 · Decision identity">
        <Field label="decision_id" value={bundle.decision_id} />
        <Field label="trace_id" value={bundle.trace_id} />
        <Field label="symbol" value={summary?.symbol ?? market.symbol} />
        <Field label="recorded_at" value={summary?.recorded_at} />
        <Field label="verdict" value={why.verdict} />
        <Field label="end-to-end latency" value={budget.total_us != null ? `${(budget.total_us / 1000).toFixed(2)}ms` : null} />
      </Section>

      <Section title="2 · Model provenance">
        <Field label="model_id" value={contract.modelId} />
        <Field label="model_version" value={contract.modelVersion} />
        <Field label="artifact fingerprint" value={contract.artifact} />
        <Field label="contract evidence" value={`${contract.contract} (source: ${contract.source})`} />
        <Field label="feature_dim" value={contract.featureDim} />
        <Field label="schema_hash" value={contract.schemaHash} />
        <Field label="prediction" value={model.prediction ?? model.signal} />
        <Field label="probabilities" value={contract.probabilities ? JSON.stringify(contract.probabilities) : model.probability} />
      </Section>

      <Section title="3 · Regime & gates">
        <Field label="regime" value={regime.regime} />
        <Field label="policy branch" value={policy.branch ?? policy.signal} />
        <Field label="rule" value={why.rule} />
        <Field label="actual vs required" value={why.actualValue != null && why.requiredValue != null ? `${why.actualValue} ${why.operator ?? "?"} ${why.requiredValue}` : null} />
        <Field label="not reached" value={why.provenanceGaps.length ? why.provenanceGaps.join(", ") : null} />
      </Section>

      <Section title="4 · Risk verdict">
        <Field label="risk verdict" value={risk.verdict ?? risk.reason} />
        <Field label="rejection stage" value={why.rejectionStage} />
        <Field label="cause" value={why.cause} />
      </Section>

      <Section title="5 · Execution evidence">
        <Field label="execution status" value={order.status ?? exec.status} />
        <Field label="order_id" value={order.order_id} />
        <Field label="gateway reached" value={mt5.reached ? "YES (response evidence observed)" : "NO RESPONSE OBSERVED"} />
        <Field label="gateway" value={mt5.gateway} />
        <Field label="gateway state" value={mt5.state} />
        <Field label="ticket" value={mt5.ticket} />
        <Field label="latency by stage" value={budget.byStage.map((b) => `${b.stage} ${(b.us / 1000).toFixed(2)}ms`).join(" · ") || null} />
      </Section>

      <Section title="6 · Outcome (deterministic WHY)">
        <div className={`dt-why ${why.verdict === "REJECTED" ? "reject" : why.verdict === "APPROVED" || why.verdict === "EXECUTED" ? "pass" : "neutral"}`}>
          <div className="dt-why-status">{why.verdict}</div>
          <ul className="dt-why-list">
            {why.steps.map((s) => (
              <li key={s.stage}>
                <code>{s.stage}</code>
                {s.status ? ` → ${s.status}` : ""}
                {s.reason ? ` — ${s.reason}` : ""}
                {s.notReached.length ? ` · NOT REACHED: ${s.notReached.join(", ")}` : ""}
              </li>
            ))}
            {why.cause ? <li>CAUSE: {why.cause}</li> : null}
          </ul>
        </div>
      </Section>

      {why.provenanceGaps.length ? (
        <Section title="Why downstream was NOT reached">
          <div className="dt-notreached">
            {why.provenanceGaps.map((s) => (
              <div key={s} className="dt-notreached-row">
                <code>{s}</code>
                <span>no observed event for this stage (PROVENANCE GAP / not reached)</span>
              </div>
            ))}
          </div>
        </Section>
      ) : null}

      <Section title="SOURCE (fact-based, not inferred)">
        <div className="dt-source">
          <Field label="evidence basis" value={`${why.evidenceCount} observed event(s)`} />
          <Field label="selected frame" value={replayEvent ? `#${replayEvent.sequence}` : "live"} />
        </div>
        <JsonBlock
          value={events.map((e) => ({ stage: e.stage, at: e.timestamp, detail: e.detail }))}
          maxChars={3000}
        />
      </Section>
    </aside>
  );
}
