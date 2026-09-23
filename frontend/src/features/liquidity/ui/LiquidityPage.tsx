/**
 * Liquidity — Liquidity Intelligence + MSLIE market-structure perception
 * (legacy tab-liquidity parity).
 *
 * Sections: state cards (enabled/available/calculation vs source split —
 * BUG-110 contract) · ten-value feature table · sweep visualization-lite
 * (liquidity map heat list + last sweep card) · confirm-guarded toggle.
 * MSLIE vector exposed read-only.
 *
 * WAVE nse-uiux-w8: presentation-only upgrade (operator hero + section
 * labels). Logic is unchanged: every query key / refetchInterval / retry /
 * enabled flag, the ConfirmModal gate, the BUG-110 calculation-vs-source
 * split and every rendered value are preserved verbatim — only the markup
 * wrapper, class names and styling moved. Styling: ./liquidity.css (.liq-*).
 */

import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import type { ShellPageProps } from "@/app/featureModule";
import {
  ConfirmModal,
  DataTable,
  EmptyState,
  ErrorState,
  MetricCard,
  Panel,
  Skeleton,
  StatusBadge,
} from "@/components/primitives";
import { useMutationFeedback } from "@/hooks/useMutationFeedback";
import { formatDateTime, formatNumber, formatPrice } from "@/lib/format";
import { CommandResultLine, FreshnessCaption, InfoRow, JsonBlock, StatusPill } from "../../research/ui/lane5Kit";
import { arr, bool, num, obj, str, toZoneRow, toggleVerdict } from "../model";
import { liquidityQueries, liquidityUseCases } from "../useCases";
import "./liquidity.css";

export default function LiquidityPage(props: ShellPageProps) {
  void props;
  const [confirmTo, setConfirmTo] = useState<boolean | null>(null);
  const cmd = useMutationFeedback();
  const qc = useQueryClient();

  const stateQ = useQuery({
    queryKey: ["liquidity", "state"],
    queryFn: ({ signal }) => liquidityQueries.state(signal),
    refetchInterval: 15_000,
    retry: false,
  });
  const featuresQ = useQuery({
    queryKey: ["liquidity", "features"],
    queryFn: ({ signal }) => liquidityQueries.features(signal),
    refetchInterval: 15_000,
    retry: false,
  });
  const mslieQ = useQuery({
    queryKey: ["liquidity", "mslie-status"],
    queryFn: ({ signal }) => liquidityQueries.mslieStatus(signal),
    refetchInterval: 15_000,
    retry: false,
  });
  const vectorQ = useQuery({
    queryKey: ["liquidity", "mslie-features"],
    queryFn: ({ signal }) => liquidityQueries.mslieFeatures(signal),
    retry: false,
  });

  const s = stateQ.data;
  const enabled = bool(s?.enabled) ?? false;
  const zones = arr(mslieQ.data?.liquidity_map).map(toZoneRow);
  const sweep = obj(mslieQ.data?.last_sweep);
  const pools = arr(s?.pools);
  const featureNames = s?.feature_names ?? Object.keys(obj(s?.features));
  const featureMap = obj(s?.features);

  return (
    <div className="liq-page">
      <header className="liq-hero">
        <div className="liq-hero-main">
          <div className="liq-kicker">
            <span className="dot" aria-hidden="true" />
            MARKET &amp; RESEARCH
          </div>
          <h1 className="liq-title">
            <span className="word">Liquidity</span>
          </h1>
          <p className="liq-desc">
            Liquidity intelligence governor + MSLIE market structure — the ten-value runtime snapshot, the
            calculation/source state split, and the live liquidity-map / sweep perception. All states below are read
            from the backend; the console never infers a sweep that was not recorded.
          </p>
        </div>
        <div className="liq-hero-side">
          <div className="liq-side-pills">
            <span
              className={`liq-state-pill ${enabled ? "on" : "off"}`}
              title="backend governor state from /api/liquidity/state"
            >
              <span className="d" aria-hidden="true" />
              {s ? (enabled ? "governor enabled" : "governor disabled") : "governor unknown"}
            </span>
            <span
              className="liq-side-pill"
              title="calculation_status — did the last compute attempt succeed (BUG-110: orthogonal to source)"
            >
              calc <StatusPill status={s?.calculation_status ?? "NOT_RUN"} />
            </span>
            <span
              className="liq-side-pill"
              title="source_status — where the input data came from (BUG-110: orthogonal to calculation)"
            >
              src <StatusPill status={s?.source_status ?? s?.source ?? "UNKNOWN"} />
            </span>
          </div>
          <div className="liq-ep-chips" role="list" aria-label="Liquidity provenance endpoints">
            <span className="liq-ep" role="listitem">
              GET /api/liquidity/state
            </span>
            <span className="liq-ep" role="listitem">
              GET /api/liquidity/features
            </span>
            <span className="liq-ep warn" role="listitem">
              POST /api/liquidity/toggle
            </span>
            <span className="liq-ep" role="listitem">
              GET /api/mslie/status
            </span>
            <span className="liq-ep" role="listitem">
              GET /api/mslie/features
            </span>
          </div>
          <FreshnessCaption
            timestamp={s?.last_update ?? undefined}
            source={`rev ${String(s?.state_revision ?? "—")}`}
            isFetching={stateQ.isFetching}
            error={stateQ.isError}
          />
        </div>
      </header>

      <div>
        <div className="liq-sec-label">
          <span className="liq-sec-i">01</span> Governor state
          <span className="liq-sec-note">calculation vs source kept separate (BUG-110)</span>
        </div>
        <div className="liq-state-grid">
          <MetricCard
            label="Governor (backend)"
            value={<StatusBadge status={s ? (enabled ? "ACTIVE" : "DISABLED") : "UNKNOWN"} />}
            sub={`source ${s?.source ?? "—"} · algo ${s?.algorithm_version ?? "—"}`}
            tone={enabled ? "pos" : "dim"}
          />
          <MetricCard
            label="calculation status"
            value={<StatusPill status={s?.calculation_status ?? "NOT_RUN"} />}
            sub="did the last compute attempt succeed"
          />
          <MetricCard
            label="source status"
            value={<StatusPill status={s?.source_status ?? s?.source ?? "UNKNOWN"} />}
            sub="where the input data came from (orthogonal per BUG-110)"
          />
          <MetricCard
            label="latency / age"
            value={`${s?.latency_ms === null || s?.latency_ms === undefined ? "—" : formatNumber(s.latency_ms, 2)} ms`}
            sub={`causal ${s?.causal_state ?? "—"} · age ${s?.age_sec === null || s?.age_sec === undefined ? "—" : formatNumber(s.age_sec, 1)}s`}
          />
        </div>
      </div>

      <Panel
        title="Liquidity Intelligence toggle"
        accent
        right={
          <button className={`btn small ${enabled ? "danger" : "primary"}`} disabled={stateQ.isPending || cmd.state.running} title={cmd.state.running ? "toggle already in flight" : undefined} onClick={() => setConfirmTo(!enabled)}>
            {cmd.state.running ? (enabled ? "disabling…" : "enabling…") : enabled ? "disable" : "enable"}
          </button>
        }
        tight
      >
        <div className="small muted">
          Toggle persists via SettingsService (HOT_RESTRICTED) and hot-applies the governor — it never restarts the
          engine and never touches orders/risk/execution. The backend returns the new authoritative state; the UI
          re-reads it.
        </div>
        {s?.model_compatibility && (
          <dl className="kv" style={{ marginTop: 8 }}>
            {Object.entries(obj(s.model_compatibility))
              .slice(0, 6)
              .map(([k, v]) => (
                <InfoRow key={k} label={k} value={typeof v === "object" ? "…" : String(v)} />
              ))}
          </dl>
        )}
        <CommandResultLine state={cmd.state} />
      </Panel>

      <div>
        <div className="liq-sec-label">
          <span className="liq-sec-i">02</span> Runtime snapshot
          <span className="liq-sec-note">ten backend values + governor pools</span>
        </div>
        <div className="grid cols-2">
          <Panel title="Ten liquidity values (runtime snapshot)" tight>
            {stateQ.isPending ? (
              <Skeleton count={4} />
            ) : stateQ.isError ? (
              <ErrorState
                message={stateQ.error instanceof Error ? stateQ.error.message : "state endpoint failed"}
                onRetry={() => void stateQ.refetch()}
              />
            ) : featureNames.length === 0 ? (
              <EmptyState message="No feature values reported." hint={s?.reason ?? "governor snapshot empty — check enabled/available above"} />
            ) : (
              <DataTable headers={[{ label: "feature" }, { label: "value", num: true }]}>
                {featureNames.map((n) => (
                  <tr key={n}>
                    <td className="tiny inline-mono">{n}</td>
                    <td className="num tiny">{num(featureMap[n]) === null ? "—" : formatNumber(num(featureMap[n])!, 4)}</td>
                  </tr>
                ))}
              </DataTable>
            )}
            <div className="tiny faint" style={{ marginTop: 6 }}>
              schema {str(obj(s?.schema).id) ?? featuresQ.data?.schema_id ?? "—"} · dim{" "}
              {num(obj(s?.schema).dimension) ?? featuresQ.data?.dimension ?? "—"} · availability{" "}
              {featuresQ.data?.feature_availability ?? "—"}
            </div>
          </Panel>

          <Panel title="Liquidity pools (governor snapshot)" tight>
            {pools.length === 0 ? (
              stateQ.isPending ? (
                <Skeleton count={3} />
              ) : (
                <EmptyState message="No pools in the current snapshot (engine not running or features disabled)." />
              )
            ) : (
              <DataTable headers={[{ label: "side" }, { label: "source" }, { label: "state" }, { label: "price", num: true }]}>
                {pools.slice(0, 24).map((p, i) => (
                  <tr key={i}>
                    <td className="tiny">{str(p.side) ?? "—"}</td>
                    <td className="tiny">{str(p.source) ?? "—"}</td>
                    <td>
                      <StatusPill status={str(p.state)} />
                    </td>
                    <td className="num tiny">{formatPrice(num(p.price), 2)}</td>
                  </tr>
                ))}
              </DataTable>
            )}
          </Panel>
        </div>
      </div>

      <div>
        <div className="liq-sec-label">
          <span className="liq-sec-i">03</span> MSLIE sweep / liquidity-map view
          <span className="liq-sec-note">heat scaled by backend probability_as_target</span>
        </div>
        <Panel
          title="MSLIE sweep / liquidity-map view"
          right={
            <FreshnessCaption
              timestamp={null}
              source={mslieQ.data?.status ?? undefined}
              isFetching={mslieQ.isFetching}
              error={mslieQ.isError}
            />
          }
          tight
        >
          {mslieQ.isPending ? (
            <Skeleton count={3} />
          ) : mslieQ.isError ? (
            <ErrorState
              message={mslieQ.error instanceof Error ? mslieQ.error.message : "mslie endpoint failed"}
              onRetry={() => void mslieQ.refetch()}
            />
          ) : (
            <div className="grid cols-2">
              <div>
                <div className="section-title">liquidity map (heat list, ranked by target probability)</div>
                {zones.length === 0 ? (
                  <EmptyState message="No liquidity zones mapped yet (engine STANDBY until bars arrive)." />
                ) : (
                  <DataTable
                    headers={[
                      { label: "side" },
                      { label: "price", num: true },
                      { label: "tf" },
                      { label: "tests", num: true },
                      { label: "prob", num: true },
                      { label: "rank" },
                    ]}
                  >
                    {[...zones]
                      .sort((a, b) => (b.probability ?? 0) - (a.probability ?? 0))
                      .slice(0, 20)
                      .map((z, i) => (
                        <tr key={i}>
                          <td className="tiny">{z.side ?? "—"}</td>
                          <td className="num tiny">{formatPrice(z.price, 2)}</td>
                          <td className="tiny">{z.timeframe ?? "—"}</td>
                          <td className="num tiny">{z.tests ?? "—"}</td>
                          <td
                            className={`num tiny liq-heat${z.probability === null ? " dim" : ""}`}
                            style={{
                              background:
                                z.probability !== null
                                  ? `color-mix(in srgb, var(--accent) ${Math.min(60, Math.round((z.probability ?? 0) * 60))}%, transparent)`
                                  : undefined,
                            }}
                          >
                            <span className="liq-heat-v">{z.probability === null ? "—" : `${(z.probability * 100).toFixed(0)}%`}</span>
                            <span
                              className="liq-heat-bar"
                              role="img"
                              aria-label={z.probability === null ? "no probability" : `probability ${(z.probability * 100).toFixed(0)} percent`}
                            >
                              <i
                                style={{
                                  inlineSize: z.probability === null ? "0%" : `${(z.probability * 100).toFixed(1)}%`,
                                }}
                              />
                            </span>
                          </td>
                          <td className="tiny">{z.rank ?? "—"}</td>
                        </tr>
                      ))}
                  </DataTable>
                )}
              </div>
              <div>
                <div className="section-title">last sweep event</div>
                {mslieQ.data?.last_sweep ? (
                  <dl className="kv liq-sweep-dl">
                    <InfoRow label="direction" value={str(sweep.direction) ?? "—"} />
                    <InfoRow label="liquidity type" value={str(sweep.liquidity_type) ?? "—"} />
                    <InfoRow label="price / pool" value={`${formatPrice(num(sweep.price), 2)} / ${formatPrice(num(sweep.pool_price), 2)}`} />
                    <InfoRow label="confidence" value={num(sweep.confidence) === null ? "—" : `${formatNumber(num(sweep.confidence)!, 1)}%`} />
                    <InfoRow label="strength (ATR)" value={formatNumber(num(sweep.sweep_strength) ?? NaN, 2)} />
                    <InfoRow label="after-event state" value={<StatusPill status={str(sweep.after_event_state)} />} />
                    <InfoRow label="timestamp" value={formatDateTime(str(sweep.timestamp))} />
                  </dl>
                ) : (
                  <EmptyState message="No sweep recorded by the backend." hint="never inferred: absence of a sweep is a fact, not a gap" />
                )}
                <div className="section-title" style={{ marginTop: 10 }}>
                  market context
                </div>
                <JsonBlock value={mslieQ.data?.market_context ?? obj(mslieQ.data?.engine_status)} maxChars={1200} />
              </div>
            </div>
          )}
        </Panel>
      </div>

      <div>
        <div className="liq-sec-label">
          <span className="liq-sec-i">04</span> Model input
          <span className="liq-sec-note">MSLIE feature vector, read-only</span>
        </div>
        <Panel title="MSLIE feature vector (model input, read-only)" tight>
          {vectorQ.isPending ? (
            <Skeleton count={2} />
          ) : vectorQ.data?.available === false ? (
            <EmptyState message={vectorQ.data.reason ?? "no vector yet"} />
          ) : (
            <JsonBlock value={vectorQ.data?.vector} maxChars={4000} />
          )}
        </Panel>
      </div>

      {confirmTo !== null && (
        <ConfirmModal
          title={confirmTo ? "Enable Liquidity Intelligence" : "Disable Liquidity Intelligence"}
          danger={!confirmTo}
          confirmLabel={confirmTo ? "Enable" : "Disable"}
          busy={cmd.state.running}
          onCancel={() => setConfirmTo(null)}
          onConfirm={async () => {
            const target = confirmTo;
            setConfirmTo(null);
            await cmd.run(async () => {
              const res = await liquidityUseCases.toggle(target);
              const v = toggleVerdict(res);
              return { ok: v.ok, success: v.ok, message: v.message, status: v.ok ? 200 : 500 };
            });
            void qc.invalidateQueries({ queryKey: ["liquidity"] });
          }}
        >
          <div className="small">
            Sets <b>model.liquidity_features_enabled = {confirmTo ? "true" : "false"}</b> via the settings service
            (actor: web). Feature-contract dimension stays {num(obj(s?.schema).dimension) ?? "—"}; the model input only
            changes on the next governed snapshot.
          </div>
        </ConfirmModal>
      )}
    </div>
  );
}
