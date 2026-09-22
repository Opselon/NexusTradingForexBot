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
import { BreakerTiles, DrawdownBar, GateFunnel, GuardianHero, MarginArc } from "@/components/pro/RiskViz";
import { EmptyState, ErrorState, MetricCard, Panel, Skeleton, StatusBadge } from "@/components/primitives";
import { AgeNote, SectionState, errorText } from "@/pages/_shared/SectionState";
import { MeterBar, SortableTable, type Column, type MeterTone } from "@/pages/_shared/widgets";
import { gateEvidence, directionWord, type GateEvidenceRow } from "@/lib/riskGateTrace";
import { limitUtilization } from "@/lib/riskVizMath";
import { formatMoney, formatNumber, formatPct } from "@/lib/format";
import { ApiError } from "@/types/api";
import { useI18n } from "@/stores/i18nStore";
import "@/pages/_shared/pages.css";

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
  reason: string | null;
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
    reason: row.detail,
  };
}

function RiskMatrix({ checks }: { checks: RiskChecks }) {
  const t = useI18n((s) => s.t);
  const verdictText = (v: "pass" | "fail" | "unknown"): string =>
    v === "pass" ? t("risk.matrix.verdict.pass", "PASS") : v === "fail" ? t("risk.matrix.verdict.fail", "FAIL") : t("risk.matrix.verdict.unknown", "UNKNOWN");
  const noReason = () => t("risk.matrix.no_reason", "no backend reason in payload — not a passing gate");
  const ev = useMemo(() => gateEvidence(checks), [checks]);
  const rows = useMemo(() => ev.rows.map(gateRow), [ev]);
  const cols = useMemo<Array<Column<GateRowVM>>>(
    () => [
      {
        key: "gate",
        label: t("risk.matrix.col.gate", "Gate"),
        sortValue: (r) => r.name,
        render: (r) => r.name.replace(/_/g, " "),
      },
      {
        key: "verdict",
        label: t("risk.matrix.col.verdict", "Verdict"),
        sortValue: (r) => r.verdict,
        render: (r) => (
          <span className={`l4-chip ${r.verdict === "pass" ? "good" : r.verdict === "fail" ? "bad" : ""}`}>
            {verdictText(r.verdict)}
          </span>
        ),
      },
      { key: "value", label: t("risk.matrix.col.value", "Value"), num: true, sortValue: (r) => (r.value === "—" ? null : r.value), render: (r) => r.value },
      { key: "limit", label: t("risk.matrix.col.limit", "Limit"), num: true, sortValue: (r) => (r.limit === "—" ? null : r.limit), render: (r) => r.limit },
      {
        key: "reason",
        label: t("risk.matrix.col.reason", "Reason (backend)"),
        render: (r) => {
          const reason = r.reason ?? noReason();
          return <span className="small muted" title={reason}>{reason.length > 60 ? `${reason.slice(0, 60)}…` : reason}</span>;
        },
      },
    ],
    [t],
  );
  return (
    <>
      <SortableTable columns={cols} rows={rows} rowKey={(r) => r.name} emptyMessage={t("risk.matrix.empty", "No gate rows.")} maxHeight={null} />
      <div className="l4-note" style={{ padding: "6px 12px" }}>
        {t(
          "risk.matrix.summary",
          "{pass} pass · {fail} fail · {unknown} unknown — each PASS/FAIL is the arithmetic restatement of two backend-supplied numbers (value vs its own declared limit); an entry whose limit is absent is UNKNOWN, never FAIL.",
          { pass: ev.pass, fail: ev.fail, unknown: ev.unknown },
        )}
      </div>
    </>
  );
}

function GuardianBlock({ state }: { state: RuntimeRiskState | null }) {
  const t = useI18n((s) => s.t);
  if (!state) return <EmptyState message={t("risk.guardian.empty_msg", "Runtime risk state unavailable (shown as UNKNOWN — never inferred).")} hint={t("risk.guardian.empty_hint", "/api/debug/state risk section did not answer.")} />;
  const effective = state.runtime_risk_state_effective.toUpperCase();
  const level = state.kill_switch_active || effective === "HALTED" ? "bad" : effective === "RUNNING" ? "good" : "warn";
  return (
    <dl className="kv">
      <dt>{t("risk.guardian.safety_status", "safety status")}</dt>
      <dd><span className={`badge ${level}`}>{state.kill_switch_active ? t("risk.guardian.kill_switch", "KILL SWITCH ACTIVE") : effective}</span></dd>
      <dt>{t("risk.guardian.halt_reason", "halt reason")}</dt>
      <dd>{state.halt_reason || "—"}</dd>
      <dt>{t("risk.guardian.halt_triggered", "halt triggered")}</dt>
      <dd>{state.halt_triggered_at || "—"}</dd>
      <dt>{t("risk.guardian.survival_mode", "survival mode")}</dt>
      <dd>{state.survival_mode ? <span className="badge warn">{t("risk.guardian.active", "ACTIVE")}</span> : <span className="badge good">{t("risk.guardian.off", "OFF")}</span>}</dd>
      <dt>{t("risk.guardian.account_freshness", "account freshness")}</dt>
      <dd><StatusBadge status={state.account_freshness} /></dd>
      <dt>{t("risk.guardian.consecutive_losses", "consecutive losses")}</dt>
      <dd>{state.consecutive_losses}</dd>
      <dt>{t("risk.guardian.hard_max_lots", "hard max lots")}</dt>
      <dd>{formatNumber(state.hard_max_lots)}</dd>
      <dt>{t("risk.guardian.backpressure", "financial backpressure")}</dt>
      <dd className={state.financial_queue_backpressure > 0 ? "pnl-neg" : undefined}>{state.financial_queue_backpressure}</dd>
      <dt>{t("risk.guardian.config_error", "config error")}</dt>
      <dd className={state.config_error ? "pnl-neg" : undefined}>{state.config_error ?? "—"}</dd>
    </dl>
  );
}

export default function RiskPage({ snapshot, nowMs }: Props) {
  const t = useI18n((s) => s.t);
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

  const drawdownActual = acct?.drawdown ?? null;
  const marginLevel = exposure?.account?.margin_level ?? acct?.margin_level ?? null;
  // Margin floor: the ONLY backend-supplied threshold we may compare against.
  // None exists in the risk payload → MarginArc renders indeterminate by design.
  const marginFloorPct: number | null = null;

  const volUtil = limitUtilization(exposure?.total_volume ?? null, cfg?.max_allowed_lots ?? null);
  const volTone: MeterTone = volUtil === null ? "unknown" : volUtil > 1 ? "bad" : volUtil > 0.8 ? "warn" : "ok";
  const marginUsagePct =
    exposure?.account?.margin !== null && exposure?.account?.margin !== undefined && exposure.account.equity
      ? (exposure.account.margin / exposure.account.equity) * 100
      : null;
  const marginUtil = limitUtilization(marginUsagePct, cfg?.max_margin_usage_pct ?? null);
  const marginTone: MeterTone = marginUtil === null ? "unknown" : marginUtil > 1 ? "bad" : marginUtil > 0.8 ? "warn" : "ok";
  const spreadUtil = limitUtilization(snapshot?.spread ?? null, cfg?.max_spread_points ?? null);
  const spreadTone: MeterTone = spreadUtil === null ? "unknown" : spreadUtil > 1 ? "bad" : spreadUtil > 0.8 ? "warn" : "ok";

  const bySymbol = exposure?.by_symbol ?? {};
  const exposureRows = Object.entries(bySymbol);

  return (
    <div>
      <div className="grid cols-4">
        <MetricCard
          label={t("risk.metric.safety_status", "Safety status")}
          value={haltState ? (haltState.kill_switch_active ? t("risk.status.blocked", "BLOCKED") : haltState.runtime_risk_state_effective.toUpperCase() === "RUNNING" ? t("risk.status.safe", "SAFE") : t("risk.status.warning", "WARNING")) : t("risk.status.unknown", "UNKNOWN")}
          tone={haltState ? (haltState.kill_switch_active ? "neg" : haltState.runtime_risk_state_effective.toUpperCase() === "RUNNING" ? "pos" : undefined) : "dim"}
          sub={haltState ? `effective=${haltState.runtime_risk_state_effective}` : t("risk.metric.state_unavailable", "backend state unavailable")}
        />
        <MetricCard label={t("risk.metric.equity", "Equity")} value={formatMoney(exposure?.account?.equity ?? acct?.equity ?? null)} sub={t("risk.metric.margin_sub", "margin {m}", { m: formatMoney(exposure?.account?.margin ?? acct?.margin ?? null) })} />
        <MetricCard
          label={t("risk.metric.open_exposure", "Open exposure")}
          value={exposure?.available ? t("risk.metric.open_value", "{n} pos · {v} lots", { n: exposure.open_positions ?? 0, v: formatNumber(exposure.total_volume) }) : "—"}
          tone="dim"
          sub={exposure?.available ? t("risk.metric.floating_sub", "floating {m}", { m: formatMoney(exposure.total_floating_profit) }) : (exposure?.reason ?? t("risk.metric.unavailable", "unavailable"))}
        />
        <MetricCard
          label={t("risk.metric.margin_level", "Margin level")}
          value={marginLevel === null || marginLevel === undefined ? "—" : formatPct(marginLevel, 1)}
          tone={typeof marginLevel === "number" && marginLevel < 200 ? "neg" : "dim"}
          sub={t("risk.metric.margin_level_sub", "broker margin_level %")}
        />
      </div>

      {/* Guardian hero + breakers from the committed pro kit (echo backend) */}
      <div className="l4-section-gap">
        <GuardianHero
          state={haltState}
          probedNote={runtimeQuery.dataUpdatedAt ? t("risk.guardian.probed", "probed {s}s ago", { s: Math.max(0, (tickMs - runtimeQuery.dataUpdatedAt) / 1000).toFixed(1) }) : undefined}
        />
      </div>
      <div className="grid cols-2 l4-section-gap">
        <Panel title={t("risk.panel.breakers", "Circuit-breaker counters")} right={<AgeNote label={t("risk.age.label", "age")} ageSec={runtimeQuery.dataUpdatedAt ? (tickMs - runtimeQuery.dataUpdatedAt) / 1000 : null} />}>
          <BreakerTiles state={haltState} />
        </Panel>
        <Panel title={t("risk.panel.gauges", "Limit gauges (backend value vs backend limit)")} right={<span className="timestamp-note">{t("risk.gauges.note", "missing limit ⇒ indeterminate, never satisfied")}</span>}>
          {statusQuery.isPending && !statusQuery.data ? (
            <Skeleton count={3} />
          ) : (
            <div style={{ display: "grid", gap: 10 }}>
              <DrawdownBar actualPct={drawdownActual} limitPct={cfg?.max_account_drawdown_pct ?? null} />
              <div style={{ display: "flex", gap: 16, alignItems: "center", flexWrap: "wrap" }}>
                <MarginArc marginLevelPct={marginLevel} thresholdPct={marginFloorPct} thresholdWord={null} />
                <div style={{ flex: 1, minWidth: 220 }}>
                  <MeterBar label={t("risk.meter.volume", "Position volume")} value={exposure?.total_volume ?? null} limit={cfg?.max_allowed_lots ?? null} unit=" lots" tone={volTone} />
                  <div style={{ blockSize: 8 }} />
                  <MeterBar
                    label={t("risk.meter.margin", "Margin usage")}
                    value={marginUsagePct}
                    limit={cfg?.max_margin_usage_pct ?? null}
                    unit="%"
                    digits={1}
                    tone={marginTone}
                    caption={
                      marginUtil === null
                        ? t("risk.meter.margin_cap_unknown", "margin usage needs both broker margin/equity and the engine max_margin_usage_pct — a missing side means the budget is unknown")
                        : t("risk.meter.margin_cap_ok", "equity-relative margin usage (arithmetic on backend values) vs engine max_margin_usage_pct")
                    }
                  />
                  <div style={{ blockSize: 8 }} />
                  <MeterBar
                    label={t("risk.meter.spread", "Spread")}
                    value={snapshot?.spread ?? null}
                    limit={cfg?.max_spread_points ?? null}
                    unit=" pts"
                    digits={1}
                    tone={spreadTone}
                    caption={spreadUtil === null ? t("risk.meter.spread_cap_unknown", "live spread or max_spread_points missing — gate not judgeable here") : t("risk.meter.spread_cap_ok", "live snapshot spread vs the engine spread gate limit")}
                  />
                </div>
              </div>
            </div>
          )}
        </Panel>
      </div>

      <div className="grid cols-2 l4-section-gap">
        <Panel title={t("risk.panel.guardian", "Guardian / runtime detail")}>
          {runtimeQuery.isPending ? (
            <Skeleton count={5} />
          ) : runtimeQuery.isError ? (
            <ErrorState message={errorText(runtimeQuery.error, t("risk.error.guardian", "Guardian state endpoint failed."))} requestId={runtimeQuery.error instanceof ApiError ? runtimeQuery.error.requestId : null} onRetry={() => void runtimeQuery.refetch()} />
          ) : (
            <GuardianBlock state={runtimeQuery.data ?? null} />
          )}
        </Panel>

        <Panel title={t("risk.panel.config", "Risk configuration (engine, sanitized)")}>
          {statusQuery.isPending ? (
            <Skeleton count={5} />
          ) : statusQuery.isError ? (
            <ErrorState message={errorText(statusQuery.error, t("risk.error.status", "Risk status endpoint failed."))} requestId={statusQuery.error instanceof ApiError ? statusQuery.error.requestId : null} onRetry={() => void statusQuery.refetch()} />
          ) : cfg ? (
            <dl className="kv">
              <dt>{t("risk.cfg.max_drawdown", "max drawdown %")}</dt>
              <dd>{formatPct(cfg.max_account_drawdown_pct)}</dd>
              <dt>{t("risk.cfg.risk_per_trade", "risk per trade %")}</dt>
              <dd>{formatPct(cfg.risk_per_trade_pct)}</dd>
              <dt>{t("risk.cfg.max_concurrent", "max concurrent positions")}</dt>
              <dd>{cfg.max_concurrent_positions ?? "—"}</dd>
              <dt>{t("risk.cfg.max_spread", "max spread points")}</dt>
              <dd>{formatNumber(cfg.max_spread_points)}</dd>
              <dt>{t("risk.cfg.max_margin", "max margin usage %")}</dt>
              <dd>{formatPct(cfg.max_margin_usage_pct)}</dd>
              <dt>{t("risk.cfg.max_lots", "max allowed lots")}</dt>
              <dd>{formatNumber(cfg.max_allowed_lots)}</dd>
              <dt>{t("risk.cfg.enforce_sl", "enforce stop loss")}</dt>
              <dd>{cfg.enforce_stop_loss === null ? "—" : cfg.enforce_stop_loss ? <span className="badge good">{t("risk.cfg.on", "ON")}</span> : <span className="badge warn">{t("risk.cfg.off", "OFF")}</span>}</dd>
            </dl>
          ) : (
            <EmptyState message={t("risk.cfg.empty", "Risk config unavailable (engine offline or endpoint refused).")} hint={t("risk.cfg.empty_hint", "Rendered as UNKNOWN — limits are never assumed.")} />
          )}
        </Panel>
      </div>

      <Panel
        title={t("risk.panel.matrix", "Last proposal gate matrix (risk_checks)")}
        right={
          <AgeNote
            label={t("risk.age.probed", "probed")}
            ageSec={statusQuery.data ? (tickMs - Date.parse(statusQuery.data.probed_at)) / 1000 : null}
            suffix={statusQuery.data?.last_proposal_present ? t("risk.matrix.suffix_present", "proposal present") : t("risk.matrix.suffix_absent", "no proposal yet")}
          />
        }
        tight
      >
        {statusQuery.isPending && !statusQuery.data ? (
          <div style={{ padding: 12 }}><Skeleton count={4} /></div>
        ) : statusQuery.isError && !statusQuery.data ? (
          <ErrorState message={errorText(statusQuery.error, t("risk.error.status", "Risk status endpoint failed."))} requestId={statusQuery.error instanceof ApiError ? statusQuery.error.requestId : null} onRetry={() => void statusQuery.refetch()} />
        ) : statusQuery.data?.last_proposal_present && statusQuery.data.risk_checks ? (
          <>
            <div style={{ padding: "10px 12px 0" }}>
              <GateFunnel checks={statusQuery.data.risk_checks} />
            </div>
            <RiskMatrix checks={statusQuery.data.risk_checks} />
          </>
        ) : (
          <EmptyState message={t("risk.matrix.empty_state", "No proposal risk_checks yet (engine has not evaluated a trade this session).")} hint={t("risk.matrix.empty_state_hint", "The matrix appears with the first proposal — an empty store is not a passing gate.")} />
        )}
      </Panel>

      <Panel
        title={t("risk.panel.exposure", "Exposure by symbol")}
        right={
          <>
            <span className="timestamp-note">source /api/v1/risk/summary</span>
            <button className="btn small ghost" onClick={() => void summaryQuery.refetch()} disabled={summaryQuery.isFetching}>⟳</button>
          </>
        }
      >
        <SectionState
          query={summaryQuery}
          emptyMessage={t("risk.exposure.empty", "No open exposure.")}
          emptyHint={t("risk.exposure.empty_hint", "The backend reported zero rows — not a rendering gap.")}
          errorFallback={t("risk.error.exposure", "Exposure endpoint failed.")}
          emptyWhen={(d) => !(d.exposure?.available && Object.keys(d.exposure.by_symbol ?? {}).length > 0)}
        >
          {(d) => (
            <div style={{ display: "grid", gap: 8 }}>
              {d.exposure?.available === false && d.exposure.reason ? <div className="l4-note warn">{t("risk.exposure.unavailable", "exposure unavailable: {reason}", { reason: d.exposure.reason })}</div> : null}
              {(() => {
                const rows = Object.entries(d.exposure?.by_symbol ?? {});
                if (rows.length === 0) {
                  return <EmptyState message={t("risk.exposure.empty", "No open exposure.")} hint={t("risk.exposure.empty_hint", "The backend reported zero rows — not a rendering gap.")} />;
                }
                const maxVol = Math.max(...rows.map(([, s]) => (Number.isFinite(s.volume) ? s.volume : 0)), 0) || 1;
                return rows.map(([sym, s]) => (
                  <MeterBar
                    key={sym}
                    label={t("risk.exposure.symbol_label", "{sym} · {n} pos", { sym, n: s.positions })}
                    value={s.volume}
                    limit={maxVol}
                    unit=" lots"
                    tone={(s.profit ?? 0) < 0 ? "warn" : "ok"}
                    fraction={s.volume / maxVol}
                    caption={t("risk.exposure.symbol_caption", "{m} floating · share of the largest symbol (relativity only, no limit claimed)", { m: formatMoney(s.profit) })}
                  />
                ));
              })()}
              {crossMismatch && (
                <div className="confirm-box" style={{ borderColor: "rgba(235,161,63,0.5)" }}>
                  <span>
                    <b>{t("risk.exposure.crosscheck_lead", "Read mismatch:")}</b>{" "}
                    {t("risk.exposure.crosscheck_text", "/api/v1/risk/summary counts {a} open positions, the canonical snapshot {b}. Two backend reads at different instants — re-probe; if it persists, reconcile on the Trading page before trusting the aggregate.", { a: String(posA), b: String(posB) })}
                  </span>
                </div>
              )}
              {exposureRows.length === 0 && d.exposure?.available && <div className="l4-note">{t("risk.exposure.available_empty", "summary reports available with an empty by_symbol map.")}</div>}
            </div>
          )}
        </SectionState>
      </Panel>

      {statusQuery.error instanceof ApiError && statusQuery.error.isAuthError && (
        <div className="banner auth"><span>{"⛔ "}{t("risk.auth.banner", "Re-authentication required — reopen with ?token=…")}</span></div>
      )}
    </div>
  );
}
