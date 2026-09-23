/**
 * PURPOSE:  Risk console page — backend-state mirror with radial limit
 *           gauges, stress/limits matrix, derived hero strip, freshness chips.
 * OWNER:    uiux-wave5-risk  (future edits to this file belong to this lane)
 * CONSUMES: riskApi (status/summary/runtimeRiskState), AppShell snapshot prop,
 *           pages/_shared kit (SectionState, widgets), components/pro/RiskViz,
 *           lib/riskGateTrace + lib/format, colocated ./riskThresholds,
 *           ./limitRows, ./HeroStrip, ./LimitGauge, ./LimitMatrix,
 *           ./FreshnessChip, ./risk.css
 * PROVIDES: default <RiskPage snapshot nowMs?> (routed by app/AppShell)
 * INVARIANTS: every verdict word is a backend string or an arithmetic
 *             restatement of two backend numbers; missing data renders
 *             EmptyState/ErrorState/Skeleton, never a fabricated value;
 *             every control that existed before this pass still works.
 * EXTEND:   new visuals belong in colocated components fed by ./limitRows —
 *           do not fetch here, do not add thresholds inline.
 */

/**
 * Risk — dedicated risk console (backend-state mirror).
 *
 * SAFETY / ACTIVE / WARNING / BLOCKED / ERROR / UNKNOWN distinction comes from
 * backend state:
 *  - /api/v1/risk/status  → last proposal risk_checks (gate outcomes) + config
 *  - /api/v1/risk/summary → exposure + margin
 *  - /api/debug/state     → kill switch / runtime_risk_state / halt reason
 * No client-side heuristics invent these verdicts.
 *
 * Layout (feature bar): GuardianHero + BreakerTiles + DrawdownBar + MarginArc
 * from the committed pro kit (components/pro/RiskViz — read-only usage), a
 * full gate MATRIX (pass/fail/unknown with value/limit/reason columns), and
 * per-symbol exposure bars. Every "no limit in payload" case renders
 * indeterminate — a missing budget is never coloured satisfied.
 */

import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { riskApi } from "@/api/riskApi";
import type { EngineSnapshot, RiskChecks, RuntimeRiskState } from "@/types/domain";
import { BreakerTiles, GateFunnel, GuardianHero, MarginArc } from "@/components/pro/RiskViz";
import { EmptyState, ErrorState, MetricCard, Panel, Skeleton, StatusBadge } from "@/components/primitives";
import { AgeNote, SectionState, errorText } from "@/pages/_shared/SectionState";
import { MeterBar, SortableTable, type Column } from "@/pages/_shared/widgets";
import { gateEvidence, verdictWord, directionWord, type GateEvidenceRow } from "@/lib/riskGateTrace";
import { formatMoney, formatNumber, formatPct } from "@/lib/format";
import { ApiError } from "@/types/api";
import { buildLimitRows } from "./limitRows";
import { pressureOf, rampStyle } from "./riskThresholds";
import { HeroStrip } from "./HeroStrip";
import { LimitGauge } from "./LimitGauge";
import { LimitMatrix } from "./LimitMatrix";
import { FreshnessChip } from "./FreshnessChip";
import "@/pages/_shared/pages.css";
import "./risk.css";

interface Props {
  snapshot: EngineSnapshot | undefined;
  /** Optional 1s ticker from the shell (AppShell passes it to Dashboard/Trading
   *  only); without it the page uses its own render-time clock for ages. */
  nowMs?: number;
}

interface GateRowVM {
  name: string;
  verdict: "pass" | "fail" | "unknown";
  value: string;
  limit: string;
  reason: string;
  /** Depth vs the gate's own limit (1.00 = at the limit) — drives the tint. */
  pressure: number | null;
}

/**
 * gateRow — one derived evidence row as a matrix cell.
 *
 * The pairing/verdict already happened in lib/riskGateTrace; this only
 * formats it for the table. A null value or limit renders as "—" (honest
 * absence), never as 0 or as a satisfied limit.
 */
function gateRow(row: GateEvidenceRow): GateRowVM {
  const cell = (v: number | null): string => {
    if (v === null || v === undefined || !Number.isFinite(v)) return "—";
    if (Math.abs(v) >= 100) return v.toFixed(0);
    return v.toFixed(3);
  };
  return {
    name: row.name,
    verdict: row.verdict,
    value: cell(row.value),
    limit: `${directionWord(row.direction)} ${cell(row.limit)}`,
    reason: row.detail ?? "no backend reason in payload — not a passing gate",
    // Colour-only depth from the same two backend numbers (never a new
    // verdict): 1.00 = at the gate's own limit, >1 = past it.
    pressure: pressureOf(row.value, row.limit, row.direction),
  };
}

function RiskMatrix({ checks }: { checks: RiskChecks }) {
  const ev = useMemo(() => gateEvidence(checks), [checks]);
  const rows = useMemo(() => ev.rows.map(gateRow), [ev]);
  const cols = useMemo<Array<Column<GateRowVM>>>(
    () => [
      {
        key: "gate",
        label: "Gate",
        sortValue: (r) => r.name,
        render: (r) => r.name.replace(/_/g, " "),
      },
      {
        key: "verdict",
        label: "Verdict",
        sortValue: (r) => r.verdict,
        render: (r) => (
          <span className={`l4-chip ${r.verdict === "pass" ? "good" : r.verdict === "fail" ? "bad" : ""}`}>
            {verdictWord(r.verdict)}
          </span>
        ),
      },
      {
        key: "value",
        label: "Value",
        num: true,
        sortValue: (r) => (r.value === "—" ? null : r.value),
        render: (r) => <span className="rsk-cell" style={rampStyle(r.pressure)}>{r.value}</span>,
      },
      { key: "limit", label: "Limit", num: true, sortValue: (r) => (r.limit === "—" ? null : r.limit), render: (r) => r.limit },
      {
        key: "reason",
        label: "Reason (backend)",
        render: (r) => <span className="small muted" title={r.reason}>{r.reason.length > 60 ? `${r.reason.slice(0, 60)}…` : r.reason}</span>,
      },
    ],
    [],
  );
  return (
    <>
      <SortableTable columns={cols} rows={rows} rowKey={gateRowKey} emptyMessage="No gate rows." maxHeight={null} />
      <div className="l4-note" style={{ padding: "6px 12px" }}>
        {ev.pass} pass · {ev.fail} fail · {ev.unknown} unknown — each PASS/FAIL is the arithmetic restatement of two
        backend-supplied numbers (value vs its own declared limit); an entry whose limit is absent is UNKNOWN, never FAIL.
      </div>
    </>
  );
}

/** Stable row identity for the gate matrix — memoized table compares refs. */
const gateRowKey = (r: GateRowVM): string => r.name;

function GuardianBlock({ state }: { state: RuntimeRiskState | null }) {
  if (!state) return <EmptyState message="Runtime risk state unavailable (shown as UNKNOWN — never inferred)." hint="/api/debug/state risk section did not answer." />;
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
      <dt>financial backpressure</dt>
      <dd className={state.financial_queue_backpressure > 0 ? "pnl-neg" : undefined}>{state.financial_queue_backpressure}</dd>
      <dt>config error</dt>
      <dd className={state.config_error ? "pnl-neg" : undefined}>{state.config_error ?? "—"}</dd>
    </dl>
  );
}

export default function RiskPage({ snapshot, nowMs }: Props) {
  const tickMs = nowMs ?? Date.now();
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
  const haltState = runtimeQuery.data ?? null;
  const acct = snapshot?.account;

  // Cross-check v1 summary exposure vs the canonical snapshot account block:
  // two backend reads of the same fact — agreement is shown, drift is shouted.
  const posA = exposure?.available ? exposure.open_positions ?? null : null;
  const posB = acct?.open_positions ?? null;
  const crossMismatch = posA !== null && posB !== null && posA !== posB;

  const marginLevel = exposure?.account?.margin_level ?? acct?.margin_level ?? null;
  // Margin floor: the ONLY backend-supplied threshold we may compare against.
  // None exists in the risk payload → MarginArc renders indeterminate by design.
  const marginFloorPct: number | null = null;

  // One derived list feeds BOTH the radial gauges and the stress/limits
  // matrix: value vs backend limit, every field read from the existing
  // payloads (no new endpoint, no invented number).
  const limitRows = useMemo(
    () => buildLimitRows({ cfg, exposure, account: acct, spread: snapshot?.spread ?? null }),
    [cfg, exposure, acct, snapshot?.spread],
  );
  const ceilingRows = useMemo(() => limitRows.filter((r) => r.direction === "le"), [limitRows]);

  // Freshness chips: the backend's own probed_at where the payload carries
  // one, otherwise the query's receive time. No parsable timestamp ⇒ NO chip
  // (an absent timestamp must never render as "0s ago = fresh").
  const probeCandidates: Array<{ key: string; label: string; atMs: number | null }> = [
    { key: "status", label: "risk status", atMs: statusQuery.data ? Date.parse(statusQuery.data.probed_at) : null },
    { key: "summary", label: "risk summary", atMs: summaryQuery.data ? Date.parse(summaryQuery.data.probed_at) : null },
    { key: "guardian", label: "guardian", atMs: runtimeQuery.dataUpdatedAt || null },
  ];
  const probes = probeCandidates.filter(
    (p): p is { key: string; label: string; atMs: number } => p.atMs !== null && Number.isFinite(p.atMs),
  );

  const bySymbol = exposure?.by_symbol ?? {};
  const exposureRows = Object.entries(bySymbol);

  return (
    <div>
      {/* Derived breach/ok sums over the rows rendered below (hidden when empty) */}
      <HeroStrip rows={limitRows} />
      {probes.length > 0 && (
        <div className="rsk-probes" aria-label="Probe freshness">
          {probes.map((p) => (
            <FreshnessChip key={p.key} label={p.label} atMs={p.atMs} nowMs={tickMs} />
          ))}
        </div>
      )}
      <div className="grid cols-4">
        <MetricCard
          label="Safety status"
          value={haltState ? (haltState.kill_switch_active ? "BLOCKED" : haltState.runtime_risk_state_effective.toUpperCase() === "RUNNING" ? "SAFE" : "WARNING") : "UNKNOWN"}
          tone={haltState ? (haltState.kill_switch_active ? "neg" : haltState.runtime_risk_state_effective.toUpperCase() === "RUNNING" ? "pos" : undefined) : "dim"}
          sub={haltState ? `effective=${haltState.runtime_risk_state_effective}` : "backend state unavailable"}
        />
        <MetricCard label="Equity" value={formatMoney(exposure?.account?.equity ?? acct?.equity ?? null)} sub={`margin ${formatMoney(exposure?.account?.margin ?? acct?.margin ?? null)}`} />
        <MetricCard
          label="Open exposure"
          value={exposure?.available ? `${exposure.open_positions ?? 0} pos · ${formatNumber(exposure.total_volume)} lots` : "—"}
          tone="dim"
          sub={exposure?.available ? `floating ${formatMoney(exposure.total_floating_profit)}` : (exposure?.reason ?? "unavailable")}
        />
        <MetricCard
          label="Margin level"
          value={marginLevel === null || marginLevel === undefined ? "—" : formatPct(marginLevel, 1)}
          tone={typeof marginLevel === "number" && marginLevel < 200 ? "neg" : "dim"}
          sub="broker margin_level %"
        />
      </div>

      {/* Guardian hero + breakers from the committed pro kit (echo backend) */}
      <div className="l4-section-gap">
        <GuardianHero
          state={haltState}
          probedNote={runtimeQuery.dataUpdatedAt ? `probed ${Math.max(0, (tickMs - runtimeQuery.dataUpdatedAt) / 1000).toFixed(1)}s ago` : undefined}
        />
      </div>
      <div className="grid cols-2 l4-section-gap">
        <Panel title="Circuit-breaker counters" right={<AgeNote label="age" ageSec={runtimeQuery.dataUpdatedAt ? (tickMs - runtimeQuery.dataUpdatedAt) / 1000 : null} />}>
          <BreakerTiles state={haltState} />
        </Panel>
        <Panel title="Limit gauges (backend value vs backend limit)" right={<span className="timestamp-note">missing limit ⇒ indeterminate, never satisfied</span>}>
          {statusQuery.isPending && !statusQuery.data ? (
            <Skeleton count={3} />
          ) : statusQuery.isError && !statusQuery.data ? (
            <ErrorState
              message={errorText(statusQuery.error, "Risk status endpoint failed for limit gauges.")}
              requestId={statusQuery.error instanceof ApiError ? statusQuery.error.requestId : null}
              onRetry={() => void statusQuery.refetch()}
            />
          ) : (
            <div className="rsk-gauges">
              {ceilingRows.map((row) => (
                <LimitGauge key={row.id} row={row} />
              ))}
              {/* Floor metric: payload carries no margin floor, so the arc is
                  indeterminate by design — never an assumed stop-out level. */}
              <MarginArc marginLevelPct={marginLevel} thresholdPct={marginFloorPct} thresholdWord={null} />
            </div>
          )}
        </Panel>
      </div>

      {/* Stress/limits matrix: every visible value-vs-limit pair as a grid,
          hover a cell (title attr) for the payload paths behind the numbers. */}
      <Panel
        title="Stress / limits matrix (value vs backend limit)"
        right={
          <span className="timestamp-note">
            hover a cell for payload paths · tint = depth past the 80 % soft threshold (100 % = the backend limit)
          </span>
        }
      >
        {statusQuery.isPending && !statusQuery.data && summaryQuery.isPending && !summaryQuery.data ? (
          <div style={{ padding: 4 }}>
            <Skeleton count={4} />
          </div>
        ) : limitRows.length === 0 && (statusQuery.isError || summaryQuery.isError) ? (
          // A failed source that produced zero rows is an ERROR, not an empty
          // payload — LimitMatrix's EmptyState would claim "no rows in payload".
          <ErrorState
            message={
              statusQuery.isError
                ? errorText(statusQuery.error, "Risk status endpoint failed.")
                : errorText(summaryQuery.error, "Risk summary endpoint failed.")
            }
            requestId={
              statusQuery.error instanceof ApiError
                ? statusQuery.error.requestId
                : summaryQuery.error instanceof ApiError
                  ? summaryQuery.error.requestId
                  : null
            }
            onRetry={() => {
              if (statusQuery.isError) void statusQuery.refetch();
              if (summaryQuery.isError) void summaryQuery.refetch();
            }}
          />
        ) : (
          <LimitMatrix rows={limitRows} />
        )}
      </Panel>

      <div className="grid cols-2 l4-section-gap">
        <Panel title="Guardian / runtime detail">
          {runtimeQuery.isPending ? (
            <Skeleton count={5} />
          ) : runtimeQuery.isError ? (
            <ErrorState message={errorText(runtimeQuery.error, "Guardian state endpoint failed.")} requestId={runtimeQuery.error instanceof ApiError ? runtimeQuery.error.requestId : null} onRetry={() => void runtimeQuery.refetch()} />
          ) : (
            <GuardianBlock state={runtimeQuery.data ?? null} />
          )}
        </Panel>

        <Panel title="Risk configuration (engine, sanitized)">
          {statusQuery.isPending ? (
            <Skeleton count={5} />
          ) : statusQuery.isError ? (
            <ErrorState message={errorText(statusQuery.error, "Risk status endpoint failed.")} requestId={statusQuery.error instanceof ApiError ? statusQuery.error.requestId : null} onRetry={() => void statusQuery.refetch()} />
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
            <EmptyState message="Risk config unavailable (engine offline or endpoint refused)." hint="Rendered as UNKNOWN — limits are never assumed." />
          )}
        </Panel>
      </div>

      <Panel
        title="Last proposal gate matrix (risk_checks)"
        right={
          <AgeNote
            label="probed"
            ageSec={statusQuery.data ? (tickMs - Date.parse(statusQuery.data.probed_at)) / 1000 : null}
            suffix={statusQuery.data?.last_proposal_present ? "proposal present" : "no proposal yet"}
          />
        }
        tight
      >
        {statusQuery.isPending && !statusQuery.data ? (
          <div style={{ padding: 12 }}><Skeleton count={4} /></div>
        ) : statusQuery.isError && !statusQuery.data ? (
          <ErrorState message={errorText(statusQuery.error, "Risk status endpoint failed.")} requestId={statusQuery.error instanceof ApiError ? statusQuery.error.requestId : null} onRetry={() => void statusQuery.refetch()} />
        ) : statusQuery.data?.last_proposal_present && statusQuery.data.risk_checks ? (
          <>
            <div style={{ padding: "10px 12px 0" }}>
              <GateFunnel checks={statusQuery.data.risk_checks} />
            </div>
            <RiskMatrix checks={statusQuery.data.risk_checks} />
          </>
        ) : (
          <EmptyState message="No proposal risk_checks yet (engine has not evaluated a trade this session)." hint="The matrix appears with the first proposal — an empty store is not a passing gate." />
        )}
      </Panel>

      <Panel
        title="Exposure by symbol"
        right={
          <>
            <span className="timestamp-note">source /api/v1/risk/summary</span>
            <button aria-label="Refresh summary" className="btn small ghost" onClick={() => void summaryQuery.refetch()} disabled={summaryQuery.isFetching}>⟳</button>
          </>
        }
      >
        <SectionState
          query={summaryQuery}
          emptyMessage="No open exposure."
          emptyHint="The backend reported zero rows — not a rendering gap."
          errorFallback="Exposure endpoint failed."
          emptyWhen={(d) => !(d.exposure?.available && Object.keys(d.exposure.by_symbol ?? {}).length > 0)}
        >
          {(d) => (
            <div style={{ display: "grid", gap: 8 }}>
              {d.exposure?.available === false && d.exposure.reason ? <div className="l4-note warn">exposure unavailable: {d.exposure.reason}</div> : null}
              {(() => {
                const rows = Object.entries(d.exposure?.by_symbol ?? {});
                if (rows.length === 0) {
                  return <EmptyState message="No open exposure." hint="The backend reported zero rows — not a rendering gap." />;
                }
                const maxVol = Math.max(...rows.map(([, s]) => (Number.isFinite(s.volume) ? s.volume : 0)), 0) || 1;
                return rows.map(([sym, s]) => (
                  <MeterBar
                    key={sym}
                    label={`${sym} · ${s.positions} pos`}
                    value={s.volume}
                    limit={maxVol}
                    unit=" lots"
                    tone={(s.profit ?? 0) < 0 ? "warn" : "ok"}
                    fraction={s.volume / maxVol}
                    caption={`${formatMoney(s.profit)} floating · share of the largest symbol (relativity only, no limit claimed)`}
                  />
                ));
              })()}
              {crossMismatch && (
                <div className="confirm-box" style={{ borderColor: "rgba(235,161,63,0.5)" }}>
                  <span>
                    <b>Read mismatch:</b> /api/v1/risk/summary counts {String(posA)} open positions, the canonical snapshot {String(posB)}. Two backend
                    reads at different instants — re-probe; if it persists, reconcile on the Trading page before trusting the aggregate.
                  </span>
                </div>
              )}
              {exposureRows.length === 0 && d.exposure?.available && <div className="l4-note">summary reports available with an empty by_symbol map.</div>}
            </div>
          )}
        </SectionState>
      </Panel>

      {statusQuery.error instanceof ApiError && statusQuery.error.isAuthError && (
        <div className="banner auth"><span>⛔ Re-authentication required — reopen with ?token=…</span></div>
      )}
    </div>
  );
}
