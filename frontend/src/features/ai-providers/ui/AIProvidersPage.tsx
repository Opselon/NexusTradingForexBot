/**
 * features/ai-providers/ui/AIProvidersPage.tsx
 *
 * AI Provider Control Center (ECOSYSTEM-001, Sections 19–26).
 *
 * What this panel IS: the operator surface for the provider ecosystem. It shows
 * which provider is ACTIVE, its health, the Test Centre results, the decision
 * pipeline for one snapshot, and the side-by-side comparison.
 *
 * What this panel NEVER does:
 *   - edit or display an API key value. The backend holds a reference; this UI
 *     only ever sees `has_secret` (Section 37).
 *   - fabricate rationale. Every recommendation shown maps to a field the
 *     backend actually returned (Section 23).
 *   - claim an AI executed a trade. The labels are deliberately
 *     AI Recommendation → Policy Decision → Risk Decision (Section 57).
 *
 * Presentation only: every value is backend-authoritative. Styled via
 * ./ai-providers.css on the shared theme tokens — no CSS framework.
 */

import { useCallback, useEffect, useMemo, useState } from "react";

import type { ShellPageProps } from "@/app/featureModule";
import { ErrorState, LoadingState, MetricCard, Panel, ProbBar, StatusBadge } from "@/components/primitives";

import { aiProvidersApi } from "../api";
import type {
  CompareResponse,
  DecisionRecord,
  ProviderEntry,
  ProviderListResponse,
  ProviderTestResult,
} from "../model";
import "./ai-providers.css";

const REFRESH_MS = 5000;

/** The pipeline the operator is meant to read, top to bottom (Section 68). */
const PIPELINE = [
  "Position Snapshot",
  "Internal ML",
  "System One",
  "OpenRouter",
  "Response Normalize",
  "Decision Fusion",
  "Deterministic Policy",
  "Risk Engine",
  "Broker Validation",
] as const;

function classNames(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(" ");
}

/** Health dot class for the provider list (Section 19). */
function healthClass(p: ProviderEntry): string {
  if (!p.enabled) return "off";
  if (p.health && !p.health.available) return "bad";
  if (p.circuit_breaker_state === "OPEN") return "bad";
  if (p.failure_count > 0) return "warn";
  if (p.health && p.health.available) return "good";
  return "unknown";
}

export default function AIProvidersPage(_props: ShellPageProps) {
  const [data, setData] = useState<ProviderListResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [test, setTest] = useState<ProviderTestResult | null>(null);
  const [compare, setCompare] = useState<CompareResponse | null>(null);
  const [trace, setTrace] = useState<DecisionRecord | null>(null);
  const [snapshot, setSnapshot] = useState<Record<string, unknown> | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(async (signal?: AbortSignal) => {
    try {
      const res = await aiProvidersApi.list(signal);
      setData(res);
      setError(null);
    } catch (e) {
      if (!signal) setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    const timer = window.setInterval(() => void load(), REFRESH_MS);
    return () => {
      controller.abort();
      window.clearInterval(timer);
    };
  }, [load]);

  const act = data;
  const providers = data?.providers ?? [];

  /** Run the Test Center for one provider (Section 22). A TEST, never a trade. */
  const runTest = useCallback(async (providerId: string) => {
    setBusy(providerId);
    setNotice(null);
    try {
      setTest(await aiProvidersApi.test(providerId));
    } catch (e) {
      setTest(null);
      setNotice(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }, []);

  /** Evaluate the SIMULATED sample snapshot (Sections 22, 56). */
  const evaluate = useCallback(async () => {
    setBusy("evaluate");
    setNotice(null);
    try {
      const res = await aiProvidersApi.evaluate({ simulated: true });
      setSnapshot(res.request ?? null);
      setTrace(res.decision);
    } catch (e) {
      setNotice(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }, []);

  /** Side-by-side comparison of every enabled provider (Section 24). */
  const runCompare = useCallback(async () => {
    setBusy("compare");
    setNotice(null);
    try {
      setCompare(await aiProvidersApi.compare({ simulated: true }));
    } catch (e) {
      setNotice(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }, []);

  /** The explicit switch flow with preconditions (Section 26). */
  const switchTo = useCallback(
    async (primary: string, mode = "HYBRID") => {
      setBusy(`switch:${primary}`);
      setNotice(null);
      try {
        const res = await aiProvidersApi.switch({ primary, mode: mode as never });
        setNotice(
          res.switched
            ? `Switched to ${res.active_provider} (${res.decision_mode})` +
                (res.restart_required ? " — restart required to activate." : "")
            : "Switch refused: " + res.preconditions.filter((p) => !p.passed).map((p) => p.detail).join("; "),
        );
        await load();
      } catch (e) {
        setNotice(e instanceof Error ? e.message : String(e));
      } finally {
        setBusy(null);
      }
    },
    [load],
  );

  /** Probabilities from the trace, for the evidence bars. */
  const evidenceBars = useMemo(() => {
    if (!trace) return [];
    return Object.entries(trace.evidence).flatMap(([providerId, ev]) => {
      const p = ev as { p_hold?: number; p_close?: number; action?: string };
      return [
        { label: `${providerId} hold`, value: p.p_hold ?? null, tone: "flat" as const },
        { label: `${providerId} close`, value: p.p_close ?? null, tone: "sell" as const },
      ];
    });
  }, [trace]);

  if (error) return <ErrorState message={error} onRetry={() => void load()} />;
  if (!data) return <LoadingState label="Loading AI providers…" />;

  return (
    <div className="aipage">
      {/* ---- header: what is ACTIVE right now (Section 53) ------------------ */}
      <Panel title="AI Providers">
        <div className="aipage-active">
          <MetricCard label="Active Provider" value={act?.active_provider ?? "—"} />
          <MetricCard label="Decision Mode" value={act?.decision_mode ?? "—"} />
          <MetricCard label="Fallback" value={act?.fallback_provider ?? "none"} />
          <MetricCard label="Shadow" value={act?.shadow_provider ?? "none"} />
          <MetricCard label="Contract" value={data.contract_version} />
          <MetricCard label="Policy" value={data.policy_version} />
        </div>
        {notice && <div className="aipage-notice">{notice}</div>}
      </Panel>

      {/* ---- provider list (Section 19) ------------------------------------- */}
      <Panel title="Providers">
        <table className="aipage-table">
          <thead>
            <tr>
              <th />
              <th>Provider</th>
              <th>Endpoint</th>
              <th>Model</th>
              <th>Latency</th>
              <th>Failures</th>
              <th>Breaker</th>
              <th>Secret</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {providers.map((p) => (
              <tr key={p.provider_id}>
                <td>
                  <span className={classNames("aipage-dot", healthClass(p))} />
                </td>
                <td>
                  <strong>{p.provider_name}</strong>
                  {p.provider_id === act?.active_provider && (
                    <span className="aipage-chip">PRIMARY</span>
                  )}
                </td>
                <td className="aipage-mono">{p.endpoint || "—"}</td>
                <td className="aipage-mono">{p.default_model || "—"}</td>
                <td>{p.latency_ms != null ? `${Math.round(p.latency_ms)} ms` : "—"}</td>
                <td>{p.failure_count}</td>
                <td>
                  <StatusBadge status={p.circuit_breaker_state ?? null} label={p.circuit_breaker_state ?? "—"} />
                </td>
                <td>{p.has_secret ? "set" : "missing"}</td>
                <td className="aipage-actions">
                  <button
                    type="button"
                    disabled={busy === p.provider_id}
                    onClick={() => void runTest(p.provider_id)}
                  >
                    {busy === p.provider_id ? "Testing…" : "Test"}
                  </button>
                  <button
                    type="button"
                    disabled={busy === `switch:${p.provider_id}`}
                    onClick={() => void switchTo(p.provider_id)}
                  >
                    Set Primary
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Panel>

      {/* ---- Test Centre (Section 22) --------------------------------------- */}
      <Panel title="Test Centre">
        <p className="aipage-help">
          Tests run against SIMULATED TEST DATA and never place an order. A Test Result is not a
          live trading decision.
        </p>
        <div className="aipage-actions">
          <button type="button" disabled={busy === "evaluate"} onClick={() => void evaluate()}>
            {busy === "evaluate" ? "Evaluating…" : "Evaluate Sample Snapshot"}
          </button>
          <button type="button" disabled={busy === "compare"} onClick={() => void runCompare()}>
            {busy === "compare" ? "Comparing…" : "Compare Providers"}
          </button>
        </div>

        {test && (
          <div className={classNames("aipage-test", test.passed ? "ok" : "bad")}>
            <div className="aipage-test-head">
              <strong>{test.provider_id}</strong>
              <StatusBadge status={test.passed ? "ok" : "failed"} label={test.passed ? "PASSED" : "FAILED"} />
              <span className="aipage-mono">{test.stage}</span>
              <span>{Math.round(test.latency_ms)} ms</span>
            </div>
            <div className="aipage-mono aipage-detail">{test.detail}</div>
            {test.normalized_response && (
              <div className="aipage-mono aipage-detail">
                {JSON.stringify(test.normalized_response.decision ?? test.normalized_response, null, 2)}
              </div>
            )}
            <div className="aipage-meta">
              <span>input tokens: {test.input_tokens ?? "—"}</span>
              <span>output tokens: {test.output_tokens ?? "—"}</span>
              <span>cost: {test.cost_estimate != null ? test.cost_estimate.toExponential(2) : "unavailable"}</span>
            </div>
          </div>
        )}

        {compare && (
          <table className="aipage-table">
            <thead>
              <tr>
                <th>Provider</th>
                <th>Model</th>
                <th>Action</th>
                <th>p(hold)</th>
                <th>p(close)</th>
                <th>Latency</th>
                <th>Error</th>
              </tr>
            </thead>
            <tbody>
              {compare.providers.map((r) => (
                <tr key={r.provider_id}>
                  <td>{r.provider_id}</td>
                  <td className="aipage-mono">{r.model ?? "—"}</td>
                  <td>
                    <span className={classNames("aipage-chip", r.action?.toLowerCase())}>
                      {r.action ?? (r.ok ? "—" : "FAILED")}
                    </span>
                  </td>
                  <td>{r.p_hold != null ? r.p_hold.toFixed(3) : "—"}</td>
                  <td>{r.p_close != null ? r.p_close.toFixed(3) : "—"}</td>
                  <td>{r.latency_ms != null ? `${Math.round(r.latency_ms)} ms` : "—"}</td>
                  <td className="aipage-mono">{r.error_category ?? ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Panel>

      {/* ---- pipeline + decision trace (Sections 23, 41) -------------------- */}
      <Panel title="Decision Pipeline">
        <ol className="aipage-pipeline">
          {PIPELINE.map((step) => (
            <li key={step}>{step}</li>
          ))}
        </ol>
        {trace && (
          <div className="aipage-trace">
            <div className="aipage-test-head">
              <strong>Policy Decision</strong>
              <span className={classNames("aipage-chip", trace.final_action.toLowerCase())}>
                {trace.final_action}
              </span>
              <StatusBadge
                status={trace.risk.allowed ? "allowed" : "restricted"}
                label={trace.risk.allowed ? "Risk: allowed" : "Risk: restricted"}
              />
              {trace.fallback_used && (
                <StatusBadge status="warn" label={`FALLBACK USED — ${trace.fallback_reason}`} />
              )}
            </div>
            <ProbBar rows={evidenceBars} />
            <div className="aipage-meta">
              <span>decision {trace.decision_id}</span>
              <span>template {trace.versions.template}</span>
              <span>policy {trace.versions.policy}</span>
              <span>gate {trace.versions.gate}</span>
              <span>{Math.round(trace.latency_ms)} ms</span>
              {trace.is_test_data && <span className="aipage-chip warn">SIMULATED TEST DATA</span>}
            </div>
            <div className="aipage-meta">
              {trace.providers_used.map((p) => (
                <span key={p}>used: {p}</span>
              ))}
              {trace.providers_failed.map((p) => (
                <span key={p} className="aipage-fail">
                  failed: {p}
                </span>
              ))}
            </div>
            <div className="aipage-scores">
              {Object.entries(trace.policy.scores).map(([action, score]) => (
                <span key={action} className="aipage-mono">
                  {action}: {score.toFixed(4)}
                </span>
              ))}
            </div>
            {trace.risk.rejections.length > 0 && (
              <div className="aipage-fail">gate: {trace.risk.rejections.join(", ")}</div>
            )}
          </div>
        )}
        {snapshot && (
          <details className="aipage-snapshot">
            <summary>Input snapshot</summary>
            <pre className="aipage-mono">{JSON.stringify(snapshot, null, 2)}</pre>
          </details>
        )}
      </Panel>
    </div>
  );
}
