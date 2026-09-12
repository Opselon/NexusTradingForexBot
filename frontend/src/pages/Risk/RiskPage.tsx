/**
 * Risk — dedicated risk console.
 *
 * SAFETY / ACTIVE / WARNING / BLOCKED / ERROR / UNKNOWN distinction comes from
 * backend state:
 *  - /api/v1/risk/status  → last proposal risk_checks (gate outcomes) + config
 *  - /api/v1/risk/summary → exposure + margin
 *  - /api/debug/state     → kill switch / runtime_risk_state / halt reason
 * No client-side heuristics invent these verdicts.
 */

import { useQuery } from "@tanstack/react-query";
import { riskApi } from "@/api/riskApi";
import type { EngineSnapshot, RuntimeRiskState, RiskChecks } from "@/types/domain";
import { EmptyState, MetricCard, Panel, StatusBadge } from "@/components/primitives";
import { formatMoney, formatNumber, formatPct } from "@/lib/format";
import { ApiError } from "@/types/api";
import { ErrorState } from "@/components/primitives";

interface Props {
  snapshot: EngineSnapshot | undefined;
}

/** Renders backend risk-check entries. Values are untrusted shapes (record)
 *  so this stays defensive without inventing verdicts. */
function RiskCheckList({ checks }: { checks: RiskChecks }) {
  const entries = Object.entries(checks);
  if (entries.length === 0) return <EmptyState message="No risk checks recorded on the last proposal." />;
  return (
    <dl className="kv">
      {entries.map(([name, raw]) => {
        const v = raw as { passed?: boolean; allowed?: boolean; value?: unknown; limit?: unknown; reason?: string } | null;
        const ok = v?.passed === true || v?.allowed === true;
        const bad = v?.passed === false || v?.allowed === false;
        return (
          <div key={name} style={{ display: "contents" }}>
            <dt>{name.replace(/_/g, " ")}</dt>
            <dd>
              <span className={`badge ${ok ? "good" : bad ? "bad" : "unknown"}`}>
                {ok ? "PASS" : bad ? "FAIL" : "—"}
              </span>{" "}
              {v?.reason ? <span className="small muted">{v.reason}</span> : null}
            </dd>
          </div>
        );
      })}
    </dl>
  );
}

function GuardianBlock({ state }: { state: RuntimeRiskState | null }) {
  if (!state) return <EmptyState message="Runtime risk state unavailable (shown as UNKNOWN — never inferred)." />;
  const effective = state.runtime_risk_state_effective.toUpperCase();
  const level = state.kill_switch_active || effective === "HALTED" ? "bad" : effective === "RUNNING" ? "good" : "warn";
  return (
    <dl className="kv">
      <dt>safety status</dt>
      <dd><span className={`badge ${level}`}>{state.kill_switch_active ? "KILL SWITCH ACTIVE" : effective}</span></dd>
      <dt>halt reason</dt>
      <dd>{state.halt_reason || "—"}</dd>
      <dt>halt triggered</dt>
      <dd>{state.halt_triggered_at || "—"}</dd>
      <dt>survival mode</dt>
      <dd>{state.survival_mode ? <span className="badge warn">ACTIVE</span> : <span className="badge good">OFF</span>}</dd>
      <dt>account freshness</dt>
      <dd><StatusBadge status={state.account_freshness} /></dd>
      <dt>consecutive losses</dt>
      <dd>{state.consecutive_losses}</dd>
      <dt>hard max lots</dt>
      <dd>{formatNumber(state.hard_max_lots)}</dd>
      <dt>audit batch failures</dt>
      <dd className={state.audit_batch_failures > 0 ? "pnl-neg" : undefined}>{state.audit_batch_failures}</dd>
      <dt>dead-letter rows</dt>
      <dd className={state.audit_dead_letter_rows > 0 ? "pnl-neg" : undefined}>{state.audit_dead_letter_rows}</dd>
      <dt>telemetry dropped</dt>
      <dd>{state.telemetry_dropped}</dd>
      <dt>financial overflow</dt>
      <dd className={state.financial_events_overflowed > 0 ? "pnl-neg" : undefined}>{state.financial_events_overflowed}</dd>
    </dl>
  );
}

export default function RiskPage({ snapshot }: Props) {
  const statusQuery = useQuery({
    queryKey: ["risk-status"],
    queryFn: ({ signal }) => riskApi.status(signal),
    refetchInterval: 10_000,
    retry: false,
  });
  const summaryQuery = useQuery({
    queryKey: ["risk-summary"],
    queryFn: ({ signal }) => riskApi.summary(signal),
    refetchInterval: 10_000,
    retry: false,
  });
  const runtimeQuery = useQuery({
    queryKey: ["runtime-risk-state"],
    queryFn: ({ signal }) => riskApi.runtimeRiskState(signal),
    refetchInterval: 10_000,
    retry: false,
  });

  const cfg = statusQuery.data?.risk_config;
  const exposure = summaryQuery.data?.exposure;

  return (
    <div>
      <div className="grid cols-4">
        <MetricCard
          label="Safety status"
          value={runtimeQuery.data ? (runtimeQuery.data.kill_switch_active ? "BLOCKED" : runtimeQuery.data.runtime_risk_state_effective.toUpperCase() === "RUNNING" ? "SAFE" : "WARNING") : "UNKNOWN"}
          tone={runtimeQuery.data ? (runtimeQuery.data.kill_switch_active ? "neg" : runtimeQuery.data.runtime_risk_state_effective.toUpperCase() === "RUNNING" ? "pos" : undefined) : "dim"}
          sub={runtimeQuery.data ? `effective=${runtimeQuery.data.runtime_risk_state_effective}` : "backend state unavailable"}
        />
        <MetricCard label="Equity" value={formatMoney(exposure?.account?.equity ?? snapshot?.account.equity ?? null)} sub={`margin ${formatMoney(exposure?.account?.margin ?? snapshot?.account.margin ?? null)}`} />
        <MetricCard
          label="Open exposure"
          value={exposure?.available ? `${exposure.open_positions ?? 0} pos · ${formatNumber(exposure.total_volume)} lots` : "—"}
          tone="dim"
          sub={exposure?.available ? `floating ${formatMoney(exposure.total_floating_profit)}` : (exposure?.reason ?? "unavailable")}
        />
        <MetricCard
          label="Margin level"
          value={exposure?.account?.margin_level === null || exposure?.account?.margin_level === undefined ? "—" : formatPct(exposure.account.margin_level, 1)}
          tone={exposure?.account?.margin_level !== null && exposure?.account?.margin_level !== undefined && exposure.account.margin_level < 200 ? "neg" : "dim"}
          sub="broker margin_level %"
        />
      </div>

      <div className="grid cols-2" style={{ marginTop: 14 }}>
        <Panel title="Guardian / circuit breakers">
          {runtimeQuery.isPending ? (
            <div className="muted small">loading…</div>
          ) : runtimeQuery.isError ? (
            <ErrorState message="Guardian state endpoint failed." onRetry={() => runtimeQuery.refetch()} />
          ) : (
            <GuardianBlock state={runtimeQuery.data ?? null} />
          )}
        </Panel>

        <Panel title="Risk configuration (engine, sanitized)">
          {statusQuery.isPending ? (
            <div className="muted small">loading…</div>
          ) : cfg ? (
            <dl className="kv">
              <dt>max drawdown %</dt>
              <dd>{formatPct(cfg.max_account_drawdown_pct)}</dd>
              <dt>risk per trade %</dt>
              <dd>{formatPct(cfg.risk_per_trade_pct)}</dd>
              <dt>max concurrent positions</dt>
              <dd>{cfg.max_concurrent_positions ?? "—"}</dd>
              <dt>max spread points</dt>
              <dd>{formatNumber(cfg.max_spread_points)}</dd>
              <dt>max margin usage %</dt>
              <dd>{formatPct(cfg.max_margin_usage_pct)}</dd>
              <dt>max allowed lots</dt>
              <dd>{formatNumber(cfg.max_allowed_lots)}</dd>
              <dt>enforce stop loss</dt>
              <dd>{cfg.enforce_stop_loss === null ? "—" : cfg.enforce_stop_loss ? <span className="badge good">ON</span> : <span className="badge warn">OFF</span>}</dd>
            </dl>
          ) : (
            <EmptyState message="Risk config unavailable (engine offline or endpoint refused)." />
          )}
        </Panel>
      </div>

      <Panel
        title="Last proposal gate trace (risk_checks)"
        right={<span className="timestamp-note">{statusQuery.data ? `probed ${statusQuery.data.probed_at}` : ""}</span>}
      >
        {statusQuery.isPending ? (
          <div className="muted small">loading…</div>
        ) : statusQuery.data === undefined ? (
          <ErrorState message="Risk status endpoint failed." onRetry={() => statusQuery.refetch()} />
        ) : statusQuery.data.last_proposal_present && statusQuery.data.risk_checks ? (
          <RiskCheckList checks={statusQuery.data.risk_checks} />
        ) : (
          <EmptyState message="No proposal risk_checks yet (engine has not evaluated a trade this session)." />
        )}
      </Panel>

      <Panel title="Exposure by symbol">
        {exposure?.available && exposure.by_symbol && Object.keys(exposure.by_symbol).length > 0 ? (
          <dl className="kv">
            {Object.entries(exposure.by_symbol).map(([sym, s]) => (
              <div key={sym} style={{ display: "contents" }}>
                <dt>{sym}</dt>
                <dd>
                  {s.positions} pos · {formatNumber(s.volume)} lots · {formatMoney(s.profit)}
                </dd>
              </div>
            ))}
          </dl>
        ) : (
          <EmptyState message="No open exposure." />
        )}
      </Panel>

      {statusQuery.error instanceof ApiError && statusQuery.error.isAuthError && (
        <div className="banner auth"><span>⛔ Re-authentication required — reopen with ?token=…</span></div>
      )}
    </div>
  );
}
