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
import { MarketRadarPanel } from "./MarketRadarPanel";
import { FeaturesGrid } from "./FeaturesGrid";
import { PredictionsTable } from "./PredictionsTable";
import { useMutationFeedback } from "@/hooks/useMutationFeedback";
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
  const chartQuery = useQuery({
    queryKey: ["chart-history", snapshot?.symbol ?? "", tfParam ?? "engine"],
    queryFn: ({ signal }) => chartApi.history(3000, tfParam, signal),
    refetchInterval: 60_000,
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
        <Panel title="Dashboard">
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
      ? chartQuery.error.message
      : "history endpoint failed"
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
        msg: res.message ?? (refused ? "Backend refused the replay toggle." : `replay ${active ? "engaged" : "released"}.`),
        ok: !refused,
      });
      void runtimeModeQuery.refetch();
    } catch (e) {
      setReplayCmd({ busy: false, msg: e instanceof ApiError ? e.message : "replay toggle failed", ok: false });
    }
  };

  return (
    <div>
      {/* Top strip — the eight critical answers */}
      <div className="grid cols-4">
        <MetricCard
          label="Engine"
          value={snapshot.engine_running ? "RUNNING" : "STOPPED"}
          tone={snapshot.engine_running ? "pos" : "dim"}
          sub={`warmup/inference: ${String(health.subsystems.engine ?? "—")}`}
        />
        <MetricCard
          label="Mode (backend)"
          value={currentMode || "—"}
          tone={currentMode.startsWith("LIVE") ? "neg" : "dim"}
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
        <MetricCard label="Drawdown" value={formatPct(acct.drawdown)} sub="peak-equity based (backend computed)" tone={acct.drawdown !== null && acct.drawdown > 5 ? "neg" : undefined} />
        <MetricCard label="Positions / Orders" value={`${acct.open_positions ?? "—"} / ${acct.pending_orders ?? "—"}`} sub="open / pending (account snapshot)" />
        <MetricCard
          label="AI decision"
          value={snapshot.ai_decision ?? "—"}
          tone={snapshot.ai_decision === "BUY" ? "pos" : snapshot.ai_decision === "SELL" ? "neg" : "dim"}
          sub={`conf ${formatPct(snapshot.ai_confidence === null ? null : (snapshot.ai_confidence ?? 0) * 100, 1)} · ${snapshot.regime ?? "regime —"}`}
        />
      </div>

      {/* Provenance strip — where every class of value on this page came from */}
      <div className="l4-toolbar l4-section-gap" aria-label="value provenance">
        <span className="l4-prov">price <b>{snapshot.provenance.price}</b></span>
        <span className="l4-prov">features <b>{snapshot.provenance.features}</b></span>
        <span className="l4-prov">model <b>{snapshot.provenance.model}</b></span>
        <span className="l4-prov">accounting <b>{snapshot.provenance.accounting}</b></span>
        <AgeNote label="tick age" ageSec={snapshot.diagnostics.tick_age_sec} suffix={snapshot.tick_stale ? "STALE" : undefined} />
        <span className="timestamp-note" title="state age computed from snapshot.generated_at vs the 1s UI ticker">
          state age {fmtAge(dataAgeSec)} · v{snapshot.state_version}
        </span>
        <span style={{ marginInlineStart: "auto" }} className="l4-chip-row">
          <InfoChip k="replay" v={replaying === null ? "—" : replaying ? "ENGAGED" : "OFF"} tone={replaying ? "warn" : ""} />
        </span>
      </div>

      {/* Price chart — the center of the monitoring tab */}
      <div className="l4-section-gap">
        <Panel
          title={`Price · ${snapshot.symbol ?? "symbol —"} ${chartQuery.data?.timeframe ?? "M1"}`}
          accent
          right={
            <>
              <span className="timestamp-note">
                {chartBars.length ? `${formingCount} forming · ${chartBars.length} bars` : "no bars"}
                {chartQuery.data?.generated_at ? ` · history ${formatTime(chartQuery.data.generated_at)}` : ""}
              </span>
              <button className="btn small ghost" onClick={() => void chartQuery.refetch()} disabled={chartQuery.isFetching}>
                ⟳ resync
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
              caption={chartBars.length && !chartQuery.data?.bars?.length ? "source: canonical snapshot bars (shallow window)" : undefined}
            />
            {snapshot.tick_stale && (
              <div className="confirm-box" style={{ borderColor: "rgba(235,161,63,0.5)" }}>
                <span>
                  Backend marks the tick stream <b>stale</b> (freshness{" "}
                  {snapshot.tick_freshness_ms === null ? "—" : `${(snapshot.tick_freshness_ms / 1000).toFixed(1)}s`}) — prices and candles above may be frozen at the
                  last real tick.
                </span>
              </div>
            )}
          </div>
        </Panel>
      </div>

      {/* Quote strip — the six market answers under the hero chart (all
          backend fields; nulls stay em-dash, never zero-filled) */}
      <div className="mc-quote l4-section-gap" aria-label="market quote strip">
        <div className="mc-quote__item">
          <span className="k">Bid</span>
          <span className="v">{formatPrice(snapshot.bid, snapshot.price_digits ?? 2)}</span>
        </div>
        <div className="mc-quote__item">
          <span className="k">Ask</span>
          <span className="v">{formatPrice(snapshot.ask, snapshot.price_digits ?? 2)}</span>
        </div>
        <div className="mc-quote__item">
          <span className="k">Spread</span>
          <span className="v">{snapshot.spread === null ? "—" : `${formatNumber(snapshot.spread)} pts`}</span>
        </div>
        <div className="mc-quote__item">
          <span className="k">ATR</span>
          <span className="v">{formatNumber(snapshot.atr)}</span>
        </div>
        <div className="mc-quote__item">
          <span className="k">Regime</span>
          <span className="v dim">{snapshot.regime ?? "—"}</span>
        </div>
        <div className="mc-quote__item">
          <span className="k">Price source</span>
          <span className="v dim">{snapshot.provenance.price}</span>
        </div>
        <AgeNote label="tick age" ageSec={snapshot.diagnostics.tick_age_sec} />
      </div>

      <div className="grid cols-2 l4-section-gap">
        {/* Market Radar — verbatim snapshot.radar (legacy renderMarketRadar) */}
        <MarketRadarPanel radar={snapshot.radar} nowMs={nowMs} />

        {/* Prediction panel — decision-card + probability meters */}
        <Panel
          title="Model prediction"
          right={<AgeNote label="inference age" ageSec={snapshot.diagnostics.inference_age_sec} />}
        >
          <div className="decision-card">
            <div className={`big ${snapshot.ai_decision === "BUY" ? "buy" : snapshot.ai_decision === "SELL" ? "sell" : "hold"}`}>
              {snapshot.ai_decision ?? "—"}
            </div>
            <div>
              <div className="why">
                {snapshot.ai_confidence !== null ? `${formatPct(snapshot.ai_confidence * 100, 1)} confidence · ` : "confidence — · "}
                {snapshot.ai_reason ?? "no reason code sent"}
              </div>
              <div className="why-detail">
                proposal {snapshot.timestamps.proposal ? formatTime(snapshot.timestamps.proposal) : "—"} · regime {snapshot.regime ?? "—"} · model{" "}
                {snapshot.model.model_id ?? "—"}
              </div>
              <div className="meta">
                <span className={`l4-chip ${snapshot.probs.available ? "good" : "warn"}`}>probs {snapshot.probs.available ? "LIVE" : "UNAVAILABLE"}</span>
                <span className="l4-chip">inference {snapshot.probs.inference_timestamp ? formatTime(snapshot.probs.inference_timestamp) : "—"}</span>
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
              <EmptyState message="No live inference yet." hint="probs.available=false — warming up, stopped, or inference blocked. Not rendered as zeros." />
            )}
          </div>
        </Panel>
      </div>

      {/* Engine control deck + replay mode */}
      <div className="grid cols-2 l4-section-gap">
        <Panel title="Engine control" accent>
          <div className="l4-transport">
            <button className="btn primary" disabled={engineCmd.state.running || snapshot.engine_running} onClick={() => void toggleEngine(true)}>
              ▶ Start engine
            </button>
            <button className="btn danger" disabled={engineCmd.state.running || !snapshot.engine_running} onClick={() => setStopConfirm(true)}>
              ■ Stop engine
            </button>
          </div>
          {engineCmd.state.lastMessage && (
            <div className={`cmd-result ${engineCmd.state.lastResult ? "ok" : "fail"}`}>
              {engineCmd.state.lastResult ? "✓" : "✕"} {engineCmd.state.lastMessage}
            </div>
          )}
          <div className="section-title" style={{ marginTop: 10 }}>Execution mode</div>
          <div className="l4-transport">
            <select className="select" value={modeTarget} onChange={(e) => setModeTarget(e.target.value)} aria-label="execution mode target">
              <option value="">select mode…</option>
              <option value="PAPER">PAPER (simulation adapter)</option>
              <option value="SHADOW">SHADOW (no execution)</option>
              <option value="LIVE">LIVE (real capital)</option>
            </select>
            <button
              className={`btn ${modeTarget === "LIVE" ? "danger" : "primary"}`}
              disabled={!modeTarget || modeCmd.state.running || modeTarget === currentMode || (modeTarget === "LIVE" && liveConfirm !== LIVE_CONFIRM_TEXT)}
              onClick={() => void submitMode()}
            >
              Apply mode
            </button>
          </div>
          {modeTarget === "LIVE" && (
            <div className="confirm-box">
              <div>
                <b>Real money is at risk.</b> <span className="muted">The engine will dispatch REAL orders to the connected broker account.</span>
              </div>
              <div className="row">
                <input
                  className="input"
                  style={{ width: 200 }}
                  aria-label="LIVE confirmation phrase" placeholder="Type LIVE to arm confirmation"
                  value={liveConfirm}
                  onChange={(e) => setLiveConfirm(e.target.value.toUpperCase())}
                />
                <span className="note">the backend validates the transition too — the UI only relays</span>
              </div>
            </div>
          )}
          {modeCmd.state.lastMessage && (
            <div className={`cmd-result ${modeCmd.state.lastResult ? "ok" : "fail"}`}>
              {modeCmd.state.lastResult ? "✓" : "✕"} {modeCmd.state.lastMessage}
            </div>
          )}
          <div className="small muted" style={{ marginTop: 10 }}>
            Verdicts come from the backend response; the authoritative state above updates with the next snapshot — the UI never assumes success.
          </div>
        </Panel>

        <Panel title="Historical replay mode" right={<TriBadge value={replaying} on="ENGAGED" off="OFF" />}>
          <div className="l4-transport">
            <button
              className={`btn ${replaying ? "danger" : "primary"}`}
              disabled={replayCmd.busy || replaying === null}
              onClick={() => setReplayConfirm(!replaying)}
            >
              {replaying ? "⏏ Leave replay" : "⏪ Enter replay"}
            </button>
            <span className="l4-note">
              speed{" "}
              <input
                className="input"
                style={{ inlineSize: 48 }}
                value={replaySpeed}
                onChange={(e) => setReplaySpeed(Math.max(1, Math.min(10, Number(e.target.value) || 1)))}
                aria-label="replay speed"
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
            The replay flag is INDEPENDENT of the execution mode (SEC-AUDIT-9): entering replay on a simulation boundary restores PAPER on exit, and the
            backend <b>refuses replay outright while mode=LIVE</b>. The decision-visible replay-on-chart pipeline below (REPLAY_API v1) is a separate,
            research-only surface.
          </div>
          <div className="section-title" style={{ marginTop: 10 }}>Runtime mode (v1)</div>
          <SectionState
            query={runtimeModeQuery}
            skeletonRows={2}
            emptyMessage="Runtime mode unavailable."
            emptyHint="Engine offline or /api/v1/runtime/mode refused."
            errorFallback="Runtime mode endpoint failed."
          >
            {(rm) => (
              <dl className="kv">
                <dt>mode</dt>
                <dd>{rm.mode ?? "—"}</dd>
                <dt>effective</dt>
                <dd>{rm.effective_mode ?? "—"}</dd>
                <dt>engine attached</dt>
                <dd>{rm.engine_attached ? "YES" : "NO"}</dd>
                <dt>replaying</dt>
                <dd>{rm.replaying === null ? "—" : rm.replaying ? "YES" : "NO"}</dd>
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
              <dd><StatusBadge status={health.overall} /></dd>
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
            <Skeleton count={4} />
          ) : riskStateQuery.isError ? (
            <ErrorState message="Guardian state endpoint failed." onRetry={() => void riskStateQuery.refetch()} />
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
            <EmptyState message="Guardian state unavailable (backend /api/debug/state offline)." hint="Shown as UNKNOWN — never inferred." />
          )}
        </Panel>

        {/* ML / 70D snapshot strip */}
        <Panel title="ML / 70D" right={<span className="l4-chip">{snapshot.model.feature_schema_id ?? "schema —"}</span>}>
          <dl className="kv">
            <dt>model</dt>
            <dd><StatusBadge status={health.subsystems.model ?? null} /></dd>
            <dt>inference freshness</dt>
            <dd><StatusBadge status={health.subsystems.inference_freshness ?? null} /></dd>
            <dt>bundle schema</dt>
            <dd>{snapshot.model.feature_schema_id ?? "—"} ({snapshot.model.feature_dimension ?? "?"}D)</dd>
            <dt>scaler</dt>
            <dd>{snapshot.model.scaler_ready === null ? "—" : snapshot.model.scaler_ready ? "READY" : "NOT FITTED"}</dd>
            <dt>inference latency</dt>
            <dd>{snapshot.model.latency_ms === null ? "—" : `${snapshot.model.latency_ms.toFixed(1)} ms`}</dd>
            <dt>model id</dt>
            <dd>{snapshot.model.model_id ?? "—"}</dd>
          </dl>
        </Panel>
      </div>

      {/* Recent audit events = latest predictions — ported PredictionsTable
          (prob bars from backend softmax values, honest empty state) */}
      <PredictionsTable predictions={snapshot.predictions} />

      {/* Open positions preview + MT5 detail */}
      <div className="grid cols-2">
        <Panel title={`Open positions (${positions.length})`} tight>
          {positions.length === 0 ? (
            <EmptyState message="No open positions." hint="Broker adapter snapshot is empty — nothing hidden, nothing estimated." />
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

        <Panel title="MT5 status detail" tight>
          {mt5Query.isPending ? (
            <div style={{ padding: 12 }}><Skeleton count={4} /></div>
          ) : mt5Query.isError ? (
            <ErrorState message="MT5 status unavailable (backend endpoint failed)." requestId={mt5Query.error instanceof ApiError ? mt5Query.error.requestId : null} onRetry={() => void mt5Query.refetch()} />
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

      {/* Confirmations */}
      {stopConfirm && (
        <ConfirmModal
          title="Confirm action — STOP ENGINE"
          confirmLabel="■ Stop engine"
          busy={engineCmd.state.running}
          onCancel={() => setStopConfirm(false)}
          onConfirm={() => {
            setStopConfirm(false);
            void toggleEngine(false);
          }}
        >
          <div>
            <b>Impact:</b> the engine loop stops — no new proposals, no new executions. Open positions stay on the broker until you act there.
            <div className="small muted" style={{ marginTop: 8 }}>Recovery: Start engine re-attaches the loop; the backend refuses the command if the runtime state forbids it.</div>
          </div>
        </ConfirmModal>
      )}
      {replayConfirm !== null && (
        <ConfirmModal
          title={`Confirm — ${replayConfirm ? "ENTER" : "LEAVE"} HISTORICAL REPLAY`}
          danger={replayConfirm ? false : true}
          confirmLabel={replayConfirm ? "⏪ Enter replay" : "⏏ Leave replay"}
          busy={replayCmd.busy}
          onCancel={() => setReplayConfirm(null)}
          onConfirm={() => void runReplayToggle(replayConfirm)}
        >
          <div>
            {replayConfirm ? (
              <>
                <b>Impact:</b> the engine switches to the historical replay stream on the simulation boundary. The backend refuses this while mode=LIVE.
                <div className="small muted" style={{ marginTop: 8 }}>Recovery: Leave replay restores the pre-replay mode captured at entry (never a hardcoded LIVE).</div>
              </>
            ) : (
              <>
                <b>Impact:</b> leaves replay; the pre-replay mode is restored by the backend and live ticks resume.
                <div className="small muted" style={{ marginTop: 8 }}>The decision-visible session below (REPLAY_API v1) is unaffected by this flag.</div>
              </>
            )}
          </div>
        </ConfirmModal>
      )}
    </div>
  );
}
