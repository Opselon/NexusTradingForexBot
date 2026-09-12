/**
 * Dashboard — the operator's pro first-answer surface.
 *
 * Answers immediately, from backend data only, in reading order:
 *   1. Hero KPI row      — running? LIVE/PAPER? MT5? gate? equity/DD? decision?
 *   2. Decision + chain  — what the engine decided and WHY (humanizer) + the
 *                          tick→features→inference→decision freshness lineage.
 *   3. Account + market  — broker micro-summary; bid/ask/spread/ATR/regime.
 *   4. Health matrix     — every subsystem verdict, backend words only.
 *   5. Guardian, ML/70D, recent decisions, positions, MT5 detail.
 *
 * All values are the canonical `get_system_state()` snapshot (REST seed +
 * WebSocket live merge). Nulls render as "—" (UNKNOWN), never fabricated.
 *
 * ── PRO LANE WIRING ───────────────────────────────────────────────────────
 * The colocated tiles in ./pro/* are this lane's own components. The sibling
 * components/pro/* set (Indicators/RiskViz/MLViz/OpsChrome/DataTablePro/
 * AuditTools) is NOT imported here yet — see scratch/wiring/dashboard-pro.md
 * for the exact import lines + JSX slots to apply once those files land.
 */

import { useQuery } from "@tanstack/react-query";
import { engineApi } from "@/api/engineApi";
import { riskApi } from "@/api/riskApi";
import type { EngineSnapshot } from "@/types/domain";
import type { RuntimeRiskState } from "@/types/domain";
import {
  DataTable,
  EmptyState,
  MetricCard,
  Panel,
  PositionSideBadge,
  StatusBadge,
} from "@/components/primitives";
import { FeedQualityChip } from "@/components/FeedQualityChip";
import { formatMoney, formatNumber, formatPct, formatPnl, formatPrice } from "@/lib/format";
import { HeroKpiRow } from "./pro/HeroKpiRow";
import { PipelineAgeChain } from "./pro/PipelineAgeChain";
import { AccountMicroCard } from "./pro/AccountMicroCard";
import { HealthMatrix } from "./pro/HealthMatrix";
import { DecisionHumanCard } from "./pro/DecisionHumanCard";

interface Props {
  snapshot: EngineSnapshot | undefined;
  nowMs: number;
}

function fmtAge(sec: number | null | undefined): string {
  if (sec === null || sec === undefined) return "—";
  if (sec < 90) return `${sec.toFixed(1)}s`;
  if (sec < 7200) return `${Math.floor(sec / 60)}m ${Math.floor(sec % 60)}s`;
  return `${Math.floor(sec / 3600)}h ${Math.floor((sec % 3600) / 60)}m`;
}

export default function DashboardPage({ snapshot, nowMs }: Props) {
  const mt5Query = useQuery({
    queryKey: ["mt5-status"],
    queryFn: ({ signal }) => engineApi.mt5Status(signal),
    refetchInterval: 10_000,
  });

  const riskStateQuery = useQuery({
    queryKey: ["runtime-risk-state"],
    queryFn: ({ signal }) => riskApi.runtimeRiskState(signal),
    refetchInterval: 10_000,
    retry: false,
  });

  if (!snapshot) {
    return <EmptyState message="Waiting for backend state…" hint="The dashboard renders only real NSE state." />;
  }

  const acct = snapshot.account;
  const health = snapshot.health;
  const positions = snapshot.positions ?? [];
  const haltState: RuntimeRiskState | null = riskStateQuery.data ?? null;
  const guardianBlocking = haltState?.kill_switch_active === true || (haltState?.runtime_risk_state_effective ?? "").toUpperCase() === "HALTED";

  return (
    <div>
      {/* 0 — Realtime truth line: feed quality chip (derived feedStore view),
         the server state_version, and provenance words straight from the
         backend snapshot. Positions PnL, spread, engine state and the
         freshness panels below all render from the version-guarded live
         merge in useRealtimeSnapshot — every SSE tick reaches them. */}
      <div className="conn-chip" style={{ display: "flex", gap: 10, flexWrap: "wrap", marginBottom: 12, width: "fit-content" }}>
        <FeedQualityChip nowMs={nowMs} />
        <span className="timestamp-note" title="Monotonic server snapshot version (never regresses across REST/live merge)">
          state_version {snapshot.state_version}
        </span>
        <span className={`badge ${String(snapshot.provenance.price).toUpperCase() === "UNAVAILABLE" ? "unknown" : "neutral"}`} title="provenance.price (backend word)">
          PRICE {snapshot.provenance.price}
        </span>
        <span className={`badge ${String(snapshot.provenance.features).toUpperCase() === "UNAVAILABLE" ? "unknown" : "neutral"}`} title="provenance.features (backend word)">
          FEAT {snapshot.provenance.features}
        </span>
        <span className={`badge ${String(snapshot.provenance.model).toUpperCase() === "UNAVAILABLE" ? "unknown" : "neutral"}`} title="provenance.model (backend word)">
          MODEL {snapshot.provenance.model}
        </span>
        <span className={`badge ${String(snapshot.provenance.accounting).toUpperCase() === "UNAVAILABLE" ? "unknown" : "neutral"}`} title="provenance.accounting (backend word)">
          ACCT {snapshot.provenance.accounting}
        </span>
      </div>

      {/* 1 — Hero: the eight critical answers, wired to the MetricCard primitive */}
      <HeroKpiRow snapshot={snapshot} guardianBlocking={guardianBlocking} />

      {/* 2 — Decision humanizer + pipeline freshness chain */}
      <div className="grid cols-2" style={{ marginTop: 14 }}>
        <Panel
          title="AI decision (human layer)"
          accent
          right={<span className="timestamp-note">reason codes translated by exact match only</span>}
        >
          <DecisionHumanCard snapshot={snapshot} />
        </Panel>

        <Panel
          title="Pipeline freshness chain"
          right={<span className="timestamp-note">live_freshness + diagnostics (backend)</span>}
        >
          <PipelineAgeChain snapshot={snapshot} />
        </Panel>
      </div>

      {/* 3 — Account micro-summary + market state */}
      <div className="grid cols-2" style={{ marginTop: 14 }}>
        <Panel
          title="Account"
          right={<span className="timestamp-note">equity {formatMoney(acct.equity)} · floating {formatPnl(acct.floating)}</span>}
        >
          <AccountMicroCard acct={acct} />
        </Panel>

        <Panel
          title="Market"
          right={<span className="timestamp-note">tick age {fmtAge(snapshot.diagnostics.tick_age_sec)}{snapshot.tick_stale ? " · STALE" : ""}</span>}
        >
          <div className="grid cols-3">
            <MetricCard label="Bid" value={formatPrice(snapshot.bid, snapshot.price_digits ?? 2)} />
            <MetricCard label="Ask" value={formatPrice(snapshot.ask, snapshot.price_digits ?? 2)} />
            <MetricCard label="Spread" value={snapshot.spread === null ? "—" : `${formatNumber(snapshot.spread)} pts`} />
            <MetricCard label="ATR" value={formatNumber(snapshot.atr)} />
            <MetricCard label="Regime" value={snapshot.regime ?? "—"} tone="dim" />
            <MetricCard label="Price source" value={snapshot.provenance.price} tone="dim" />
          </div>
          {snapshot.tick_stale && (
            <div className="confirm-box">
              <span>Backend marks the tick stream <b>stale</b> (freshness {snapshot.tick_freshness_ms === null ? "—" : `${(snapshot.tick_freshness_ms / 1000).toFixed(1)}s`}). Prices above may be frozen.</span>
            </div>
          )}
        </Panel>
      </div>

      {/* 4 — Subsystem health matrix */}
      <div style={{ marginTop: 14 }}>
        <Panel title="Subsystem health matrix" right={<span className="timestamp-note">badge levels from backend status words</span>}>
          <HealthMatrix snapshot={snapshot} />
        </Panel>
      </div>

      {/* 5 — Guardian + ML detail (unchanged semantics: UNKNOWN stays UNKNOWN) */}
      <div className="grid cols-2" style={{ marginTop: 14 }}>
        {/* Guardian / kill switch */}
        <Panel title="Guardian / runtime risk">
          {riskStateQuery.isPending ? (
            <div className="muted small">loading guardian state…</div>
          ) : haltState ? (
            <dl className="kv">
              <dt>kill switch</dt>
              <dd>{haltState.kill_switch_active ? <span className="badge bad">ACTIVE</span> : <span className="badge good">DISENGAGED</span>}</dd>
              <dt>runtime risk state</dt>
              <dd><span className={`badge ${haltState.runtime_risk_state_effective.toUpperCase() === "RUNNING" ? "good" : "bad"}`}>{haltState.runtime_risk_state_effective}</span></dd>
              <dt>halt reason</dt>
              <dd>{haltState.halt_reason || "—"}</dd>
              <dt>survival mode</dt>
              <dd>{haltState.survival_mode ? <span className="badge warn">ACTIVE</span> : "—"}</dd>
              <dt>account freshness</dt>
              <dd><StatusBadge status={haltState.account_freshness} /></dd>
              <dt>consecutive losses</dt>
              <dd>{haltState.consecutive_losses}</dd>
              <dt>audit dead-letter rows</dt>
              <dd className={haltState.audit_dead_letter_rows > 0 ? "pnl-neg" : undefined}>{haltState.audit_dead_letter_rows}</dd>
            </dl>
          ) : (
            <div className="muted small">guardian state unavailable (backend /api/debug/state offline) — shown as UNKNOWN, not inferred.</div>
          )}
        </Panel>

        {/* ML / 70D snapshot strip */}
        <Panel title="ML / 70D">
          <dl className="kv">
            <dt>model</dt>
            <dd><StatusBadge status={health.subsystems.model ?? null} /></dd>
            <dt>inference freshness</dt>
            <dd><StatusBadge status={health.subsystems.inference_freshness ?? null} /></dd>
            <dt>bundle schema</dt>
            <dd>{snapshot.model.feature_schema_id ?? "—"} ({snapshot.model.feature_dimension ?? "?"}D)</dd>
            <dt>scaler</dt>
            <dd>{snapshot.model.scaler_ready === null ? "—" : snapshot.model.scaler_ready ? "READY" : "NOT FITTED"}</dd>
            <dt>probs N/B/S</dt>
            <dd>
              {snapshot.probs.available
                ? `${(snapshot.probs.no_trade ?? 0).toFixed(2)} / ${(snapshot.probs.buy ?? 0).toFixed(2)} / ${(snapshot.probs.sell ?? 0).toFixed(2)}`
                : "—"}
            </dd>
            <dt>inference latency</dt>
            <dd>{snapshot.model.latency_ms === null ? "—" : `${snapshot.model.latency_ms.toFixed(1)} ms`}</dd>
            <dt>model id</dt>
            <dd>{snapshot.model.model_id ?? "—"}</dd>
          </dl>
        </Panel>
      </div>

      {/* Recent audit events = latest predictions (audit_signals passthrough) */}
      <div style={{ marginTop: 14 }}>
        <Panel
          title="Recent model decisions (audit_signals)"
          right={<span className="timestamp-note">real rows from the audit DB — never fabricated</span>}
          tight
        >
          {snapshot.predictions.length === 0 ? (
            <EmptyState message="No model decisions recorded yet." hint="Rows appear once the engine records audit_signals entries." />
          ) : (
            <DataTable
              headers={[
                { label: "Time" },
                { label: "Action" },
                { label: "Confidence", num: true },
                { label: "Regime" },
                { label: "P(NO) ", num: true },
                { label: "P(BUY)", num: true },
                { label: "P(SELL)", num: true },
                { label: "Reason" },
              ]}
            >
              {snapshot.predictions.slice(0, 12).map((p, i) => (
                <tr key={p.request_id ?? i}>
                  <td>{p.time ?? "—"}</td>
                  <td>{p.action ?? "—"}</td>
                  <td className="num">{p.confidence === null ? "—" : formatPct(p.confidence * 100, 1)}</td>
                  <td>{p.regime ?? "—"}</td>
                  <td className="num">{p.probabilities.no_trade?.toFixed(3) ?? "—"}</td>
                  <td className="num">{p.probabilities.buy?.toFixed(3) ?? "—"}</td>
                  <td className="num">{p.probabilities.sell?.toFixed(3) ?? "—"}</td>
                  <td>{p.reason ?? "—"}</td>
                </tr>
              ))}
            </DataTable>
          )}
        </Panel>
      </div>

      {/* Open positions preview */}
      <div style={{ marginTop: 14 }}>
        <Panel title={`Open positions (${positions.length})`} tight>
          {positions.length === 0 ? (
            <EmptyState message="No open positions." />
          ) : (
            <DataTable
              headers={[
                { label: "Ticket" },
                { label: "Symbol" },
                { label: "Side" },
                { label: "Volume", num: true },
                { label: "Entry", num: true },
                { label: "Current", num: true },
                { label: "PnL", num: true },
              ]}
            >
              {positions.slice(0, 8).map((p, i) => (
                <tr key={p.ticket ?? i}>
                  <td>{p.ticket ?? "—"}</td>
                  <td>{p.symbol ?? "—"}</td>
                  <td><PositionSideBadge type={p.type} /></td>
                  <td className="num">{formatNumber(p.volume)}</td>
                  <td className="num">{formatPrice(p.price_open)}</td>
                  <td className="num">{formatPrice(p.price_current)}</td>
                  <td className={`num ${p.profit !== null && p.profit >= 0 ? "pnl-pos" : "pnl-neg"}`}>{formatPnl(p.profit)}</td>
                </tr>
              ))}
            </DataTable>
          )}
        </Panel>
      </div>

      {/* MT5 detail from the dedicated status endpoint */}
      <div style={{ marginTop: 14 }}>
        <Panel title="MT5 status detail" tight>
          {mt5Query.isPending ? (
            <div className="state-block"><div className="spinner" /></div>
          ) : mt5Query.isError ? (
            <EmptyState message="MT5 status unavailable (backend endpoint failed)." hint="Rendered as UNKNOWN — never inferred." />
          ) : mt5Query.data ? (
            <dl className="kv" style={{ padding: "10px 14px" }}>
              <dt>connection</dt>
              <dd><StatusBadge status={String((mt5Query.data.connection as { state?: string } | undefined)?.state ?? null)} /></dd>
              <dt>terminal version</dt>
              <dd>{String((mt5Query.data.connection as { terminal_version?: string } | undefined)?.terminal_version ?? "—")}</dd>
              <dt>account</dt>
              <dd>{mt5Query.data.account?.available ? `${mt5Query.data.account.company ?? "—"} · ${mt5Query.data.account.server ?? "—"}` : "—"}</dd>
              <dt>pending orders (broker)</dt>
              <dd>{mt5Query.data.orders?.length ?? 0}</dd>
              <dt>positions (broker)</dt>
              <dd>{mt5Query.data.positions?.length ?? 0}</dd>
            </dl>
          ) : null}
        </Panel>
      </div>
    </div>
  );
}
