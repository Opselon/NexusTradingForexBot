/**
 * Liquidity — Liquidity Intelligence + MSLIE market-structure perception
 * (legacy tab-liquidity parity).
 *
 * Sections: state cards (enabled/available/calculation vs source split —
 * BUG-110 contract) · ten-value feature table · sweep visualization-lite
 * (liquidity map heat list + last sweep card) · confirm-guarded toggle.
 * MSLIE vector exposed read-only.
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
import { useI18n } from "@/stores/i18nStore";
import { formatDateTime, formatNumber, formatPrice } from "@/lib/format";
import { CommandResultLine, FreshnessCaption, InfoRow, JsonBlock, StatusPill } from "../../research/ui/lane5Kit";
import { arr, bool, num, obj, str, toZoneRow, toggleVerdict } from "../model";
import { liquidityQueries, liquidityUseCases } from "../useCases";

export default function LiquidityPage(props: ShellPageProps) {
  void props;
  const t = useI18n((s) => s.t);
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
    <div>
      <div className="page-head" style={{ display: "flex", alignItems: "baseline", gap: 10, flexWrap: "wrap" }}>
        <h2>{t("nav.feature.liquidity", "Liquidity")}</h2>
        <span className="muted small">{t("liquidity.page.subtitle", "liquidity intelligence governor · MSLIE market structure")}</span>
        <FreshnessCaption
          timestamp={s?.last_update ?? undefined}
          source={t("liquidity.meta.rev", "rev {n}", { n: String(s?.state_revision ?? "—") })}
          isFetching={stateQ.isFetching}
          error={stateQ.isError}
        />
      </div>

      <div className="grid cols-4">
        <MetricCard
          label={t("liquidity.card.governor", "Governor (backend)")}
          value={<StatusBadge status={s ? (enabled ? "ACTIVE" : "DISABLED") : "UNKNOWN"} />}
          sub={t("liquidity.card.governor_sub", "source {src} · algo {algo}", { src: s?.source ?? "—", algo: s?.algorithm_version ?? "—" })}
          tone={enabled ? "pos" : "dim"}
        />
        <MetricCard
          label={t("liquidity.card.calc_status", "calculation status")}
          value={<StatusPill status={s?.calculation_status ?? "NOT_RUN"} />}
          sub={t("liquidity.card.calc_sub", "did the last compute attempt succeed")}
        />
        <MetricCard
          label={t("liquidity.card.source_status", "source status")}
          value={<StatusPill status={s?.source_status ?? s?.source ?? "UNKNOWN"} />}
          sub={t("liquidity.card.source_sub", "where the input data came from (orthogonal per BUG-110)")}
        />
        <MetricCard
          label={t("liquidity.card.latency", "latency / age")}
          value={`${s?.latency_ms === null || s?.latency_ms === undefined ? "—" : formatNumber(s.latency_ms, 2)} ms`}
          sub={t("liquidity.card.latency_sub", "causal {c} · age {a}s", {
            c: s?.causal_state ?? "—",
            a: s?.age_sec === null || s?.age_sec === undefined ? "—" : formatNumber(s.age_sec, 1),
          })}
        />
      </div>

      <Panel
        title={t("liquidity.panel.toggle", "Liquidity Intelligence toggle")}
        accent
        right={
          <button className={`btn small ${enabled ? "danger" : "primary"}`} disabled={stateQ.isPending || cmd.state.running} onClick={() => setConfirmTo(!enabled)}>
            {enabled ? t("common.disable", "disable") : t("common.enable", "enable")}
          </button>
        }
        tight
      >
        <div className="small muted">
          {t(
            "liquidity.panel.toggle_help",
            "Toggle persists via SettingsService (HOT_RESTRICTED) and hot-applies the governor — it never restarts the engine and never touches orders/risk/execution. The backend returns the new authoritative state; the UI re-reads it.",
          )}
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

      <div style={{ height: 12 }} />
      <div className="grid cols-2">
        <Panel title={t("liquidity.panel.values", "Ten liquidity values (runtime snapshot)")} tight>
          {stateQ.isPending ? (
            <Skeleton count={4} />
          ) : stateQ.isError ? (
            <ErrorState
              message={stateQ.error instanceof Error ? stateQ.error.message : t("liquidity.err.state", "state endpoint failed")}
              onRetry={() => void stateQ.refetch()}
            />
          ) : featureNames.length === 0 ? (
            <EmptyState
              message={t("liquidity.empty.features", "No feature values reported.")}
              hint={s?.reason ?? t("liquidity.empty.features_hint", "governor snapshot empty — check enabled/available above")}
            />
          ) : (
            <DataTable headers={[{ label: t("liquidity.th.feature", "feature") }, { label: t("liquidity.th.value", "value"), num: true }]}>
              {featureNames.map((n) => (
                <tr key={n}>
                  <td className="tiny inline-mono">{n}</td>
                  <td className="num tiny">{num(featureMap[n]) === null ? "—" : formatNumber(num(featureMap[n])!, 4)}</td>
                </tr>
              ))}
            </DataTable>
          )}
          <div className="tiny faint" style={{ marginTop: 6 }}>
            {t("liquidity.meta.schema", "schema {id} · dim {dim} · availability {av}", {
              id: str(obj(s?.schema).id) ?? featuresQ.data?.schema_id ?? "—",
              dim: num(obj(s?.schema).dimension) ?? featuresQ.data?.dimension ?? "—",
              av: featuresQ.data?.feature_availability ?? "—",
            })}
          </div>
        </Panel>

        <Panel title={t("liquidity.panel.pools", "Liquidity pools (governor snapshot)")} tight>
          {pools.length === 0 ? (
            stateQ.isPending ? (
              <Skeleton count={3} />
            ) : (
              <EmptyState message={t("liquidity.empty.pools", "No pools in the current snapshot (engine not running or features disabled).")} />
            )
          ) : (
            <DataTable
              headers={[
                { label: t("liquidity.th.side", "side") },
                { label: t("liquidity.th.source", "source") },
                { label: t("liquidity.th.state", "state") },
                { label: t("liquidity.th.price", "price"), num: true },
              ]}
            >
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

      <div style={{ height: 12 }} />
      <Panel
        title={t("liquidity.panel.mslie", "MSLIE sweep / liquidity-map view")}
        right={<FreshnessCaption timestamp={null} source={mslieQ.data?.status ?? undefined} isFetching={mslieQ.isFetching} error={mslieQ.isError} />}
        tight
      >
        {mslieQ.isPending ? (
          <Skeleton count={3} />
        ) : mslieQ.isError ? (
          <ErrorState
            message={mslieQ.error instanceof Error ? mslieQ.error.message : t("liquidity.err.mslie", "mslie endpoint failed")}
            onRetry={() => void mslieQ.refetch()}
          />
        ) : (
          <div className="grid cols-2">
            <div>
              <div className="section-title">{t("liquidity.section.map", "liquidity map (heat list, ranked by target probability)")}</div>
              {zones.length === 0 ? (
                <EmptyState message={t("liquidity.empty.zones", "No liquidity zones mapped yet (engine STANDBY until bars arrive).")} />
              ) : (
                <DataTable
                  headers={[
                    { label: t("liquidity.th.side", "side") },
                    { label: t("liquidity.th.price", "price"), num: true },
                    { label: t("liquidity.th.tf", "tf") },
                    { label: t("liquidity.th.tests", "tests"), num: true },
                    { label: t("liquidity.th.prob", "prob"), num: true },
                    { label: t("liquidity.th.rank", "rank") },
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
                          className="num tiny"
                          style={{
                            background:
                              z.probability !== null
                                ? `color-mix(in srgb, var(--accent) ${Math.min(60, Math.round((z.probability ?? 0) * 60))}%, transparent)`
                                : undefined,
                          }}
                        >
                          {z.probability === null ? "—" : `${(z.probability * 100).toFixed(0)}%`}
                        </td>
                        <td className="tiny">{z.rank ?? "—"}</td>
                      </tr>
                    ))}
                </DataTable>
              )}
            </div>
            <div>
              <div className="section-title">{t("liquidity.section.last_sweep", "last sweep event")}</div>
              {mslieQ.data?.last_sweep ? (
                <dl className="kv">
                  <InfoRow label={t("liquidity.kv.direction", "direction")} value={str(sweep.direction) ?? "—"} />
                  <InfoRow label={t("liquidity.kv.type", "liquidity type")} value={str(sweep.liquidity_type) ?? "—"} />
                  <InfoRow
                    label={t("liquidity.kv.price_pool", "price / pool")}
                    value={`${formatPrice(num(sweep.price), 2)} / ${formatPrice(num(sweep.pool_price), 2)}`}
                  />
                  <InfoRow
                    label={t("ux.signal.confidence", "confidence")}
                    value={num(sweep.confidence) === null ? "—" : `${formatNumber(num(sweep.confidence)!, 1)}%`}
                  />
                  <InfoRow label={t("liquidity.kv.strength", "strength (ATR)")} value={formatNumber(num(sweep.sweep_strength) ?? NaN, 2)} />
                  <InfoRow label={t("liquidity.kv.after_state", "after-event state")} value={<StatusPill status={str(sweep.after_event_state)} />} />
                  <InfoRow label={t("liquidity.kv.timestamp", "timestamp")} value={formatDateTime(str(sweep.timestamp))} />
                </dl>
              ) : (
                <EmptyState
                  message={t("liquidity.empty.sweep", "No sweep recorded by the backend.")}
                  hint={t("liquidity.empty.sweep_hint", "never inferred: absence of a sweep is a fact, not a gap")}
                />
              )}
              <div className="section-title" style={{ marginTop: 10 }}>
                {t("liquidity.section.market_context", "market context")}
              </div>
              <JsonBlock value={mslieQ.data?.market_context ?? obj(mslieQ.data?.engine_status)} maxChars={1200} />
            </div>
          </div>
        )}
      </Panel>

      <div style={{ height: 12 }} />
      <Panel title={t("liquidity.panel.vector", "MSLIE feature vector (model input, read-only)")} tight>
        {vectorQ.isPending ? (
          <Skeleton count={2} />
        ) : vectorQ.data?.available === false ? (
          <EmptyState message={vectorQ.data.reason ?? t("liquidity.empty.vector", "no vector yet")} />
        ) : (
          <JsonBlock value={vectorQ.data?.vector} maxChars={4000} />
        )}
      </Panel>

      {confirmTo !== null && (
        <ConfirmModal
          title={confirmTo ? t("liquidity.confirm.enable_title", "Enable Liquidity Intelligence") : t("liquidity.confirm.disable_title", "Disable Liquidity Intelligence")}
          danger={!confirmTo}
          confirmLabel={confirmTo ? t("common.enable", "Enable") : t("common.disable", "Disable")}
          busy={cmd.state.running}
          onCancel={() => setConfirmTo(null)}
          onConfirm={async () => {
            const target = confirmTo;
            setConfirmTo(null);
            await cmd.run(async () => {
              const res = await liquidityUseCases.toggle(target);
              const v = toggleVerdict(res);
              if (!v.ok) return { ok: false, success: false, message: v.message === "Backend refused the toggle." ? t("liquidity.feedback.refused", "Backend refused the toggle.") : v.message, status: 500 };
              const state = res.enabled ? t("liquidity.feedback.enabled", "ENABLED") : t("liquidity.feedback.disabled", "DISABLED");
              const ver = res.algorithm_version ?? t("liquidity.feedback.version_unknown", "version not reported");
              const message = t("liquidity.feedback.toggled", "Liquidity Intelligence now {state} (source: {src}, {ver})", {
                state,
                src: res.source ?? "—",
                ver,
              });
              return { ok: true, success: true, message, status: 200 };
            });
            void qc.invalidateQueries({ queryKey: ["liquidity"] });
          }}
        >
          <div className="small">
            {t(
              "liquidity.confirm.body",
              "Sets {code} via the settings service (actor: web). Feature-contract dimension stays {dim}; the model input only changes on the next governed snapshot.",
              {
                code: `model.liquidity_features_enabled = ${confirmTo ? "true" : "false"}`,
                dim: num(obj(s?.schema).dimension) ?? "—",
              },
            )}
          </div>
        </ConfirmModal>
      )}
    </div>
  );
}
