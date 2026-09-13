/**
 * Risk — dedicated risk console.
 *
 * SAFETY / ACTIVE / WARNING / BLOCKED / ERROR / UNKNOWN distinction comes from
 * backend state:
 *  - /api/v1/risk/status  → last proposal risk_checks (gate outcomes) + config
 *  - /api/v1/risk/summary → exposure + margin
 *  - /api/debug/state     → kill switch / runtime_risk_state / halt reason
 * No client-side heuristics invent these verdicts.
 *
 * i18n: gate names, PASS/FAIL verdicts, state words (KILL SWITCH ACTIVE,
 * RUNNING...) and config field names are BACKEND words — never translated.
 * Panel titles, <dt> labels and operator-facing notes are UI copy (alt.*).
 */

import { useQuery } from "@tanstack/react-query";
import { riskApi } from "@/api/riskApi";
import type { EngineSnapshot } from "@/types/domain";
import { EmptyState, MetricCard, Panel } from "@/components/primitives";
import { formatMoney, formatNumber, formatPct } from "@/lib/format";
import { GuardianHero, DrawdownBar, MarginArc, GateFunnel, BreakerTiles } from "@/components/pro/RiskViz";
import { useI18n } from "@/stores/i18nStore";
import { ApiError } from "@/types/api";
import { ErrorState } from "@/components/primitives";

interface Props {
  snapshot: EngineSnapshot | undefined;
}

export default function RiskPage({ snapshot }: Props) {
  const t = useI18n((s) => s.t);
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
          label={t("alt.risk.metric_safety", "Safety status")}
          value={runtimeQuery.data ? (runtimeQuery.data.kill_switch_active ? "BLOCKED" : runtimeQuery.data.runtime_risk_state_effective.toUpperCase() === "RUNNING" ? "SAFE" : "WARNING") : "UNKNOWN"}
          tone={runtimeQuery.data ? (runtimeQuery.data.kill_switch_active ? "neg" : runtimeQuery.data.runtime_risk_state_effective.toUpperCase() === "RUNNING" ? "pos" : undefined) : "dim"}
          sub={runtimeQuery.data ? `effective=${runtimeQuery.data.runtime_risk_state_effective}` : t("alt.risk.metric_safety_sub", "backend state unavailable")}
        />
        <MetricCard label={t("alt.risk.metric_equity", "Equity")} value={formatMoney(exposure?.account?.equity ?? snapshot?.account.equity ?? null)} sub={t("alt.risk.margin_sub", "margin {m}", { m: formatMoney(exposure?.account?.margin ?? snapshot?.account.margin ?? null) })} />
        <MetricCard
          label={t("alt.risk.metric_exposure", "Open exposure")}
          value={exposure?.available ? t("alt.risk.exposure_value", "{pos} pos · {lots} lots", { pos: exposure.open_positions ?? 0, lots: formatNumber(exposure.total_volume) }) : "—"}
          tone="dim"
          sub={exposure?.available ? t("alt.risk.floating_sub", "floating {v}", { v: formatMoney(exposure.total_floating_profit) }) : (exposure?.reason ?? t("alt.risk.unavailable_sub", "unavailable"))}
        />
        <MarginArc marginLevelPct={exposure?.account?.margin_level ?? snapshot?.account.margin_level ?? null} thresholdPct={null} thresholdWord={null} label={t("alt.risk.metric_margin_level", "Margin level")} />
      </div>

      <div className="grid cols-2" style={{ marginTop: 14 }}>
        <Panel title={t("alt.risk.panel_guardian", "Guardian / circuit breakers")}>
          {runtimeQuery.isPending ? (
            <div className="muted small">{t("alt.common.loading", "loading…")}</div>
          ) : runtimeQuery.isError ? (
            <ErrorState message={t("alt.risk.err_guardian", "Guardian state endpoint failed.")} onRetry={() => runtimeQuery.refetch()} />
          ) : (
            <>
              <GuardianHero state={runtimeQuery.data ?? null} probedNote={statusQuery.data ? t("alt.risk.probed_note", "probed {time}", { time: statusQuery.data.probed_at }) : undefined} />
              <BreakerTiles state={runtimeQuery.data ?? null} />
            </>
          )}
        </Panel>

        <Panel title={t("alt.risk.panel_config", "Risk configuration (engine, sanitized)")}>
          {statusQuery.isPending ? (
            <div className="muted small">{t("alt.common.loading", "loading…")}</div>
          ) : cfg ? (
            <>
            <dl className="kv">
              <dt>{t("alt.risk.cfg_max_dd", "max drawdown %")}</dt>
              <dd>{formatPct(cfg.max_account_drawdown_pct)}</dd>
              <dt>{t("alt.risk.cfg_risk_trade", "risk per trade %")}</dt>
              <dd>{formatPct(cfg.risk_per_trade_pct)}</dd>
              <dt>{t("alt.risk.cfg_max_pos", "max concurrent positions")}</dt>
              <dd>{cfg.max_concurrent_positions ?? "—"}</dd>
              <dt>{t("alt.risk.cfg_max_spread", "max spread points")}</dt>
              <dd>{formatNumber(cfg.max_spread_points)}</dd>
              <dt>{t("alt.risk.cfg_max_margin", "max margin usage %")}</dt>
              <dd>{formatPct(cfg.max_margin_usage_pct)}</dd>
              <dt>{t("alt.risk.cfg_max_lots", "max allowed lots")}</dt>
              <dd>{formatNumber(cfg.max_allowed_lots)}</dd>
              <dt>{t("alt.risk.cfg_enforce_sl", "enforce stop loss")}</dt>
              <dd>{cfg.enforce_stop_loss === null ? "—" : cfg.enforce_stop_loss ? <span className="badge good">ON</span> : <span className="badge warn">OFF</span>}</dd>
            </dl>
            <div style={{ marginTop: 12 }}>
            <DrawdownBar
              actualPct={snapshot?.account.drawdown ?? null}
              limitPct={cfg.max_account_drawdown_pct ?? null}
            />
            </div>
            </>
          ) : (
            <EmptyState message={t("alt.risk.empty_config", "Risk config unavailable (engine offline or endpoint refused).")} />
          )}
        </Panel>
      </div>

      <Panel
        title={t("alt.risk.panel_gate", "Last proposal gate trace (risk_checks)")}
        right={<span className="timestamp-note">{statusQuery.data ? t("alt.risk.probed_note", "probed {time}", { time: statusQuery.data.probed_at }) : ""}</span>}
      >
        {statusQuery.isPending ? (
          <div className="muted small">{t("alt.common.loading", "loading…")}</div>
        ) : statusQuery.data === undefined ? (
          <ErrorState message={t("alt.risk.err_status", "Risk status endpoint failed.")} onRetry={() => statusQuery.refetch()} />
        ) : statusQuery.data.last_proposal_present && statusQuery.data.risk_checks ? (
          <GateFunnel checks={statusQuery.data.risk_checks} />
        ) : (
          <EmptyState message={t("alt.risk.empty_gate", "No proposal risk_checks yet (engine has not evaluated a trade this session).")} />
        )}
      </Panel>

      <Panel title={t("alt.risk.panel_exposure", "Exposure by symbol")}>
        {exposure?.available && exposure.by_symbol && Object.keys(exposure.by_symbol).length > 0 ? (
          <dl className="kv">
            {Object.entries(exposure.by_symbol).map(([sym, s]) => (
              <div key={sym} style={{ display: "contents" }}>
                <dt>{sym}</dt>
                <dd>
                  {t("alt.risk.exposure_row", "{pos} pos · {lots} lots · {money}", { pos: s.positions, lots: formatNumber(s.volume), money: formatMoney(s.profit) })}
                </dd>
              </div>
            ))}
          </dl>
        ) : (
          <EmptyState message={t("alt.risk.empty_exposure", "No open exposure.")} />
        )}
      </Panel>

      {statusQuery.error instanceof ApiError && statusQuery.error.isAuthError && (
        <div className="banner auth"><span>{t("alt.risk.reauth_banner", "⛔ Re-authentication required — reopen with ?token=…")}</span></div>
      )}
    </div>
  );
}
