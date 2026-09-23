/**
 * Command Center — strategy fleet command view (legacy command_center_* parity).
 *
 * Sections: overview KPI strip · spatial 2.5D fleet map (Canvas2D port of
 * Web/command_center_spatial.js) · fleet grid (risk-first) · inspector drawer
 * (snapshot + ai attribution + debug intelligence + evidence completeness) ·
 * execution-safety card · decision timeline per strategy · time machine
 * (bounds + debounced lazy frame slider).
 */

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import type { ShellPageProps } from "@/app/featureModule";
import {
  DataTable,
  EmptyState,
  ErrorState,
  MetricCard,
  Panel,
  Segmented,
  Skeleton,
  StatusBadge,
} from "@/components/primitives";
import { formatDateTime, formatNumber, formatTime } from "@/lib/format";
import { useI18n } from "@/stores/i18nStore";
import { DistBars, Drawer, FreshnessCaption, GateStepper, InfoRow, JsonBlock, StatusPill, useDebounced, useNow } from "../../research/ui/lane5Kit";
import { arr, num, obj, str, stuckRows, type CcFleetRowDto } from "../model";
import { commandCenterQueries, commandCenterUseCases, useTimeMachineFrame } from "../useCases";
import { SpatialFleetCanvas } from "./SpatialFleetCanvas";

type View = "spatial" | "fleet" | "timemachine";

export default function CommandCenterPage(props: ShellPageProps) {
  void props;
  const t = useI18n((s) => s.t);
  const nowMs = useNow(5000);
  const [view, setView] = useState<View>("spatial");
  const [lifecycle, setLifecycle] = useState("");
  const [executionFilter, setExecutionFilter] = useState("");
  const [inspectId, setInspectId] = useState<string | null>(null);

  const overviewQ = useQuery({
    queryKey: ["command-center", "overview"],
    queryFn: ({ signal }) => commandCenterQueries.overview(signal),
    refetchInterval: 30_000,
    retry: false,
  });
  const fleetQ = useQuery({
    queryKey: ["command-center", "fleet", lifecycle, executionFilter],
    queryFn: ({ signal }) => commandCenterQueries.fleet(lifecycle || undefined, executionFilter || undefined, signal),
    refetchInterval: 60_000,
    retry: false,
  });

  const rows = commandCenterUseCases.fleetByRisk(fleetQ.data?.rows ?? [], nowMs);
  const overview = overviewQ.data?.available === true ? overviewQ.data : null;

  return (
    <div>
      <div className="page-head" style={{ display: "flex", alignItems: "baseline", gap: 10, flexWrap: "wrap" }}>
        <h2>{t("nav.feature.command-center", "Command Center")}</h2>
        <span className="muted small">
          {t("command-center.page.subtitle", "strategy fleet · spatial map · inspector · execution safety · time machine")}
        </span>
        <FreshnessCaption timestamp={null} source="research engine projection" isFetching={overviewQ.isFetching || fleetQ.isFetching} error={overviewQ.isError} />
      </div>

      {/* ---- overview KPI strip ---- */}
      <div className="grid cols-4">
        <MetricCard
          label={t("command-center.kpi.strategies", "strategies")}
          value={overviewQ.isPending ? "…" : String(overview?.total_strategies ?? "—")}
          tone="dim"
          sub={t("command-center.kpi.running_evaluations", "running evaluations {n}", {
            n: String(overview?.running_evaluations ?? 0),
          })}
        />
        <MetricCard
          label={t("command-center.kpi.execution_eligible", "execution eligible")}
          value={String(overview?.execution_eligible_count ?? "—")}
          tone={overview?.execution_eligible_count ? "pos" : "dim"}
          sub={t("command-center.kpi.eligibility_yes", "eligibility YES (domain authority)")}
        />
        <MetricCard
          label={t("command-center.kpi.blocked", "blocked")}
          value={String(overview?.blocked_count ?? "—")}
          tone={overview?.blocked_count ? "neg" : "dim"}
          sub={t("command-center.kpi.eligibility_blocked", "eligibility BLOCKED")}
        />
        <MetricCard
          label={t("command-center.kpi.terminal_states", "terminal states")}
          value={`${String(obj(overview?.terminal).REJECTED ?? 0)}R / ${String(obj(overview?.terminal).DEGRADED ?? 0)}D`}
          tone="dim"
          sub={t("command-center.kpi.retired_excluded", "retired excluded from pipeline census")}
        />
      </div>

      <div style={{ height: 12 }} />
      <div className="grid cols-2">
        <Panel title={t("command-center.panel.lifecycle_census", "Lifecycle census")} tight>
          <DistBars
            rows={Object.entries(obj(overview?.by_lifecycle)).map(([k, v]) => ({ label: k, count: num(v) ?? 0 }))}
            tone="var(--green)"
          />
        </Panel>
        <Panel title={t("command-center.panel.eval_pipeline", "Evaluation pipeline (counts across registry)")} tight>
          <DistBars
            rows={Object.entries(obj(overview?.evaluation_pipeline)).map(([k, v]) => ({ label: k, count: num(v) ?? 0 }))}
            tone="var(--amber)"
          />
        </Panel>
      </div>

      {stuckRows(overview ?? undefined).length > 0 && (
        <Panel title={t("command-center.panel.stuck", "Stuck strategies (hours in non-terminal state)")} tight>
          <DataTable
            headers={[
              { label: t("command-center.th.strategy", "strategy") },
              { label: t("command-center.th.state", "state") },
              { label: t("command-center.th.hours", "hours"), num: true },
              { label: "" },
            ]}
          >
            {stuckRows(overview ?? undefined).map((s) => (
              <tr key={s.strategy_id}>
                <td className="inline-mono tiny">{s.strategy_id.slice(0, 16)}</td>
                <td>
                  <StatusPill status={s.state} />
                </td>
                <td className="num tiny">{s.hours === null ? "—" : formatNumber(s.hours, 1)}</td>
                <td>
                  <button className="btn small ghost" onClick={() => setInspectId(s.strategy_id)}>
                    {t("command-center.action.inspect", "inspect")}
                  </button>
                </td>
              </tr>
            ))}
          </DataTable>
        </Panel>
      )}

      <div style={{ marginBlock: 12 }}>
        <Segmented
          options={[
            { id: "spatial" as const, label: t("command-center.view.spatial", "Spatial 2.5D map") },
            { id: "fleet" as const, label: t("command-center.view.fleet", "Fleet grid") },
            { id: "timemachine" as const, label: t("command-center.panel.time_machine", "Time machine") },
          ]}
          value={view}
          onChange={setView}
        />
      </div>

      {view === "spatial" && (
        <Panel title={t("command-center.panel.spatial", "Spatial fleet map — lifecycle strata (Canvas2D 2.5D)")} tight>
          <div style={{ padding: 10 }}>
            <SpatialFleetCanvas
              selectedId={inspectId}
              onSelect={(id) => setInspectId(id)}
              onInspect={(id) => setInspectId(id)}
            />
          </div>
        </Panel>
      )}

      {view === "fleet" && (
        <Panel
          title={t("command-center.panel.fleet", "Fleet ({rows} rows, risk-first order)", { rows: String(fleetQ.data?.count ?? 0) })}
          right={
            <div style={{ display: "flex", gap: 6 }}>
              <select className="select" style={{ width: 150 }} value={lifecycle} onChange={(e) => setLifecycle(e.target.value)}>
                <option value="">{t("command-center.filter.lifecycle_any", "lifecycle: any")}</option>
                {["DISCOVERED", "VALIDATED", "SHADOW", "ACTIVE", "REJECTED", "DEGRADED", "RETIRED"].map((l) => (
                  <option key={l} value={l}>
                    {l}
                  </option>
                ))}
              </select>
              <select className="select" style={{ width: 140 }} value={executionFilter} onChange={(e) => setExecutionFilter(e.target.value)}>
                <option value="">{t("command-center.filter.eligibility_any", "eligibility: any")}</option>
                {["YES", "BLOCKED", "CONDITIONAL", "UNKNOWN"].map((l) => (
                  <option key={l} value={l}>
                    {l}
                  </option>
                ))}
              </select>
            </div>
          }
          tight
        >
          {fleetQ.isPending ? (
            <Skeleton count={6} />
          ) : fleetQ.isError ? (
            <ErrorState message={fleetQ.error instanceof Error ? fleetQ.error.message : t("command-center.err.fleet", "fleet failed")} onRetry={() => void fleetQ.refetch()} />
          ) : fleetQ.data?.available === false ? (
            <EmptyState
              message={t("command-center.empty.research_unavailable", "Research engine unavailable")}
              hint={fleetQ.data.reason ?? "RESEARCH_ENGINE_UNAVAILABLE"}
            />
          ) : rows.length === 0 ? (
            <EmptyState message={t("command-center.empty.no_strategies", "No strategies match the filters.")} />
          ) : (
            <div className="table-wrap" style={{ maxHeight: 560 }}>
              <table className="data-table">
                <thead>
                  <tr>
                    <th>{t("command-center.th.strategy", "strategy")}</th>
                    <th>{t("command-center.th.lifecycle", "lifecycle")}</th>
                    <th className="num">{t("command-center.th.conf", "conf")}</th>
                    <th className="num">{t("command-center.th.samples", "samples")}</th>
                    <th className="num">{t("command-center.th.health", "health")}</th>
                    <th>{t("command-center.th.eligibility", "eligibility")}</th>
                    <th>{t("command-center.th.reason", "reason")}</th>
                    <th>{t("command-center.th.updated", "updated")}</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r: CcFleetRowDto, i) => (
                    <tr key={`${r.strategy_id}-${i}`}>
                      <td className="inline-mono tiny" title={r.strategy_id}>
                        {(r.strategy_id ?? "—").slice(0, 14)}
                      </td>
                      <td>
                        <StatusPill status={r.lifecycle} />
                      </td>
                      <td className="num tiny">{r.confidence === null || r.confidence === undefined ? "—" : formatNumber(r.confidence, 3)}</td>
                      <td className="num tiny">{r.sample_count ?? "—"}</td>
                      <td className="num tiny">{r.health_final === null || r.health_final === undefined ? "—" : formatNumber(r.health_final, 1)}</td>
                      <td>
                        <StatusBadge status={r.eligibility_state} />
                      </td>
                      <td className="tiny muted" title={r.eligibility_reason ?? ""}>
                        {(r.eligibility_reason ?? "—").slice(0, 32)}
                      </td>
                      <td className="tiny">{r.updated_at ? formatDateTime(r.updated_at) : "—"}</td>
                      <td>
                        <button className="btn small ghost" onClick={() => setInspectId(str(r.strategy_id) ?? "")}>
                          {t("command-center.action.inspector", "inspector")}
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Panel>
      )}

      {view === "timemachine" && <TimeMachine />}

      {inspectId && <InspectorDrawer strategyId={inspectId} onClose={() => setInspectId(null)} />}
    </div>
  );
}

/* -------------------------------------------------------------------- */
/* Time machine: bounds + frame slider (lazy, debounced)                 */
/* -------------------------------------------------------------------- */

function TimeMachine() {
  const t = useI18n((s) => s.t);
  const boundsQ = useQuery({
    queryKey: ["command-center", "tm-bounds"],
    queryFn: ({ signal }) => commandCenterQueries.tmBounds(signal),
    retry: false,
  });
  const bounds = boundsQ.data?.available === true ? boundsQ.data : null;
  const range = useMemo(() => {
    const lo = bounds?.earliest ? new Date(bounds.earliest).getTime() : NaN;
    const hi = bounds?.latest ? new Date(bounds.latest).getTime() : NaN;
    return Number.isNaN(lo) || Number.isNaN(hi) || hi <= lo ? null : { lo, hi };
  }, [bounds]);
  const [sliderMs, setSliderMs] = useState<number | null>(null);
  const effectiveMs = sliderMs ?? range?.hi ?? null;
  const debouncedIso = useDebounced(effectiveMs === null ? null : new Date(effectiveMs).toISOString(), 350);
  const frameQ = useTimeMachineFrame(debouncedIso);

  if (boundsQ.isPending)
    return (
      <Panel title={t("command-center.panel.time_machine", "Time machine")}>
        <Skeleton count={3} />
      </Panel>
    );
  if (!bounds || !range) {
    return (
      <Panel title={t("command-center.panel.time_machine", "Time machine")} tight>
        <EmptyState
          message={t("command-center.empty.no_history", "No historical events yet")}
          hint={boundsQ.data?.reason ?? t("command-center.empty.no_history_hint", "timemachine/bounds answered available:false — nothing to scrub")}
        />
      </Panel>
    );
  }

  const frame = frameQ.data;
  const nodes = arr(frame?.nodes);
  const byZone = new Map<string, number>();
  for (const n of nodes) byZone.set(str(n.zone) ?? "UNKNOWN", (byZone.get(str(n.zone) ?? "UNKNOWN") ?? 0) + 1);
  const transitions = arr(frame?.transitions);

  return (
    <Panel
      title={t("command-center.panel.time_machine_title", "Time machine — fleet state at an instant")}
      right={
        <span className="tiny muted">
          {formatDateTime(debouncedIso)} ·{" "}
          {t("command-center.tm.events_in_range", "{n} events in range", { n: String(bounds.total_events ?? 0) })}
        </span>
      }
      tight
    >
      <div style={{ display: "grid", gap: 8 }}>
        <input
          type="range"
          min={range.lo}
          max={range.hi}
          step={60_000}
          value={effectiveMs ?? range.hi}
          onChange={(e) => setSliderMs(Number(e.target.value))}
          style={{ width: "100%", accentColor: "var(--accent)" }}
          aria-label={t("command-center.a11y.timeline_scrubber", "timeline scrubber")}
        />
        <div style={{ display: "flex", justifyContent: "space-between" }} className="tiny faint">
          <span>{formatDateTime(bounds.earliest)}</span>
          <span>{formatTime(debouncedIso ?? bounds.latest)}</span>
          <span>{formatDateTime(bounds.latest)}</span>
        </div>
        {frameQ.isFetching && <div className="tiny muted">{t("command-center.tm.frame_loading", "frame loading (lazy, debounced 350 ms)…")}</div>}
        {frame?.available === false ? (
          <EmptyState message={frame.reason ?? t("command-center.tm.frame_unavailable", "frame not available")} />
        ) : (
          <div className="grid cols-2" style={{ marginTop: 6 }}>
            <div>
              <div className="section-title">{t("command-center.tm.zone_census", "zone census at instant")}</div>
              <DistBars rows={[...byZone.entries()].map(([label, count]) => ({ label, count }))} />
            </div>
            <div>
              <div className="section-title">{t("command-center.tm.transitions", "transitions in this frame (±60 s)")}</div>
              {transitions.length === 0 ? (
                <EmptyState message={t("command-center.empty.no_transitions", "No lifecycle transition happened at this instant.")} />
              ) : (
                <DataTable
                  headers={[
                    { label: t("command-center.th.strategy", "strategy") },
                    { label: "→" },
                    { label: t("command-center.th.actor", "actor") },
                    { label: t("command-center.th.reason", "reason") },
                  ]}
                >
                  {transitions.slice(0, 12).map((t, i) => (
                    <tr key={i}>
                      <td className="inline-mono tiny">{str(t.strategy_id)?.slice(0, 12) ?? "—"}</td>
                      <td>
                        <StatusPill status={str(t.to_state)} />
                      </td>
                      <td className="tiny">{str(t.actor) ?? "—"}</td>
                      <td className="tiny muted">{str(t.reason) ?? ""}</td>
                    </tr>
                  ))}
                </DataTable>
              )}
            </div>
          </div>
        )}
      </div>
    </Panel>
  );
}

/* -------------------------------------------------------------------- */
/* Inspector drawer: snapshot + safety + timeline + debug intelligence   */
/* -------------------------------------------------------------------- */

function InspectorDrawer({ strategyId, onClose }: { strategyId: string; onClose: () => void }) {
  const t = useI18n((s) => s.t);
  const inspectorQ = useQuery({
    queryKey: ["command-center", "inspector", strategyId],
    queryFn: ({ signal }) => commandCenterQueries.inspector(strategyId, signal),
    retry: false,
  });
  const safetyQ = useQuery({
    queryKey: ["command-center", "safety", strategyId],
    queryFn: ({ signal }) => commandCenterQueries.safety(strategyId, signal),
    retry: false,
  });
  const timelineQ = useQuery({
    queryKey: ["command-center", "timeline", strategyId],
    queryFn: ({ signal }) => commandCenterQueries.timeline(strategyId, signal),
    retry: false,
  });

  const snap = inspectorQ.data;
  const debug = obj(snap?.debug_intelligence);
  const attribution = obj(snap?.ai_attribution);
  const completeness = obj(snap?.evidence_completeness);
  const ee = obj(safetyQ.data);
  const events = arr(timelineQ.data?.events);
  const gates = obj(obj(snap?.evaluation).gates);

  return (
    <Drawer title={t("command-center.inspector.title", "Inspector — {id}", { id: strategyId })} onClose={onClose}>
      {inspectorQ.isPending ? (
        <Skeleton count={5} />
      ) : inspectorQ.data?.available === false ? (
        <EmptyState
          message={str(inspectorQ.data.error) ?? t("command-center.empty.strategy_not_found", "strategy not found")}
          hint={t("command-center.empty.strategy_not_found_hint", "inspector answers STRATEGY_NOT_FOUND for unknown ids")}
        />
      ) : (
        <div style={{ display: "grid", gap: 12 }}>
          <Panel title={t("command-center.panel.execution_safety", "Execution safety (CAN-THIS-TRADE)")} accent tight>
            {safetyQ.isPending ? (
              <Skeleton />
            ) : safetyQ.isError ? (
              <ErrorState message={t("command-center.err.safety", "execution-safety endpoint failed")} onRetry={() => void safetyQ.refetch()} />
            ) : (
              <div className="decision-card">
                <div>
                  <div className={`big ${ee.can_trade === true ? "buy" : "sell"}`}>
                    {ee.can_trade === true
                      ? t("command-center.decision.yes", "YES")
                      : ee.can_trade === false
                        ? t("command-center.decision.no", "NO")
                        : "—"}
                  </div>
                  <div className="why-detail tiny">{str(ee.lifecycle) ?? "—"}</div>
                </div>
                <div style={{ flex: 1 }}>
                  <dl className="kv">
                    <InfoRow label="eligibility_state" value={<StatusBadge status={str(ee.eligibility_state)} />} />
                    <InfoRow label={t("command-center.label.reason", "reason")} value={str(ee.reason) ?? "—"} />
                    <InfoRow label="required_gate" value={str(ee.required_gate) ?? "—"} />
                    <InfoRow
                      label={t("command-center.label.blockers", "blockers")}
                      value={
                        arr(ee.blockers).length === 0
                          ? t("command-center.label.none", "none")
                          : t("command-center.label.listed", "{n} listed", { n: String(arr(ee.blockers).length) })
                      }
                    />
                  </dl>
                  {arr(ee.blockers).map((b, i) => (
                    <div key={i} className="tiny muted">
                      • {typeof b === "string" ? b : JSON.stringify(b)}
                    </div>
                  ))}
                </div>
              </div>
            )}
          </Panel>

          <Panel title={t("command-center.panel.evaluation", "Evaluation (transient telemetry — not lifecycle)")} tight>
            {Object.keys(gates).length === 0 ? (
              <EmptyState message={t("command-center.empty.no_evaluation", "No evaluation running / recorded.")} />
            ) : (
              <GateStepper
                gates={Object.entries(gates).map(([k, v]) => ({ name: k, status: str(v) ?? "UNKNOWN" }))}
              />
            )}
          </Panel>

          <div className="grid cols-2">
            <Panel title={t("command-center.panel.debug_intel", "Debug intelligence (backend-computed)")} tight>
              <dl className="kv">
                <InfoRow label={t("command-center.label.anomaly_score", "anomaly score")} value={formatNumber(num(debug.anomaly_score) ?? NaN, 3)} />
                <InfoRow label={t("command-center.label.validation_consistency", "validation consistency")} value={formatNumber(num(debug.validation_consistency) ?? NaN, 3)} />
                <InfoRow label={t("command-center.label.debug_priority", "debug priority")} value={formatNumber(num(debug.debug_priority) ?? NaN, 3)} />
              </dl>
              {arr(debug.hints).length > 0 && (
                <ul className="tiny muted" style={{ margin: "6px 0 0", paddingInlineStart: 16 }}>
                  {arr(debug.hints).map((h, i) => (
                    <li key={i}>{typeof h === "string" ? h : JSON.stringify(h)}</li>
                  ))}
                </ul>
              )}
            </Panel>
            <Panel title={t("command-center.panel.evidence", "Evidence completeness")} tight>
              {Object.keys(completeness).length === 0 ? (
                <EmptyState message={t("command-center.empty.completeness_missing", "completeness not reported")} />
              ) : (
                <dl className="kv">
                  {Object.entries(completeness).slice(0, 10).map(([k, v]) => (
                    <InfoRow key={k} label={k} value={typeof v === "object" ? "…" : String(v)} />
                  ))}
                </dl>
              )}
            </Panel>
          </div>

          {Object.keys(attribution).length > 0 && (
            <Panel title={t("command-center.panel.ai_attribution", "AI attribution (explainability)")} tight>
              <JsonBlock value={attribution} maxChars={2500} />
            </Panel>
          )}

          <Panel title={t("command-center.panel.decision_timeline", "Decision timeline ({n})", { n: String(events.length) })} tight>
            {events.length === 0 ? (
              timelineQ.isPending ? <Skeleton /> : <EmptyState message={t("command-center.empty.no_timeline", "No timeline events.")} />
            ) : (
              <DataTable
                headers={[
                  { label: t("command-center.th.at", "at") },
                  { label: t("command-center.th.event", "event") },
                  { label: t("command-center.th.from", "from") },
                  { label: t("command-center.th.to", "to") },
                  { label: t("command-center.th.actor", "actor") },
                ]}
              >
                {events.slice(0, 60).map((e, i) => (
                  <tr key={i}>
                    <td className="tiny">{formatDateTime(str(e.timestamp) ?? str(e.at))}</td>
                    <td className="tiny">{str(e.event_type) ?? str(e.event) ?? "—"}</td>
                    <td className="tiny">{str(e.from_state) ?? str(e.from) ?? ""}</td>
                    <td className="tiny">{str(e.to_state) ?? str(e.to) ?? ""}</td>
                    <td className="tiny muted">{str(e.actor) ?? "—"}</td>
                  </tr>
                ))}
              </DataTable>
            )}
          </Panel>

          <Panel title={t("command-center.panel.invariant", "Invariant check")} tight>
            <JsonBlock value={snap?.invariant_check} maxChars={1500} />
          </Panel>
        </div>
      )}
    </Drawer>
  );
}
