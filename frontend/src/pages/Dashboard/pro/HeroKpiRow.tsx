/**
 * HeroKpiRow — the pro first-answer KPI strip.
 *
 * Wired to the EXISTING MetricCard primitive; every value is read straight
 * off the canonical backend snapshot (`get_system_state()`). Nulls render as
 * "—" (UNKNOWN) — no client-side inference, no fabricated defaults.
 */

import type { EngineSnapshot } from "@/types/domain";
import { MetricCard } from "@/components/primitives";
import { formatMoney, formatNumber, formatPct, formatPnl } from "@/lib/format";

interface Props {
  snapshot: EngineSnapshot;
  /** Guardian verdict derived by the page from runtime risk state (backend). */
  guardianBlocking: boolean;
}

export function HeroKpiRow({ snapshot, guardianBlocking }: Props) {
  const acct = snapshot.account;
  const health = snapshot.health;

  return (
    <div className="grid cols-4 hero-kpis">
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
        sub={"peak-equity based (backend computed)"}
        tone={acct.drawdown !== null && acct.drawdown > 5 ? "neg" : undefined}
      />
      <MetricCard
        label="Positions / Orders"
        value={`${acct.open_positions ?? "—"} / ${acct.pending_orders ?? "—"}`}
        sub={`open ${formatNumber(snapshot.positions?.length ?? acct.open_positions, 0)} rows · pending (account snapshot)`}
      />
      <MetricCard
        label="AI decision"
        value={snapshot.ai_decision ?? "—"}
        tone={snapshot.ai_decision === "BUY" ? "pos" : snapshot.ai_decision === "SELL" ? "neg" : "dim"}
        sub={`conf ${formatPct(snapshot.ai_confidence === null ? null : (snapshot.ai_confidence ?? 0) * 100, 1)} · ${snapshot.regime ?? "regime —"}`}
      />
    </div>
  );
}
