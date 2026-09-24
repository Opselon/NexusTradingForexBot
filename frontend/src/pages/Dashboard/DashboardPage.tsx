/**
 * Dashboard — the operator's first answer surface (legacy tab-monitoring).
 *
 * Answers immediately, from backend data only:
 *  Is NSE running? LIVE or PAPER? MT5 connected? Trading enabled?
 *  What does the price chart say RIGHT NOW (broker-native bars, SMC/ICT
 *  overlays)? What did the model decide, with what probabilities, from what
 *  provenance? Can the operator start/stop the engine, switch mode, and run
 *  a bounded historical replay — all confirmed by the backend, never faked?
 *
 * Data sources:
 *  - canonical get_system_state() snapshot (REST seed + SSE live merge) —
 *    props, never re-fetched here
 *  - /api/chart/history?count= — authoritative MT5 rate history + visual
 *    overlays (BROKER_NATIVE with explicit ENGINE_STATE fallback)
 *  - /api/v1/runtime/mode — replay flag mirror for the control deck
 *  - /api/replay/* — REPLAY_API v1 session/transport (ReplayPanel)
 *
 * Every section carries its own skeleton/error+retry/empty state; nulls
 * render "—" (UNKNOWN). Command outcomes come from the backend reply only.
 */

import { useCallback, useMemo, useRef, useState } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { engineApi } from "@/api/engineApi";
import { riskApi } from "@/api/riskApi";
import { chartApi, replayApi, runtimeApi } from "@/pages/_shared/edgeApi";
import type { EngineSnapshot, RuntimeRiskState } from "@/types/domain";
import {
  ConfirmModal,
  DataTable,
  EmptyState,
  ErrorState,
  MetricCard,
  Panel,
  PositionSideBadge,
  ProbBar,
  Skeleton,
  StatusBadge,
} from "@/components/primitives";
import { AgeNote, SectionState, fmtAge, TriBadge } from "@/pages/_shared/SectionState";
import { InfoChip } from "@/pages/_shared/widgets";
import { ReplayPanel } from "./ReplayPanel";
import { PriceChart } from "./PriceChart";
import { chartHistoryKey, chartStaleMs } from "./chart/queryPerf";
import { MarketRadarPanel } from "./MarketRadarPanel";
import { FeaturesGrid } from "./FeaturesGrid";
import { PredictionsTable } from "./PredictionsTable";
import { useMutationFeedback } from "@/hooks/useMutationFeedback";
import { useI18n } from "@/stores/i18nStore";
import { formatMoney, formatNumber, formatPct, formatPnl, formatPrice, formatTime } from "@/lib/format";
import { ApiError } from "@/types/api";
import type { VisualOverlays } from "@/pages/_shared/contracts";
import "@/pages/_shared/pages.css";
import "./market-console.css";

interface Props {
  snapshot: EngineSnapshot | undefined;
  nowMs: number;
}

const LIVE_CONFIRM_TEXT = "LIVE";

export default function DashboardPage({ snapshot, nowMs }: Props) {
  const t = useI18n((s) => s.t);
  const engineCmd = useMutationFeedback();
  const modeCmd = useMutationFeedback();
  const [stopConfirm, setStopConfirm] = useState(false);
  const [modeTarget, setModeTarget] = useState("");
  const [liveConfirm, setLiveConfirm] = useState("");
  const [replayConfirm, setReplayConfirm] = useState<boolean | null>(null);
  const [replaySpeed, setReplaySpeed] = useState(1);
  const [replayCmd, setReplayCmd] = useState<{ busy: boolean; msg: string | null; ok: boolean | null }>({ busy: false, msg: null, ok: null });
  const [replayCursor, setReplayCursor] = useState<string | null>(null);
  // Chart timeframe switcher (null = the engine's own execution timeframe).
  const [tfParam, setTfParam] = useState<string | null>(null);
  const engineTfRef = useRef<string | null>(null);

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

  // 3000-bar window for deep pan/zoom (backend caps 5000); the key carries
  // the timeframe so each TF caches separately, and keepPreviousData holds
  // the old bars while the new TF loads (TradingView-like continuity).
  // Lane C (wave 3): staleTime + the query-key/staleness helpers live in
  // chart/queryPerf — the painter reacts to hover/zoom/crosshair with renders
  // that re-read query options, and staleTime:0 turns each of those into a
  // background refetch of the whole 3000-bar window. Live ticks already
  // arrive over SSE; the 60s refetchInterval stays as the dead-stream safety
  // net, so caching the window between beats costs nothing in accuracy and
  // removes the fetch storm.
  const chartQuery = useQuery({
    queryKey: chartHistoryKey(snapshot?.symbol ?? "", tfParam),
    queryFn: ({ signal }) => chartApi.history(3000, tfParam, signal),
    refetchInterval: 60_000,
    staleTime: chartStaleMs(),
    retry: 1,
    enabled: Boolean(snapshot),
    placeholderData: keepPreviousData,
  });
  // Engine-native timeframe = what a no-param response echoes (the backend
  // reports the tf the engine trades on). Captured for the overlay
  // disclosure chip whenever a foreign timeframe is served.
  if (chartQuery.data && tfParam === null) engineTfRef.current = chartQuery.data.timeframe;
  const activeTf = chartQuery.isFetching
    ? (tfParam ?? engineTfRef.current ?? "M1")
    : (chartQuery.data?.timeframe ?? engineTfRef.current ?? "M1");

  const runtimeModeQuery = useQuery({
    queryKey: ["runtime-mode"],
    queryFn: ({ signal }) => runtimeApi.mode(signal),
    refetchInterval: 10_000,
    retry: false,
  });

  const onCursorMove = useCallback((iso: string | null) => setReplayCursor(iso), []);

  if (!snapshot) {
    return (
      <div>
        <Panel title={t("dash.page.title", "Dashboard")}>
          <Skeleton count={6} />
        </Panel>
      </div>
    );
  }

  const acct = snapshot.account;
  const health = snapshot.health;
  const positions = snapshot.positions ?? [];
  const haltState: RuntimeRiskState | null = riskStateQuery.data ?? null;
  const guardianBlocking = haltState?.kill_switch_active === true || (haltState?.runtime_risk_state_effective ?? "").toUpperCase() === "HALTED";
  const currentMode = (snapshot.runtime_mode ?? snapshot.execution_mode ?? "").toUpperCase();
  const replaying = runtimeModeQuery.data?.replaying ?? null;

  // Chart: prefer the dedicated history endpoint (deeper window, broker
  // provenance); fall back to snapshot bars while it loads. Nulls stay null.
  const useServerBars = Boolean(chartQuery.data?.bars?.length);
  const chartBars = useServerBars ? chartQuery.data!.bars : snapshot.bars ?? [];
  // perf: the forming-bar count (filter over the chart bar array) derives once
  // per bar array instead of on every render of this SSE-driven page.
  const formingCount = useMemo(
    () => chartBars.filter((b) => b.is_complete === false).length,
    [chartBars],
  );
  const chartSource = useServerBars ? chartQuery.data!.source : snapshot.bars?.length ? "SNAPSHOT" : null;
  const chartBusy = chartQuery.isPending && !chartQuery.data && !snapshot.bars?.length;
  const chartErr = !useServerBars && chartQuery.isError && !snapshot.bars?.length
    ? chartQuery.error instanceof ApiError
      ? chartQuery.error.localized(t)
      : t("dash.chart.history_failed", "history endpoint failed")
    : null;
  // snapshot.generated_at is the backend's own wall-clock stamp; the 1s UI
  // ticker (nowMs) turns it into an age. Unparseable stamp → "—", never 0s.
  const genMs = Date.parse(snapshot.generated_at);
  const dataAgeSec = Number.isFinite(genMs) ? Math.max(0, (nowMs - genMs) / 1000) : null;

  const toggleEngine = async (active: boolean): Promise<void> => {
    await engineCmd.run(() => engineApi.toggleEngine(active));
  };

  const submitMode = async (): Promise<void> => {
    if (!modeTarget) return;
    if (modeTarget === "LIVE" && liveConfirm !== LIVE_CONFIRM_TEXT) return;
    const ok = await modeCmd.run(() => engineApi.setMode(modeTarget));
    if (ok) {
      setModeTarget("");
      setLiveConfirm("");
    }
  };

  const runReplayToggle = async (active: boolean): Promise<void> => {
    setReplayConfirm(null);
    setReplayCmd({ busy: true, msg: null, ok: null });
    try {
      const res = await replayApi.toggle(active, replaySpeed);
      const refused = res.success === false;
      setReplayCmd({
        busy: false,
        msg:
          res.message ??
          (refused
            ? t("dash.replay.backend_refused", "Backend refused the replay toggle.")
            : t("dash.replay.engage_released", "replay {w}.", {
                w: active ? t("dash.replay.engaged_word", "engaged") : t("dash.replay.released_word", "released"),
              })),
        ok: !refused,
      });
      void runtimeModeQuery.refetch();
    } catch (e) {
      setReplayCmd({
        busy: false,
        msg: e instanceof ApiError ? e.localized(t) : t("dash.replay.toggle_failed", "replay toggle failed"),
        ok: false,
      });
    }
  };

  return (
    <div>
      {/* Top strip — the eight critical answers */}
      <div className="grid cols-4">
        <MetricCard
          label={t("dash.card.engine", "Engine")}
          value={snapshot.engine_running ? t("dash.card.running", "RUNNING") : t("dash.card.stopped", "STOPPED")}
          tone={snapshot.engine_running ? "pos" : "dim"}
          sub={t("dash.card.engine_sub", "warmup/inference: {v}", { v: String(health.subsystems.engine ?? "—") })}
        />
        <MetricCard
          label={t("dash.card.mode", "Mode (backend)")}
          value={currentMode || "—"}
          tone={currentMode.startsWith("LIVE") ? "neg" : "dim"}
          sub={t("dash.card.mode_sub", "data_source: {v}{m}", {
            v: snapshot.data_source ?? "—",
            m: snapshot.mode_source_mismatch ? t("dash.card.mismatch", " · MISMATCH!") : "",
          })}
        />
        <MetricCard
          label={t("dash.card.mt5", "MT5 / Broker")}
          value={String(health.subsystems.mt5 ?? "—")}
          tone={health.subsystems.mt5 === "READY" ? "pos" : undefined}
          sub={String(health.details.mt5 ?? "")}
        />
        <MetricCard
          label={t("dash.card.trading_gate", "Trading gate")}
          value={
            acct.trade_allowed === null
              ? "—"
              : acct.trade_allowed
                ? t("dash.card.allowed", "ALLOWED")
                : t("dash.card.restricted", "RESTRICTED")
          }
          tone={acct.trade_allowed === true ? "pos" : acct.trade_allowed === false ? "neg" : "dim"}
          sub={t("dash.card.gate_sub", "terminal trade_allowed (broker) · guardian: {g}", {
            g: guardianBlocking ? t("dash.card.blocking", "BLOCKING") : t("dash.card.ok", "ok"),
          })}
        />
        <MetricCard
          label={t("dash.card.equity", "Equity")}
          value={formatMoney(acct.equity)}
          sub={t("dash.card.equity_sub", "balance {b} · floating {f}", {
            b: formatMoney(acct.balance),
            f: formatPnl(acct.floating),
          })}
          tone={acct.floating !== null && acct.floating < 0 ? "neg" : acct.floating !== null ? "pos" : undefined}
        />
        <MetricCard
          label={t("dash.card.drawdown", "Drawdown")}
          value={formatPct(acct.drawdown)}
          sub={t("dash.card.drawdown_sub", "peak-equity based (backend computed)")}
          tone={acct.drawdown !== null && acct.drawdown > 5 ? "neg" : undefined}
        />
        <MetricCard
          label={t("dash.card.positions", "Positions / Orders")}
          value={`${acct.open_positions ?? "—"} / ${acct.pending_orders ?? "—"}`}
          sub={t("dash.card.positions_sub", "open / pending (account snapshot)")}
        />
        <MetricCard
          label={t("dash.card.ai_decision", "AI decision")}
          value={snapshot.ai_decision ?? "—"}
          tone={snapshot.ai_decision === "BUY" ? "pos" : snapshot.ai_decision === "SELL" ? "neg" : "dim"}
          sub={t("dash.card.ai_sub", "conf {c} · {r}", {
            c: formatPct(snapshot.ai_confidence === null ? null : (snapshot.ai_confidence ?? 0) * 100, 1),
            r: snapshot.regime ?? t("dash.card.regime_none", "regime —"),
          })}
        />
      </div>

      {/* Provenance strip — where every class of value on this page came from */}
      <div className="l4-toolbar l4-section-gap" aria-label={t("dash.prov.aria", "value provenance")}>
        <span className="l4-prov">
          {t("dash.prov.price", "price")} <b>{snapshot.provenance.price}</b>
        </span>
        <span className="l4-prov">
          {t("dash.prov.features", "features")} <b>{snapshot.provenance.features}</b>
        </span>
        <span className="l4-prov">
          {t("dash.prov.model", "model")} <b>{snapshot.provenance.model}</b>
        </span>
        <span className="l4-prov">
          {t("dash.prov.accounting", "accounting")} <b>{snapshot.provenance.accounting}</b>
        </span>
        <AgeNote
          label={t("dash.age.tick", "tick age")}
          ageSec={snapshot.diagnostics.tick_age_sec}
          suffix={snapshot.tick_stale ? t("dash.age.stale_suffix", "STALE") : undefined}
        />
        <span
          className="timestamp-note"
          title={t("dash.prov.state_age_title", "state age computed from snapshot.generated_at vs the 1s UI ticker")}
        >
          {t("dash.prov.state_age", "state age {a} · v{n}", { a: fmtAge(dataAgeSec), n: snapshot.state_version })}
        </span>
        <span style={{ marginInlineStart: "auto" }} className="l4-chip-row">
          <InfoChip
            k={t("dash.replay.k", "replay")}
            v={
              replaying === null
                ? "—"
                : replaying
                  ? t("dash.replay.engaged", "ENGAGED")
                  : t("dash.replay.off", "OFF")
            }
            tone={replaying ? "warn" : ""}
          />
        </span>
      </div>

      {/* Price chart — the center of the monitoring tab */}
      <div className="l4-section-gap">
        <Panel
          title={`${t("dash.chart.title", "Price")} · ${snapshot.symbol ?? t("dash.chart.symbol_none", "symbol —")} ${chartQuery.data?.timeframe ?? "M1"}`}
          accent
          right={
            <>
              <span className="timestamp-note">
                {chartBars.length
                  ? t("dash.chart.forming_bars", "{n} forming · {m} bars", { n: formingCount, m: chartBars.length })
                  : t("dash.chart.no_bars", "no bars")}
                {chartQuery.data?.generated_at
                  ? t("dash.chart.history", " · history {t}", { t: formatTime(chartQuery.data.generated_at) })
                  : ""}
              </span>
              <button className="btn small ghost" onClick={() => void chartQuery.refetch()} disabled={chartQuery.isFetching}>
                {t("dash.chart.resync", "⟳ resync")}
              </button>
            </>
          }
          tight
        >
          <div style={{ padding: 10 }}>
            <PriceChart
              bars={chartBars}
              digits={snapshot.price_digits ?? 2}
              source={chartSource ?? (chartBars.length ? "UNKNOWN" : "UNAVAILABLE")}
              symbol={chartQuery.data?.symbol ?? snapshot.symbol}
              timeframe={chartQuery.data?.timeframe ?? "M1"}
              activeTf={activeTf}
              onTfChange={setTfParam}
              engineTimeframe={engineTfRef.current}
              overlays={chartQuery.data?.visual_overlays ?? (snapshot.visual_overlays as VisualOverlays)}
              liveBid={snapshot.bid}
              cursorIso={replayCursor}
              stale={snapshot.tick_stale}
              busy={Boolean(chartBusy)}
              error={chartErr}
              onRetry={() => void chartQuery.refetch()}
              caption={
                chartBars.length && !chartQuery.data?.bars?.length
                  ? t("dash.chart.caption", "source: canonical snapshot bars (shallow window)")
                  : undefined
              }
            />
            {snapshot.tick_stale && (
              <div className="confirm-box" style={{ borderColor: "rgba(235,161,63,0.5)" }}>
                <span>
                  {t(
                    "dash.chart.stale_notice",
                    "Backend marks the tick stream stale (freshness {f}) — prices and candles above may be frozen at the last real tick.",
                    {
                      f:
                        snapshot.tick_freshness_ms === null
                          ? "—"
                          : `${(snapshot.tick_freshness_ms / 1000).toFixed(1)}s`,
                    },
                  )}
                </span>
              </div>
            )}
          </div>
        </Panel>
      </div>

      {/* Quote strip — the six market answers under the hero chart (all
          backend fields; nulls stay em-dash, never zero-filled) */}
      <div className="mc-quote l4-section-gap" aria-label={t("dash.quote.aria", "market quote strip")}>
        <div className="mc-quote__item">
          <span className="k">{t("dash.quote.bid", "Bid")}</span>
          <span className="v">{formatPrice(snapshot.bid, snapshot.price_digits ?? 2)}</span>
        </div>
        <div className="mc-quote__item">
          <span className="k">{t("dash.quote.ask", "Ask")}</span>
          <span className="v">{formatPrice(snapshot.ask, snapshot.price_digits ?? 2)}</span>
        </div>
        <div className="mc-quote__item">
          <span className="k">{t("dash.quote.spread", "Spread")}</span>
          <span className="v">{snapshot.spread === null ? "—" : `${formatNumber(snapshot.spread)} pts`}</span>
        </div>
        <div className="mc-quote__item">
          <span className="k">{t("dash.quote.atr", "ATR")}</span>
          <span className="v">{formatNumber(snapshot.atr)}</span>
        </div>
        <div className="mc-quote__item">
          <span className="k">{t("dash.quote.regime", "Regime")}</span>
          <span className="v dim">{snapshot.regime ?? "—"}</span>
        </div>
        <div className="mc-quote__item">
          <span className="k">{t("dash.quote.price_source", "Price source")}</span>
          <span className="v dim">{snapshot.provenance.price}</span>
        </div>
        <AgeNote label={t("dash.age.tick", "tick age")} ageSec={snapshot.diagnostics.tick_age_sec} />
      </div>

      <div className="grid cols-2 l4-section-gap">
        {/* Market Radar — verbatim snapshot.radar (legacy renderMarketRadar) */}
        <MarketRadarPanel radar={snapshot.radar} nowMs={nowMs} />

        {/* Prediction panel — decision-card + probability meters */}
        <Panel
          title={t("dash.pred.title", "Model prediction")}
          right={<AgeNote label={t("dash.age.inference", "inference age")} ageSec={snapshot.diagnostics.inference_age_sec} />}
        >
          <div className="decision-card">
            <div className={`big ${snapshot.ai_decision === "BUY" ? "buy" : snapshot.ai_decision === "SELL" ? "sell" : "hold"}`}>
              {snapshot.ai_decision ?? "—"}
            </div>
            <div>
              <div className="why">
                {snapshot.ai_confidence !== null
                  ? t("dash.pred.conf", "{c} confidence · ", { c: formatPct(snapshot.ai_confidence * 100, 1) })
                  : t("dash.pred.no_conf", "confidence — · ")}
                {snapshot.ai_reason ?? t("dash.pred.no_reason", "no reason code sent")}
              </div>
              <div className="why-detail">
                {t("dash.pred.detail", "proposal {p} · regime {r} · model {m}", {
                  p: snapshot.timestamps.proposal ? formatTime(snapshot.timestamps.proposal) : "—",
                  r: snapshot.regime ?? "—",
                  m: snapshot.model.model_id ?? "—",
                })}
              </div>
              <div className="meta">
                <span className={`l4-chip ${snapshot.probs.available ? "good" : "warn"}`}>
                  {snapshot.probs.available
                    ? t("dash.pred.probs_live", "probs LIVE")
                    : t("dash.pred.probs_unavailable", "probs UNAVAILABLE")}
                </span>
                <span className="l4-chip">
                  {t("dash.pred.inference", "inference {t}", {
                    t: snapshot.probs.inference_timestamp ? formatTime(snapshot.probs.inference_timestamp) : "—",
                  })}
                </span>
              </div>
            </div>
          </div>
          <div style={{ marginTop: 10 }}>
            {snapshot.probs.available ? (
              <ProbBar
                rows={[
                  { label: "P(NO_TRADE)", value: snapshot.probs.no_trade, tone: "flat" },
                  { label: "P(BUY)", value: snapshot.probs.buy, tone: "buy" },
                  { label: "P(SELL)", value: snapshot.probs.sell, tone: "sell" },
                ]}
              />
            ) : (
              <EmptyState
                message={t("dash.pred.empty", "No live inference yet.")}
                hint={t("dash.pred.empty_hint", "probs.available=false — warming up, stopped, or inference blocked. Not rendered as zeros.")}
              />
            )}
          </div>
        </Panel>
      </div>

      {/* Engine control deck + replay mode */}
      <div className="grid cols-2 l4-section-gap">
        <Panel title={t("dash.engine.title", "Engine control")} accent>
          <div className="l4-transport">
            <button className="btn primary" disabled={engineCmd.state.running || snapshot.engine_running} onClick={() => void toggleEngine(true)}>
              {t("dash.engine.start", "▶ Start engine")}
            </button>
            <button className="btn danger" disabled={engineCmd.state.running || !snapshot.engine_running} onClick={() => setStopConfirm(true)}>
              {t("dash.engine.stop", "■ Stop engine")}
            </button>
          </div>
          {engineCmd.state.lastMessage && (
            <div className={`cmd-result ${engineCmd.state.lastResult ? "ok" : "fail"}`}>
              {engineCmd.state.lastResult ? "✓" : "✕"} {engineCmd.state.lastMessage}
            </div>
          )}
          <div className="section-title" style={{ marginTop: 10 }}>{t("dash.engine.mode_title", "Execution mode")}</div>
          <div className="l4-transport">
            <select
              className="select"
              value={modeTarget}
              onChange={(e) => setModeTarget(e.target.value)}
              aria-label={t("dash.engine.mode_aria", "execution mode target")}
            >
              <option value="">{t("dash.engine.select_mode", "select mode…")}</option>
              <option value="PAPER">{t("dash.engine.paper", "PAPER (simulation adapter)")}</option>
              <option value="SHADOW">{t("dash.engine.shadow", "SHADOW (no execution)")}</option>
              <option value="LIVE">{t("dash.engine.live", "LIVE (real capital)")}</option>
            </select>
            <button
              className={`btn ${modeTarget === "LIVE" ? "danger" : "primary"}`}
              disabled={!modeTarget || modeCmd.state.running || modeTarget === currentMode || (modeTarget === "LIVE" && liveConfirm !== LIVE_CONFIRM_TEXT)}
              onClick={() => void submitMode()}
            >
              {t("dash.engine.apply", "Apply mode")}
            </button>
          </div>
          {modeTarget === "LIVE" && (
            <div className="confirm-box">
              <div>
                <b>{t("dash.engine.real_money", "Real money is at risk.")}</b> <span className="muted">{t("dash.engine.live_impact", "The engine will dispatch REAL orders to the connected broker account.")}</span>
              </div>
              <div className="row">
                <input
                  className="input"
                  style={{ width: 200 }}
                  aria-label={t("dash.engine.arm_aria", "LIVE confirmation phrase")}
                  placeholder={t("dash.engine.arm_placeholder", "Type LIVE to arm confirmation")}
                  value={liveConfirm}
                  onChange={(e) => setLiveConfirm(e.target.value.toUpperCase())}
                />
                <span className="note">{t("dash.engine.relay_note", "the backend validates the transition too — the UI only relays")}</span>
              </div>
            </div>
          )}
          {modeCmd.state.lastMessage && (
            <div className={`cmd-result ${modeCmd.state.lastResult ? "ok" : "fail"}`}>
              {modeCmd.state.lastResult ? "✓" : "✕"} {modeCmd.state.lastMessage}
            </div>
          )}
          <div className="small muted" style={{ marginTop: 10 }}>
            {t(
              "dash.engine.verdicts",
              "Verdicts come from the backend response; the authoritative state above updates with the next snapshot — the UI never assumes success.",
            )}
          </div>
        </Panel>

        <Panel
          title={t("dash.replay.mode_panel", "Historical replay mode")}
          right={
            <TriBadge
              value={replaying}
              on={t("dash.replay.engaged", "ENGAGED")}
              off={t("dash.replay.off", "OFF")}
            />
          }
        >
          <div className="l4-transport">
            <button
              className={`btn ${replaying ? "danger" : "primary"}`}
              disabled={replayCmd.busy || replaying === null}
              onClick={() => setReplayConfirm(!replaying)}
            >
              {replaying ? t("dash.replay.leave", "⏏ Leave replay") : t("dash.replay.enter", "⏪ Enter replay")}
            </button>
            <span className="l4-note">
              {t("dash.replay.speed_label", "speed")}{" "}
              <input
                className="input"
                style={{ inlineSize: 48 }}
                value={replaySpeed}
                onChange={(e) => setReplaySpeed(Math.max(1, Math.min(10, Number(e.target.value) || 1)))}
                aria-label={t("dash.replay.speed_aria", "replay speed")}
                type="number"
                min={1}
                max={10}
              />
            </span>
          </div>
          {replayCmd.msg && (
            <div className={`cmd-result ${replayCmd.ok ? "ok" : "fail"}`}>
              {replayCmd.ok ? "✓" : "✕"} {replayCmd.msg}
            </div>
          )}
          <div className="small muted" style={{ marginTop: 8 }}>
            {t("dash.replay.note_a", "The replay flag is INDEPENDENT of the execution mode (SEC-AUDIT-9): entering replay on a simulation boundary restores PAPER on exit, and the backend")}{" "}
            <b>{t("dash.replay.note_b", "refuses replay outright while mode=LIVE")}</b>.{" "}
            {t("dash.replay.note_c", "The decision-visible replay-on-chart pipeline below (REPLAY_API v1) is a separate, research-only surface.")}
          </div>
          <div className="section-title" style={{ marginTop: 10 }}>
            {t("dash.replay.runtime_title", "Runtime mode (v1)")}
          </div>
          <SectionState
            query={runtimeModeQuery}
            skeletonRows={2}
            emptyMessage={t("dash.replay.runtime_empty", "Runtime mode unavailable.")}
            emptyHint={t("dash.replay.runtime_empty_hint", "Engine offline or /api/v1/runtime/mode refused.")}
            errorFallback={t("dash.replay.runtime_error", "Runtime mode endpoint failed.")}
          >
            {(rm) => (
              <dl className="kv">
                <dt>{t("dash.runtime.mode", "mode")}</dt>
                <dd>{rm.mode ?? "—"}</dd>
                <dt>{t("dash.runtime.effective", "effective")}</dt>
                <dd>{rm.effective_mode ?? "—"}</dd>
                <dt>{t("dash.runtime.engine_attached", "engine attached")}</dt>
                <dd>{rm.engine_attached ? t("dash.runtime.yes", "YES") : t("dash.runtime.no", "NO")}</dd>
                <dt>{t("dash.runtime.replaying", "replaying")}</dt>
                <dd>{rm.replaying === null ? "—" : rm.replaying ? t("dash.runtime.yes", "YES") : t("dash.runtime.no", "NO")}</dd>
              </dl>
            )}
          </SectionState>
        </Panel>
      </div>

      {/* Replay-on-chart pipeline (ported from Web/replay_panel.js) */}
      <div className="l4-section-gap">
        <ReplayPanel onCursorMove={onCursorMove} />
      </div>

      <div className="grid cols-2 l4-section-gap">
        {/* Feature grid — all effective-schema values, honest per-status
            (ported FeaturesGrid: category filters, delta pulse, dim header) */}
        <FeaturesGrid
          features={snapshot.features}
          featureDimension={snapshot.model.feature_dimension}
          ageSec={snapshot.diagnostics.features_age_sec}
          pulseKey={snapshot.state_version}
        />

        {/* Subsystem health */}
        <Panel
          title={t("dash.health.title", "Subsystem health")}
          right={<span className="timestamp-note">{t("dash.health.checked", "checked {t}", { t: formatTime(health.checked_at) })}</span>}
        >
          <dl className="kv">
            {Object.entries(health.subsystems).map(([name, st]) => (
              <div key={name} style={{ display: "contents" }}>
                <dt>{name.replace(/_/g, " ")}</dt>
                <dd><StatusBadge status={st} /></dd>
              </div>
            ))}
            <div style={{ display: "contents" }}>
              <dt>{t("dash.health.overall", "overall")}</dt>
              <dd><StatusBadge status={health.overall} /></dd>
            </div>
            <div style={{ display: "contents" }}>
              <dt>{t("dash.health.freshness", "freshness")}</dt>
              <dd><StatusBadge status={snapshot.live_freshness?.overall ?? null} /></dd>
            </div>
          </dl>
        </Panel>
      </div>

      <div className="grid cols-2">
        {/* Guardian / kill switch */}
        <Panel title={t("dash.guardian.title", "Guardian / runtime risk")}>
          {riskStateQuery.isPending ? (
            <Skeleton count={4} />
          ) : riskStateQuery.isError ? (
            <ErrorState message={t("dash.guardian.error", "Guardian state endpoint failed.")} onRetry={() => void riskStateQuery.refetch()} />
          ) : haltState ? (
            <dl className="kv">
              <dt>{t("dash.guardian.kill_switch", "kill switch")}</dt>
              <dd>
                {haltState.kill_switch_active ? (
                  <span className="badge bad">{t("dash.guardian.active", "ACTIVE")}</span>
                ) : (
                  <span className="badge good">{t("dash.guardian.disengaged", "DISENGAGED")}</span>
                )}
              </dd>
              <dt>{t("dash.guardian.risk_state", "runtime risk state")}</dt>
              <dd>
                <span className={`badge ${haltState.runtime_risk_state_effective.toUpperCase() === "RUNNING" ? "good" : "bad"}`}>
                  {haltState.runtime_risk_state_effective}
                </span>
              </dd>
              <dt>{t("dash.guardian.halt_reason", "halt reason")}</dt>
              <dd>{haltState.halt_reason || "—"}</dd>
              <dt>{t("dash.guardian.survival", "survival mode")}</dt>
              <dd>{haltState.survival_mode ? <span className="badge warn">{t("dash.guardian.active", "ACTIVE")}</span> : "—"}</dd>
              <dt>{t("dash.guardian.account_freshness", "account freshness")}</dt>
              <dd>
                <StatusBadge status={haltState.account_freshness} />
              </dd>
              <dt>{t("dash.guardian.consecutive_losses", "consecutive losses")}</dt>
              <dd>{haltState.consecutive_losses}</dd>
              <dt>{t("dash.guardian.dead_letter", "audit dead-letter rows")}</dt>
              <dd className={haltState.audit_dead_letter_rows > 0 ? "pnl-neg" : undefined}>{haltState.audit_dead_letter_rows}</dd>
            </dl>
          ) : (
            <EmptyState
              message={t("dash.guardian.empty", "Guardian state unavailable (backend /api/debug/state offline).")}
              hint={t("dash.guardian.empty_hint", "Shown as UNKNOWN — never inferred.")}
            />
          )}
        </Panel>

        {/* ML / 70D snapshot strip */}
        <Panel
          title={t("dash.ml.title", "ML / 70D")}
          right={<span className="l4-chip">{snapshot.model.feature_schema_id ?? t("dash.ml.schema_none", "schema —")}</span>}
        >
          <dl className="kv">
            <dt>{t("dash.ml.model", "model")}</dt>
            <dd>
              <StatusBadge status={health.subsystems.model ?? null} />
            </dd>
            <dt>{t("dash.ml.inference_freshness", "inference freshness")}</dt>
            <dd>
              <StatusBadge status={health.subsystems.inference_freshness ?? null} />
            </dd>
            <dt>{t("dash.ml.bundle_schema", "bundle schema")}</dt>
            <dd>
              {snapshot.model.feature_schema_id ?? "—"} ({snapshot.model.feature_dimension ?? "?"}D)
            </dd>
            <dt>{t("dash.ml.scaler", "scaler")}</dt>
            <dd>
              {snapshot.model.scaler_ready === null
                ? "—"
                : snapshot.model.scaler_ready
                  ? t("dash.ml.ready", "READY")
                  : t("dash.ml.not_fitted", "NOT FITTED")}
            </dd>
            <dt>{t("dash.ml.latency", "inference latency")}</dt>
            <dd>{snapshot.model.latency_ms === null ? "—" : `${snapshot.model.latency_ms.toFixed(1)} ms`}</dd>
            <dt>{t("dash.ml.model_id", "model id")}</dt>
            <dd>{snapshot.model.model_id ?? "—"}</dd>
          </dl>
        </Panel>
      </div>

      {/* Recent audit events = latest predictions — ported PredictionsTable
          (prob bars from backend softmax values, honest empty state) */}
      <PredictionsTable predictions={snapshot.predictions} />

      {/* Open positions preview + MT5 detail */}
      <div className="grid cols-2">
        <Panel title={t("dash.pos.title", "Open positions ({n})", { n: positions.length })} tight>
          {positions.length === 0 ? (
            <EmptyState
              message={t("dash.pos.empty", "No open positions.")}
              hint={t("dash.pos.empty_hint", "Broker adapter snapshot is empty — nothing hidden, nothing estimated.")}
            />
          ) : (
            <DataTable
              headers={[
                { label: t("dash.pos.ticket", "Ticket") },
                { label: t("dash.pos.symbol", "Symbol") },
                { label: t("dash.pos.side", "Side") },
                { label: t("dash.pos.volume", "Volume"), num: true },
                { label: t("dash.pos.entry", "Entry"), num: true },
                { label: t("dash.pos.current", "Current"), num: true },
                { label: t("dash.pos.pnl", "PnL"), num: true },
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

        <Panel title={t("dash.mt5.title", "MT5 status detail")} tight>
          {mt5Query.isPending ? (
            <div style={{ padding: 12 }}><Skeleton count={4} /></div>
          ) : mt5Query.isError ? (
            <ErrorState
              message={t("dash.mt5.error", "MT5 status unavailable (backend endpoint failed).")}
              requestId={mt5Query.error instanceof ApiError ? mt5Query.error.requestId : null}
              onRetry={() => void mt5Query.refetch()}
            />
          ) : mt5Query.data ? (
            <dl className="kv" style={{ padding: "10px 14px" }}>
              <dt>{t("dash.mt5.connection", "connection")}</dt>
              <dd><StatusBadge status={String((mt5Query.data.connection as { state?: string } | undefined)?.state ?? null)} /></dd>
              <dt>{t("dash.mt5.terminal_version", "terminal version")}</dt>
              <dd>{String((mt5Query.data.connection as { terminal_version?: string } | undefined)?.terminal_version ?? "—")}</dd>
              <dt>{t("dash.mt5.account", "account")}</dt>
              <dd>{mt5Query.data.account?.available ? `${mt5Query.data.account.company ?? "—"} · ${mt5Query.data.account.server ?? "—"}` : "—"}</dd>
              <dt>{t("dash.mt5.pending_orders", "pending orders (broker)")}</dt>
              <dd>{mt5Query.data.orders?.length ?? 0}</dd>
              <dt>{t("dash.mt5.positions", "positions (broker)")}</dt>
              <dd>{mt5Query.data.positions?.length ?? 0}</dd>
            </dl>
          ) : null}
        </Panel>
      </div>

      {/* Confirmations */}
      {stopConfirm && (
        <ConfirmModal
          title={t("dash.confirm.stop_title", "Confirm action — STOP ENGINE")}
          confirmLabel={t("dash.engine.stop", "■ Stop engine")}
          busy={engineCmd.state.running}
          onCancel={() => setStopConfirm(false)}
          onConfirm={() => {
            setStopConfirm(false);
            void toggleEngine(false);
          }}
        >
          <div>
            <b>{t("dash.engine.impact_label", "Impact:")}</b>{" "}
            {t(
              "dash.confirm.stop_body",
              "the engine loop stops — no new proposals, no new executions. Open positions stay on the broker until you act there.",
            )}
            <div className="small muted" style={{ marginTop: 8 }}>
              {t(
                "dash.confirm.stop_recovery",
                "Recovery: Start engine re-attaches the loop; the backend refuses the command if the runtime state forbids it.",
              )}
            </div>
          </div>
        </ConfirmModal>
      )}
      {replayConfirm !== null && (
        <ConfirmModal
          title={t("dash.replay.confirm_title", "Confirm — {w} HISTORICAL REPLAY", {
            w: replayConfirm ? t("dash.replay.enter_word", "ENTER") : t("dash.replay.leave_word", "LEAVE"),
          })}
          danger={replayConfirm ? false : true}
          confirmLabel={replayConfirm ? t("dash.replay.enter", "⏪ Enter replay") : t("dash.replay.leave", "⏏ Leave replay")}
          busy={replayCmd.busy}
          onCancel={() => setReplayConfirm(null)}
          onConfirm={() => void runReplayToggle(replayConfirm)}
        >
          <div>
            {replayConfirm ? (
              <>
                <b>{t("dash.engine.impact_label", "Impact:")}</b>{" "}
                {t(
                  "dash.replay.enter_body",
                  "the engine switches to the historical replay stream on the simulation boundary. The backend refuses this while mode=LIVE.",
                )}
                <div className="small muted" style={{ marginTop: 8 }}>
                  {t(
                    "dash.replay.enter_recovery",
                    "Recovery: Leave replay restores the pre-replay mode captured at entry (never a hardcoded LIVE).",
                  )}
                </div>
              </>
            ) : (
              <>
                <b>{t("dash.engine.impact_label", "Impact:")}</b>{" "}
                {t(
                  "dash.replay.leave_body",
                  "leaves replay; the pre-replay mode is restored by the backend and live ticks resume.",
                )}
                <div className="small muted" style={{ marginTop: 8 }}>
                  {t(
                    "dash.replay.leave_note",
                    "The decision-visible session below (REPLAY_API v1) is unaffected by this flag.",
                  )}
                </div>
              </>
            )}
          </div>
        </ConfirmModal>
      )}
    </div>
  );
}
