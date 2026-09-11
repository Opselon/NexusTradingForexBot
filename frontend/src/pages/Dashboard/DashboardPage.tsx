/**
 * Dashboard — the operator's first answer surface.
 *
 * Answers immediately, from backend data only:
 *  Is NSE running? LIVE or PAPER? MT5 connected? Trading enabled?
 *  Engine healthy? Market state? Positions/orders? Guardian blocking?
 *  ML/70D state?
 *
 * All values are the canonical `get_system_state()` snapshot (REST seed +
 * WebSocket live merge). Nulls render as "—" (UNKNOWN), never fabricated.
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
import { formatMoney, formatNumber, formatPct, formatPnl, formatPrice, formatTime } from "@/lib/format";

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

export default function DashboardPage({ snapshot }: Props) {
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
      {/* Top strip — the six critical answers */}
      <div className="grid cols-4">
        <MetricCard
          label="Engine"
          value={snapshot.engine_running ? "RUNNING" : "STOPPED"}
          tone={snapshot.engine_running ? "pos" : "dim"}
          sub={`warmup/inference: ${String(health.subsystems.engine ?? "—")}`}
        />
        <MetricCard
          label="Mode (backend)"
          value={snapshot.runtime_mode ?? snapshot.execution_mode ?? "—"}
          tone={String(snapshot.runtime_mode ?? "").startsWith("LIVE") ? "neg" : "dim"}
          sub={`data_source: ${snapshot.data_source ?? "—"}${snapshot.mode_source_mismatch ? " · MISMATCH!" : ""}`}
        />
        <MetricCard
          label="MT5 / Broker"
          value={String(health.subsystems.mt5 ?? "—")}
          tone={health.subsystems.mt5 === "READY" ? "pos" : undefined}
          sub={String(health.details.mt5 ?? "")}
        />
        <MetricCard
          label="Trading gate"
          value={acct.trade_allowed === null ? "—" : acct.trade_allowed ? "ALLOWED" : "RESTRICTED"}
          tone={acct.trade_allowed === true ? "pos" : acct.trade_allowed === false ? "neg" : "dim"}
          sub={`terminal trade_allowed (broker) · guardian: ${guardianBlocking ? "BLOCKING" : "ok"}`}
        />
        <MetricCard
          label="Equity"
          value={formatMoney(acct.equity)}
          sub={`balance ${formatMoney(acct.balance)} · floating ${formatPnl(acct.floating)}`}
          tone={acct.floating !== null && acct.floating < 0 ? "neg" : acct.floating !== null ? "pos" : undefined}
        />
        <MetricCard
          label="Drawdown"
          value={formatPct(acct.drawdown)}
          sub={`peak-equity based (backend computed)`}
          tone={acct.drawdown !== null && acct.drawdown > 5 ? "neg" : undefined}
        />
        <MetricCard
          label="Positions / Orders"
          value={`${acct.open_positions ?? "—"} / ${acct.pending_orders ?? "—"}`}
          sub={`open / pending (account snapshot)`}
        />
        <MetricCard
          label="AI decision"
          value={snapshot.ai_decision ?? "—"}
          tone={snapshot.ai_decision === "BUY" ? "pos" : snapshot.ai_decision === "SELL" ? "neg" : "dim"}
          sub={`conf ${formatPct(snapshot.ai_confidence === null ? null : (snapshot.ai_confidence ?? 0) * 100, 1)} · ${snapshot.regime ?? "regime —"}`}
        />
      </div>

      <div className="grid cols-2" style={{ marginTop: 14 }}>
        {/* Market state */}
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

        {/* Subsystem health */}
        <Panel title="Subsystem health" right={<span className="timestamp-note">checked {formatTime(health.checked_at)}</span>}>
          <dl className="kv">
            {Object.entries(health.subsystems).map(([name, st]) => (
              <div key={name} style={{ display: "contents" }}>
                <dt>{name.replace(/_/g, " ")}</dt>
                <dd><StatusBadge status={st} /></dd>
              </div>
            ))}
            <div style={{ display: "contents" }}>
              <dt>overall</dt>
              <dd>
                <StatusBadge status={health.overall} />
              </dd>
            </div>
            <div style={{ display: "contents" }}>
              <dt>freshness</dt>
              <dd><StatusBadge status={snapshot.live_freshness?.overall ?? null} /></dd>
            </div>
          </dl>
        </Panel>
      </div>

      <div className="grid cols-2">
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

      {/* Open positions preview */}
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

      {/* MT5 detail from the dedicated status endpoint */}
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
  );
}
