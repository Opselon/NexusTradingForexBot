/**
 * Factory — autonomous strategy evolution control room (legacy tab-factory).
 *
 * Sections: loop status + provider health · generate/complete/evaluate ·
 * generations table · candidates table (per generation) · benchmarks ·
 * failures · ranking (dimension) · evolution memory · LLM config (safe
 * status only — the raw API key never round-trips) · console events.
 * All backend /api/factory/* routes exist; when the factory is not mounted
 * the routes answer {available:false, reason:"FACTORY_UNAVAILABLE"} and the
 * UI renders that verbatim per section (never a fabricated board).
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
  Segmented,
  Skeleton,
  StatusBadge,
} from "@/components/primitives";
import { useMutationFeedback } from "@/hooks/useMutationFeedback";
import { formatDateTime, formatNumber } from "@/lib/format";
import { useI18n } from "@/stores/i18nStore";
import { CommandResultLine, FreshnessCaption, InfoRow, JsonBlock, StatusPill } from "../../research/ui/lane5Kit";
import { arr, bool, num, obj, str, type FactoryCommandDto, type Row } from "../model";
import { factoryQueries, factoryUseCases } from "../useCases";

type Tab = "generations" | "candidates" | "benchmarks" | "failures" | "ranking" | "memory" | "console";

const RANK_DIMS = ["OVERALL", "SHARPE", "EXPECTANCY", "STABILITY"];

export default function FactoryPage(props: ShellPageProps) {
  void props;
  const [tab, setTab] = useState<Tab>("generations");
  const [genFilter, setGenFilter] = useState("");
  const [dim, setDim] = useState("OVERALL");
  const [size, setSize] = useState("20");
  const [pending, setPending] = useState<{ label: string; danger: boolean; run: () => Promise<FactoryCommandDto> } | null>(null);
  const t = useI18n((s) => s.t);
  const cmd = useMutationFeedback();
  const qc = useQueryClient();

  const statusQ = useQuery({
    queryKey: ["factory", "status"],
    queryFn: ({ signal }) => factoryQueries.status(signal),
    refetchInterval: 20_000,
    retry: false,
  });
  const generationsQ = useQuery({
    queryKey: ["factory", "generations"],
    queryFn: ({ signal }) => factoryQueries.generations(signal),
    retry: false,
    enabled: tab === "generations",
  });
  const candidatesQ = useQuery({
    queryKey: ["factory", "candidates", genFilter],
    queryFn: ({ signal }) => factoryQueries.candidates(genFilter || undefined, signal),
    retry: false,
    enabled: tab === "candidates",
  });
  const benchmarksQ = useQuery({
    queryKey: ["factory", "benchmarks", genFilter],
    queryFn: ({ signal }) => factoryQueries.benchmarks(genFilter || undefined, signal),
    retry: false,
    enabled: tab === "benchmarks",
  });
  const failuresQ = useQuery({
    queryKey: ["factory", "failures"],
    queryFn: ({ signal }) => factoryQueries.failures(signal),
    retry: false,
    enabled: tab === "failures",
  });
  const rankingQ = useQuery({
    queryKey: ["factory", "ranking", dim],
    queryFn: ({ signal }) => factoryQueries.ranking(dim, signal),
    retry: false,
    enabled: tab === "ranking",
  });
  const memoryQ = useQuery({
    queryKey: ["factory", "memory"],
    queryFn: ({ signal }) => factoryQueries.memory(signal),
    retry: false,
    enabled: tab === "memory",
  });
  const eventsQ = useQuery({
    queryKey: ["factory", "events", genFilter],
    queryFn: ({ signal }) => factoryQueries.events(genFilter || undefined, signal),
    retry: false,
    enabled: tab === "console",
  });
  const llmQ = useQuery({
    queryKey: ["factory", "llm-config"],
    queryFn: ({ signal }) => factoryQueries.llmConfig(signal),
    retry: false,
  });

  const mounted = statusQ.data?.available === true;
  const loop = obj(statusQ.data?.loop);
  const provider = obj(statusQ.data?.provider);
  const usage = obj(provider.usage);
  const gens = factoryUseCases.generationList(arr(generationsQ.data?.generations));
  const dimLabels: Record<string, string> = {
    OVERALL: t("factory.dim.overall", "OVERALL"),
    SHARPE: t("factory.dim.sharpe", "SHARPE"),
    EXPECTANCY: t("factory.dim.expectancy", "EXPECTANCY"),
    STABILITY: t("factory.dim.stability", "STABILITY"),
  };

  const runCmd = async (label: string, fn: () => Promise<FactoryCommandDto>) => {
    await cmd.run(async () => {
      const v = factoryUseCases.verdict(await fn());
      return { ok: v.ok, success: v.ok, message: `${label}: ${v.message}`, status: v.ok ? 200 : 500 };
    });
    void qc.invalidateQueries({ queryKey: ["factory"] });
  };

  const ask = (label: string, danger: boolean, run: () => Promise<FactoryCommandDto>) => setPending({ label, danger, run });

  return (
    <div>
      <div className="page-head" style={{ display: "flex", alignItems: "baseline", gap: 10, flexWrap: "wrap" }}>
        <h2>{t("nav.feature.factory", "Strategy Factory")}</h2>
        <span className="muted small">{t("factory.subtitle", "autonomous evolution control room — never touches the live path")}</span>
        <FreshnessCaption
          timestamp={null}
          source={t("factory.fresh_source", "factory store")}
          isFetching={statusQ.isFetching}
          error={statusQ.isError}
        />
      </div>

      {!mounted && !statusQ.isPending && (
        <div className="banner stale" role="status">
          {t("factory.banner_1", "Strategy Factory not mounted — {reason}.", {
            reason: statusQ.data?.reason ?? t("factory.banner_no_reason", "backend answered available:false"),
          })}{" "}
          {t("factory.banner_2", "Every section below shows the backend's own unavailability verdict; no data is simulated.")}
        </div>
      )}

      <div className="grid cols-4">
        <MetricCard
          label={t("factory.metric_loop", "loop state")}
          value={<StatusBadge status={str(loop.state) ?? "UNKNOWN"} />}
          sub={t("factory.metric_loop_sub", "generation {g}", { g: str(loop.current_generation) ?? "—" })}
          tone={bool(loop.kill_requested) ? "neg" : "dim"}
        />
        <MetricCard
          label={t("factory.metric_provider", "LLM provider")}
          value={<StatusBadge status={bool(provider.available) ? "READY" : "UNAVAILABLE"} />}
          sub={t("factory.metric_provider_sub", "{model} · {key}", {
            model: str(provider.model) ?? t("factory.no_model", "no model"),
            key: bool(obj(llmQ.data?.status).api_key_present) ? t("factory.key_set", "key set") : t("factory.key_missing", "key missing"),
          })}
        />
        <MetricCard
          label={t("factory.metric_usage", "provider usage")}
          value={formatNumber(num(usage.requests) ?? 0, 0)}
          sub={t("factory.metric_usage_sub", "errors {e} · prompt {v}", {
            e: String(num(usage.errors) ?? 0),
            v: str(provider.prompt_version) ?? "—",
          })}
          tone="dim"
        />
        <MetricCard
          label={t("factory.metric_clusters", "clone clusters")}
          value={t("factory.metric_clusters_value", "{n} ({m} pathological)", {
            n: String(num(loop.clone_clusters_tracked) ?? 0),
            m: String(num(loop.clone_clusters_pathological) ?? 0),
          })}
          tone="dim"
          sub={t("factory.metric_clusters_sub", "operator evidence is cumulative (persisted)")}
        />
      </div>

      <Panel title={t("factory.loop_panel", "Loop control + generate")} accent tight>
        {statusQ.isPending ? (
          <Skeleton count={2} />
        ) : (
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center" }}>
            <label className="tiny muted" htmlFor="factory-size">
              {t("factory.size_label", "size")}
            </label>
            <input id="factory-size" className="input" style={{ width: 70 }} value={size} onChange={(e) => setSize(e.target.value)} />
            <button
              className="btn small primary"
              disabled={cmd.state.running}
              onClick={() => ask(t("factory.cmd.generate_label", "Generate generation"), false, () => factoryUseCases.generate(num(Number(size)) ?? undefined))}
            >
              ⚒ {t("factory.cmd.generate", "generate")}
            </button>
            <button
              className="btn small"
              disabled={cmd.state.running || !mounted}
              onClick={() => ask(t("factory.cmd.loop_start_label", "Start autonomous loop"), true, factoryUseCases.loopStart)}
            >
              ▶ {t("factory.cmd.loop_start", "loop start")}
            </button>
            <button
              className="btn small"
              disabled={cmd.state.running || !mounted}
              onClick={() => ask(t("factory.cmd.pause_label", "Pause loop"), false, factoryUseCases.loopPause)}
            >
              ⏸ {t("factory.cmd.pause", "pause")}
            </button>
            <button
              className="btn small"
              disabled={cmd.state.running || !mounted}
              onClick={() => ask(t("factory.cmd.resume_label", "Resume loop"), false, factoryUseCases.loopResume)}
            >
              ⏵ {t("factory.cmd.resume", "resume")}
            </button>
            <button
              className="btn small danger"
              disabled={cmd.state.running || !mounted}
              onClick={() => ask(t("factory.cmd.stop_label", "STOP loop (kill switch)"), true, factoryUseCases.loopStop)}
            >
              ■ {t("factory.cmd.stop", "loop stop")}
            </button>
            <button
              className="btn small ghost"
              disabled={cmd.state.running || !mounted}
              onClick={() => ask(t("factory.cmd.provider_test_label", "Provider connectivity test"), false, factoryUseCases.providerTest)}
            >
              {t("factory.cmd.provider_test", "provider test")}
            </button>
          </div>
        )}
        <div className="tiny muted" style={{ marginTop: 6 }}>
          {t(
            "factory.loop_hint",
            "generate creates candidates that enter validation only (never live); loop start primes the real evaluation worker (generate→validate→evaluate→complete). Provider test never returns secret values.",
          )}
        </div>
        <CommandResultLine state={cmd.state} />
      </Panel>

      <div style={{ marginBlock: 12, display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
        <input
          className="input"
          style={{ width: 200 }}
          aria-label={t("factory.gen_filter_a11y", "Generation id filter")}
          placeholder={t("factory.gen_filter_placeholder", "generation id filter")}
          value={genFilter}
          onChange={(e) => setGenFilter(e.target.value)}
        />
        <span className="tiny faint">{t("factory.gen_filter_hint", "filters candidates / benchmarks / events")}</span>
      </div>

      <Segmented
        options={[
          { id: "generations" as const, label: t("factory.tab.generations", "Generations") },
          { id: "candidates" as const, label: t("factory.tab.candidates", "Candidates") },
          { id: "benchmarks" as const, label: t("factory.tab.benchmarks", "Benchmarks") },
          { id: "failures" as const, label: t("factory.tab.failures", "Failures") },
          { id: "ranking" as const, label: t("factory.tab.ranking", "Ranking") },
          { id: "memory" as const, label: t("factory.tab.memory", "Evolution memory") },
          { id: "console" as const, label: t("factory.tab.console", "Console") },
        ]}
        value={tab}
        onChange={setTab}
      />

      <div style={{ marginTop: 12 }}>
        {tab === "generations" && (
          <Panel title={t("factory.panel_generations", "Generations (newest first)")} tight>
            {generationsQ.isPending ? (
              <Skeleton count={4} />
            ) : generationsQ.isError ? (
              <ErrorState message={t("factory.err_generations", "generations endpoint failed")} onRetry={() => void generationsQ.refetch()} />
            ) : generationsQ.data?.available === false ? (
              <EmptyState message={t("factory.not_mounted", "Factory not mounted")} hint={generationsQ.data.reason ?? "FACTORY_UNAVAILABLE"} />
            ) : gens.length === 0 ? (
              <EmptyState message={t("factory.empty_generations", "No generations yet — press Generate.")} />
            ) : (
              <DataTable
                headers={[
                  { label: t("factory.h_generation", "generation") },
                  { label: t("factory.h_mode", "mode") },
                  { label: t("factory.h_state", "state") },
                  { label: t("factory.h_size", "size"), num: true },
                  { label: t("factory.h_created", "created") },
                  { label: "" },
                ]}
              >
                {gens.map((g) => (
                  <tr key={g.id}>
                    <td className="inline-mono tiny" title={g.id}>
                      {g.number !== null ? `#${g.number} ` : ""}
                      {g.id.slice(0, 14)}
                    </td>
                    <td className="tiny">{g.mode}</td>
                    <td>
                      <StatusPill status={g.state} />
                    </td>
                    <td className="num tiny">{g.size ?? "—"}</td>
                    <td className="tiny">{formatDateTime(g.createdAt)}</td>
                    <td>
                      <button
                        className="btn small ghost"
                        disabled={cmd.state.running || g.state === "COMPLETED"}
                        onClick={() =>
                          ask(t("factory.cmd.complete_label", "Complete {id}"), false, () => factoryUseCases.complete(g.id))
                        }
                      >
                        {t("factory.cmd.complete", "complete")}
                      </button>
                    </td>
                  </tr>
                ))}
              </DataTable>
            )}
          </Panel>
        )}

        {tab === "candidates" && (
          <Panel title={t("factory.panel_candidates", "Candidates (per generation filter)")} tight>
            {candidatesQ.isPending ? (
              <Skeleton count={4} />
            ) : candidatesQ.data?.available === false ? (
              <EmptyState message={t("factory.not_mounted", "Factory not mounted")} hint={candidatesQ.data.reason ?? "FACTORY_UNAVAILABLE"} />
            ) : arr(candidatesQ.data?.candidates).length === 0 ? (
              <EmptyState message={t("factory.empty_candidates", "No candidates for this selection.")} />
            ) : (
              <DataTable
                headers={[
                  { label: t("factory.h_candidate", "candidate") },
                  { label: t("factory.h_generation", "generation") },
                  { label: t("factory.h_lifecycle", "lifecycle") },
                  { label: t("factory.h_score", "score"), num: true },
                  { label: "" },
                ]}
              >
                {arr(candidatesQ.data?.candidates)
                  .slice(0, 100)
                  .map((c: Row, i: number) => {
                    const cid = str(c.candidate_id) ?? str(c.id) ?? "";
                    return (
                      <tr key={`${cid}-${i}`}>
                        <td className="inline-mono tiny">{cid.slice(0, 16) || "—"}</td>
                        <td className="inline-mono tiny">{str(c.generation_id)?.slice(0, 10) ?? "—"}</td>
                        <td>
                          <StatusPill status={str(c.lifecycle) ?? str(c.status)} />
                        </td>
                        <td className="num tiny">{num(c.score) === null ? "—" : formatNumber(num(c.score)!, 3)}</td>
                        <td>
                          <button
                            className="btn small ghost"
                            disabled={cmd.state.running || !cid}
                            onClick={() => ask(t("factory.cmd.evaluate_label", "Evaluate {id}"), false, () => factoryUseCases.evaluate(cid))}
                          >
                            {t("factory.cmd.evaluate", "evaluate")}
                          </button>
                        </td>
                      </tr>
                    );
                  })}
              </DataTable>
            )}
          </Panel>
        )}

        {tab === "benchmarks" && (
          <Panel title={t("factory.panel_benchmarks", "Strategy-aware benchmarks (per-candidate backtests)")} tight>
            {benchmarksQ.isPending ? (
              <Skeleton count={4} />
            ) : benchmarksQ.data?.available === false ? (
              <EmptyState message={t("factory.not_mounted", "Factory not mounted")} hint={benchmarksQ.data.reason ?? ""} />
            ) : arr(benchmarksQ.data?.benchmarks).length === 0 ? (
              <EmptyState message={t("factory.empty_benchmarks", "No benchmark rows for this selection.")} />
            ) : (
              <DataTable
                headers={[
                  { label: t("factory.h_candidate", "candidate") },
                  { label: t("factory.h_coverage", "coverage"), num: true },
                  { label: t("factory.h_decision", "decision") },
                  { label: t("factory.h_oos", "oos") },
                  { label: t("factory.h_robust", "robust") },
                ]}
              >
                {arr(benchmarksQ.data?.benchmarks)
                  .slice(0, 60)
                  .map((b: Row, i: number) => (
                    <tr key={i}>
                      <td className="inline-mono tiny">{str(b.candidate_id)?.slice(0, 14) ?? "—"}</td>
                      <td className="num tiny">{num(b.coverage) === null ? "—" : `${formatNumber(num(b.coverage)!, 1)}%`}</td>
                      <td>
                        <StatusPill status={str(b.decision_label) ?? str(b.decision)} />
                      </td>
                      <td className="tiny">{str(b.oos_status) ?? "—"}</td>
                      <td className="tiny">{str(b.robustness_status) ?? "—"}</td>
                    </tr>
                  ))}
              </DataTable>
            )}
          </Panel>
        )}

        {tab === "failures" && (
          <Panel title={t("factory.panel_failures", "Failure ledger")} tight>
            {failuresQ.isPending ? (
              <Skeleton count={3} />
            ) : failuresQ.data?.available === false ? (
              <EmptyState message={t("factory.not_mounted", "Factory not mounted")} hint={failuresQ.data.reason ?? ""} />
            ) : arr(failuresQ.data?.failures).length === 0 ? (
              <EmptyState message={t("factory.empty_failures", "No recorded failures.")} />
            ) : (
              <DataTable
                headers={[
                  { label: t("factory.h_at", "at") },
                  { label: t("factory.h_stage", "stage") },
                  { label: t("factory.h_reason", "reason") },
                ]}
              >
                {arr(failuresQ.data?.failures)
                  .slice(0, 80)
                  .map((f: Row, i: number) => (
                    <tr key={i}>
                      <td className="tiny">{formatDateTime(str(f.created_at) ?? str(f.at))}</td>
                      <td className="tiny">{str(f.stage) ?? str(f.kind) ?? "—"}</td>
                      <td className="tiny muted" title={str(f.reason) ?? ""}>
                        {(str(f.reason) ?? "—").slice(0, 60)}
                      </td>
                    </tr>
                  ))}
              </DataTable>
            )}
          </Panel>
        )}

        {tab === "ranking" && (
          <Panel
            title={t("factory.panel_ranking", "Registry survivors ranked by {dim}", { dim: dimLabels[dim] ?? dim })}
            right={
              <select
                aria-label={t("factory.rank_dim_a11y", "Rank dimension")}
                className="select"
                style={{ width: 130 }}
                value={dim}
                onChange={(e) => setDim(e.target.value)}
              >
                {RANK_DIMS.map((d) => (
                  <option key={d} value={d}>
                    {dimLabels[d] ?? d}
                  </option>
                ))}
              </select>
            }
            tight
          >
            {rankingQ.isPending ? (
              <Skeleton count={4} />
            ) : rankingQ.data?.available === false ? (
              <EmptyState message={t("factory.not_mounted", "Factory not mounted")} hint={rankingQ.data.reason ?? ""} />
            ) : (
              <DataTable
                headers={[
                  { label: "#" },
                  { label: t("factory.h_strategy", "strategy") },
                  { label: t("factory.h_lifecycle", "lifecycle") },
                  { label: t("factory.h_value", "value"), num: true },
                ]}
              >
                {arr(rankingQ.data?.ranked).map((r: Row, i: number) => (
                  <tr key={i}>
                    <td className="num tiny">{i + 1}</td>
                    <td className="inline-mono tiny">{str(r.strategy_id)?.slice(0, 16) ?? "—"}</td>
                    <td>
                      <StatusPill status={str(r.lifecycle)} />
                    </td>
                    <td className="num tiny">{formatNumber(num(r.score) ?? num(r.value) ?? NaN, 3)}</td>
                  </tr>
                ))}
              </DataTable>
            )}
          </Panel>
        )}

        {tab === "memory" && (
          <Panel title={t("factory.panel_memory", "Structured evolution memory (next-generation input)")} tight>
            {memoryQ.isPending ? (
              <Skeleton count={3} />
            ) : memoryQ.data?.available === false ? (
              <EmptyState message={t("factory.not_mounted", "Factory not mounted")} hint={memoryQ.data.reason ?? ""} />
            ) : (
              <JsonBlock value={memoryQ.data?.memory} maxChars={6000} />
            )}
          </Panel>
        )}

        {tab === "console" && (
          <Panel title={t("factory.panel_console", "Factory event console")} tight>
            {eventsQ.isPending ? (
              <Skeleton count={4} />
            ) : eventsQ.data?.available === false ? (
              <EmptyState message={t("factory.not_mounted", "Factory not mounted")} hint={eventsQ.data.reason ?? ""} />
            ) : arr(eventsQ.data?.events).length === 0 ? (
              <EmptyState message={t("factory.empty_events", "No events.")} />
            ) : (
              <DataTable
                headers={[
                  { label: t("factory.h_at", "at") },
                  { label: t("factory.h_event", "event") },
                  { label: t("factory.h_detail", "detail") },
                ]}
              >
                {arr(eventsQ.data?.events)
                  .slice(0, 100)
                  .map((e: Row, i: number) => (
                    <tr key={i}>
                      <td className="tiny">{formatDateTime(str(e.created_at) ?? str(e.at))}</td>
                      <td className="small">{str(e.event_type) ?? str(e.kind) ?? "—"}</td>
                      <td className="tiny muted" title={str(e.payload) ?? str(e.detail) ?? ""}>
                        {(str(e.detail) ?? str(e.message) ?? "").slice(0, 80)}
                      </td>
                    </tr>
                  ))}
              </DataTable>
            )}
          </Panel>
        )}
      </div>

      <div style={{ height: 12 }} />
      <Panel title={t("factory.panel_llm", "LLM provider config (safe status — secrets never round-trip)")} tight>
        {llmQ.isPending ? (
          <Skeleton count={2} />
        ) : llmQ.data?.available === false ? (
          <EmptyState message={t("factory.empty_llm", "Factory not mounted — llm-config has no backend data.")} />
        ) : (
          <dl className="kv">
            <InfoRow
              label="api_key_present"
              value={bool(obj(llmQ.data?.status).api_key_present) ? t("factory.key_present", "yes (masked)") : t("factory.key_absent", "no")}
            />
            <InfoRow label="base_url" value={str(obj(llmQ.data?.status).base_url) ?? "—"} />
            <InfoRow label={t("factory.f_model", "model")} value={str(obj(llmQ.data?.status).model) ?? "—"} />
            <InfoRow label={t("factory.f_temperature", "temperature")} value={formatNumber(num(obj(llmQ.data?.status).temperature) ?? NaN, 2)} />
            <InfoRow
              label={t("factory.f_enabled", "factory enabled")}
              value={<StatusPill status={str(obj(llmQ.data?.status).factory_enabled) ?? str(obj(llmQ.data?.status).enabled) ?? "—"} />}
            />
          </dl>
        )}
          <div className="row" style={{ marginTop: 8, display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
            <span className="tiny muted">{t("factory.chg_note", "strategy factory feature (CHG-0034 single user control):")}</span>
            <button
              className="btn small primary"
              disabled={cmd.state.running}
              onClick={() => ask(t("factory.cmd.enable_label", "Enable factory provider"), false, () => factoryUseCases.providerToggle(true))}
            >
              {t("common.enable", "enable")}
            </button>
            <button
              className="btn small danger"
              disabled={cmd.state.running}
              onClick={() => ask(t("factory.cmd.disable_label", "Disable factory provider"), true, () => factoryUseCases.providerToggle(false))}
            >
              {t("common.disable", "disable")}
            </button>
            <span className="tiny faint">{t("factory.toggle_hint", "enabling validates config without a network probe; disabling stops new provider requests only.")}</span>
          </div>
          <div className="tiny faint" style={{ marginTop: 6 }}>
            {t(
              "factory.apikey_note",
              "Editing the API key deliberately stays out of the alt UI surface (masked-only contract, parity with legacy); configure via the settings route owned by the Config feature.",
            )}
          </div>
      </Panel>

      {pending && (
        <ConfirmModal
          title={t("factory.confirm_title", "Confirm: {cmd}", { cmd: pending.label })}
          danger={pending.danger}
          confirmLabel={t("factory.confirm_send", "Send command")}
          busy={cmd.state.running}
          onCancel={() => setPending(null)}
          onConfirm={async () => {
            const p = pending;
            setPending(null);
            await runCmd(p.label, p.run);
          }}
        >
          <div className="small">
            {pending.danger
              ? t(
                  "factory.confirm_danger",
                  "This changes the autonomous loop control state on the backend. The factory never touches the live trading path, but loop start keeps generating/validating candidates until paused/stopped.",
                )
              : t("factory.confirm_plain", "Command goes to the factory backend; its response decides the outcome.")}
          </div>
        </ConfirmModal>
      )}
    </div>
  );
}
