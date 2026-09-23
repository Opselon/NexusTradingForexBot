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

import { useCallback, useMemo, useState } from "react";
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
import { formatNumber } from "@/lib/format";
import { ApiError } from "@/types/api";
import { CommandResultLine, FreshnessCaption, InfoRow, JsonBlock, StatusPill } from "../../research/ui/lane5Kit";
import { arr, bool, num, obj, str, type FactoryCommandDto, type Row } from "../model";
import { factoryQueries, factoryUseCases } from "../useCases";
import { BenchmarkRow, CandidateRow, EventRow, FailureRow, GenerationRow, RankingRow, type AskFn } from "./FactoryTableRows";

type Tab = "generations" | "candidates" | "benchmarks" | "failures" | "ranking" | "memory" | "console";

const RANK_DIMS = ["OVERALL", "SHARPE", "EXPECTANCY", "STABILITY"];

/** Failure text of a query — backend code + message, verbatim. */
function errText(e: unknown): string {
  if (e instanceof ApiError) return `${e.code}: ${e.message}`;
  if (e instanceof Error) return e.message;
  return "Backend request failed.";
}

/** Correlation id carried by ApiError for ErrorState's request_id line. */
function errRid(e: unknown): string | null {
  if (e instanceof ApiError) return e.requestId;
  if (e && typeof e === "object" && "requestId" in e) {
    const r = (e as { requestId?: unknown }).requestId;
    return typeof r === "string" && r !== "" ? r : null;
  }
  return null;
}

/** Query -> ErrorState with request_id + Retry (4-state ladder, error rung). */
function QueryError({ q }: { q: { error: unknown; refetch: () => unknown } }) {
  return (
    <ErrorState
      message={errText(q.error)}
      requestId={errRid(q.error)}
      onRetry={() => void q.refetch()}
    />
  );
}

export default function FactoryPage(props: ShellPageProps) {
  void props;
  const [tab, setTab] = useState<Tab>("generations");
  const [genFilter, setGenFilter] = useState("");
  const [dim, setDim] = useState("OVERALL");
  const [size, setSize] = useState("20");
  const [pending, setPending] = useState<{ label: string; danger: boolean; run: () => Promise<FactoryCommandDto> } | null>(null);
  const cmd = useMutationFeedback();
  const qc = useQueryClient();

  const statusQ = useQuery({
    queryKey: ["factory", "status"],
    queryFn: ({ signal }) => factoryQueries.status(signal),
    // Bounded poll: 20s while the tab is active; react-query pauses interval
    // refetches while the window is in the background (default) and aborts
    // in-flight requests via the queryFn AbortSignal.
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
  // VOs are rebuilt only when the generations payload changes, so the memoized
  // GenerationRow leaves stay effective across the 20s status poll.
  const gens = useMemo(
    () => factoryUseCases.generationList(arr(generationsQ.data?.generations)),
    [generationsQ.data],
  );

  const runCmd = async (label: string, fn: () => Promise<FactoryCommandDto>) => {
    await cmd.run(async () => {
      const v = factoryUseCases.verdict(await fn());
      return { ok: v.ok, success: v.ok, message: `${label}: ${v.message}`, status: v.ok ? 200 : 500 };
    });
    void qc.invalidateQueries({ queryKey: ["factory"] });
  };

  const ask = useCallback<AskFn>(
    (label, danger, run) => setPending({ label, danger, run }),
    [],
  );

  return (
    <div>
      <div className="page-head" style={{ display: "flex", alignItems: "baseline", gap: 10, flexWrap: "wrap" }}>
        <h2>Strategy Factory</h2>
        <span className="muted small">autonomous evolution control room — never touches the live path</span>
        <FreshnessCaption
          timestamp={statusQ.dataUpdatedAt || null}
          source="factory store · GET /api/factory/status"
          isFetching={statusQ.isFetching}
          error={statusQ.isError}
        />
      </div>

      {statusQ.isError ? (
        <QueryError q={statusQ} />
      ) : (
        !mounted &&
        !statusQ.isPending && (
          <div className="banner stale" role="status">
            Strategy Factory not mounted — {statusQ.data?.reason ?? "backend answered available:false"}. Every section below shows the backend's own
            unavailability verdict; no data is simulated.
          </div>
        )
      )}

      <div className="grid cols-4">
        <MetricCard label="loop state" value={<StatusBadge status={str(loop.state) ?? "UNKNOWN"} />} sub={`generation ${str(loop.current_generation) ?? "—"}`} tone={bool(loop.kill_requested) ? "neg" : "dim"} />
        <MetricCard
          label="LLM provider"
          value={<StatusBadge status={bool(provider.available) ? "READY" : "UNAVAILABLE"} />}
          sub={`${str(provider.model) ?? "no model"} · ${bool(obj(llmQ.data?.status).api_key_present) ? "key set" : "key missing"}`}
        />
        <MetricCard
          label="provider usage"
          value={formatNumber(num(usage.requests) ?? 0, 0)}
          sub={`errors ${String(num(usage.errors) ?? 0)} · prompt ${str(provider.prompt_version) ?? "—"}`}
          tone="dim"
        />
        <MetricCard
          label="clone clusters"
          value={`${String(num(loop.clone_clusters_tracked) ?? 0)} (${String(num(loop.clone_clusters_pathological) ?? 0)} pathological)`}
          tone="dim"
          sub="operator evidence is cumulative (persisted)"
        />
      </div>

      <Panel title="Loop control + generate" accent tight>
        {statusQ.isPending ? (
          <Skeleton count={2} />
        ) : (
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center" }}>
            <label className="tiny muted" htmlFor="factory-size">
              size
            </label>
            <input id="factory-size" className="input" style={{ width: 70 }} value={size} onChange={(e) => setSize(e.target.value)} />
            <button className="btn small primary" disabled={cmd.state.running} onClick={() => ask("Generate generation", false, () => factoryUseCases.generate(num(Number(size)) ?? undefined))}>
              ⚒ generate
            </button>
            <button className="btn small" disabled={cmd.state.running || !mounted} onClick={() => ask("Start autonomous loop", true, factoryUseCases.loopStart)}>
              ▶ loop start
            </button>
            <button className="btn small" disabled={cmd.state.running || !mounted} onClick={() => ask("Pause loop", false, factoryUseCases.loopPause)}>
              ⏸ pause
            </button>
            <button className="btn small" disabled={cmd.state.running || !mounted} onClick={() => ask("Resume loop", false, factoryUseCases.loopResume)}>
              ⏵ resume
            </button>
            <button className="btn small danger" disabled={cmd.state.running || !mounted} onClick={() => ask("STOP loop (kill switch)", true, factoryUseCases.loopStop)}>
              ■ loop stop
            </button>
            <button className="btn small ghost" disabled={cmd.state.running || !mounted} onClick={() => ask("Provider connectivity test", false, factoryUseCases.providerTest)}>
              provider test
            </button>
          </div>
        )}
        <div className="tiny muted" style={{ marginTop: 6 }}>
          generate creates candidates that enter validation only (never live); loop start primes the real evaluation worker (generate→validate→
          evaluate→complete). Provider test never returns secret values.
        </div>
        <CommandResultLine state={cmd.state} />
      </Panel>

      <div style={{ marginBlock: 12, display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
        <input className="input" style={{ width: 200 }} aria-label="Generation id filter" placeholder="generation id filter" value={genFilter} onChange={(e) => setGenFilter(e.target.value)} />
        <span className="tiny faint">filters candidates / benchmarks / events</span>
      </div>

      <Segmented
        options={[
          { id: "generations" as const, label: "Generations" },
          { id: "candidates" as const, label: "Candidates" },
          { id: "benchmarks" as const, label: "Benchmarks" },
          { id: "failures" as const, label: "Failures" },
          { id: "ranking" as const, label: "Ranking" },
          { id: "memory" as const, label: "Evolution memory" },
          { id: "console" as const, label: "Console" },
        ]}
        value={tab}
        onChange={setTab}
      />

      <div style={{ marginTop: 12 }}>
        {tab === "generations" && (
          <Panel title="Generations (newest first)" tight>
            {generationsQ.isPending ? (
              <Skeleton count={4} />
            ) : generationsQ.isError ? (
              <QueryError q={generationsQ} />
            ) : generationsQ.data?.available === false ? (
              <EmptyState message="Factory not mounted" hint={`GET /api/factory/generations: ${generationsQ.data.reason ?? "FACTORY_UNAVAILABLE"}`} />
            ) : gens.length === 0 ? (
              <EmptyState message="No generations yet — press Generate." hint="GET /api/factory/generations returned an empty list." />
            ) : (
              <DataTable headers={[{ label: "generation" }, { label: "mode" }, { label: "state" }, { label: "size", num: true }, { label: "created" }, { label: "" }]}>
                {gens.map((g) => (
                  <GenerationRow key={g.id} g={g} busy={cmd.state.running} onAsk={ask} />
                ))}
              </DataTable>
            )}
          </Panel>
        )}

        {tab === "candidates" && (
          <Panel title="Candidates (per generation filter)" tight>
            {candidatesQ.isPending ? (
              <Skeleton count={4} />
            ) : candidatesQ.isError ? (
              <QueryError q={candidatesQ} />
            ) : candidatesQ.data?.available === false ? (
              <EmptyState message="Factory not mounted" hint={`GET /api/factory/candidates: ${candidatesQ.data.reason ?? "FACTORY_UNAVAILABLE"}`} />
            ) : arr(candidatesQ.data?.candidates).length === 0 ? (
              <EmptyState message="No candidates for this selection." hint="GET /api/factory/candidates returned an empty list for this generation filter." />
            ) : (
              <DataTable headers={[{ label: "candidate" }, { label: "generation" }, { label: "lifecycle" }, { label: "score", num: true }, { label: "" }]}>
                {arr(candidatesQ.data?.candidates)
                  .slice(0, 100)
                  .map((c: Row, i: number) => {
                    const cid = str(c.candidate_id) ?? str(c.id) ?? "";
                    return <CandidateRow key={cid || `cand-${i}`} c={c} busy={cmd.state.running} onAsk={ask} />;
                  })}
              </DataTable>
            )}
          </Panel>
        )}

        {tab === "benchmarks" && (
          <Panel title="Strategy-aware benchmarks (per-candidate backtests)" tight>
            {benchmarksQ.isPending ? (
              <Skeleton count={4} />
            ) : benchmarksQ.isError ? (
              <QueryError q={benchmarksQ} />
            ) : benchmarksQ.data?.available === false ? (
              <EmptyState message="Factory not mounted" hint={`GET /api/factory/benchmarks: ${benchmarksQ.data.reason ?? ""}`} />
            ) : arr(benchmarksQ.data?.benchmarks).length === 0 ? (
              <EmptyState message="No benchmark rows for this selection." hint="GET /api/factory/benchmarks returned an empty list for this generation filter." />
            ) : (
              <DataTable headers={[{ label: "candidate" }, { label: "coverage", num: true }, { label: "decision" }, { label: "oos" }, { label: "robust" }]}>
                {arr(benchmarksQ.data?.benchmarks)
                  .slice(0, 60)
                  .map((b: Row, i) => (
                    <BenchmarkRow key={`bench-${i}`} b={b} />
                  ))}
              </DataTable>
            )}
          </Panel>
        )}

        {tab === "failures" && (
          <Panel title="Failure ledger" tight>
            {failuresQ.isPending ? (
              <Skeleton count={3} />
            ) : failuresQ.isError ? (
              <QueryError q={failuresQ} />
            ) : failuresQ.data?.available === false ? (
              <EmptyState message="Factory not mounted" hint={`GET /api/factory/failures: ${failuresQ.data.reason ?? ""}`} />
            ) : arr(failuresQ.data?.failures).length === 0 ? (
              <EmptyState message="No recorded failures." hint="GET /api/factory/failures returned an empty ledger." />
            ) : (
              <DataTable headers={[{ label: "at" }, { label: "stage" }, { label: "reason" }]}>
                {arr(failuresQ.data?.failures)
                  .slice(0, 80)
                  .map((f: Row, i) => (
                    <FailureRow key={`fail-${i}`} f={f} />
                  ))}
              </DataTable>
            )}
          </Panel>
        )}

        {tab === "ranking" && (
          <Panel
            title={`Registry survivors ranked by ${dim}`}
            right={
              <select aria-label="Rank dimension" className="select" style={{ width: 130 }} value={dim} onChange={(e) => setDim(e.target.value)}>
                {RANK_DIMS.map((d) => (
                  <option key={d} value={d}>
                    {d}
                  </option>
                ))}
              </select>
            }
            tight
          >
            {rankingQ.isPending ? (
              <Skeleton count={4} />
            ) : rankingQ.isError ? (
              <QueryError q={rankingQ} />
            ) : rankingQ.data?.available === false ? (
              <EmptyState message="Factory not mounted" hint={`GET /api/factory/ranking: ${rankingQ.data.reason ?? ""}`} />
            ) : arr(rankingQ.data?.ranked).length === 0 ? (
              <EmptyState message="No ranked survivors yet." hint={`GET /api/factory/ranking returned an empty list for dimension ${dim}.`} />
            ) : (
              <DataTable headers={[{ label: "#" }, { label: "strategy" }, { label: "lifecycle" }, { label: "value", num: true }]}>
                {arr(rankingQ.data?.ranked).map((r: Row, i) => (
                  <RankingRow key={`rank-${dim}-${i}`} r={r} rank={i + 1} />
                ))}
              </DataTable>
            )}
          </Panel>
        )}

        {tab === "memory" && (
          <Panel title="Structured evolution memory (next-generation input)" tight>
            {memoryQ.isPending ? (
              <Skeleton count={3} />
            ) : memoryQ.isError ? (
              <QueryError q={memoryQ} />
            ) : memoryQ.data?.available === false ? (
              <EmptyState message="Factory not mounted" hint={`GET /api/factory/memory: ${memoryQ.data.reason ?? ""}`} />
            ) : memoryQ.data?.memory == null ? (
              <EmptyState message="No evolution memory stored." hint="GET /api/factory/memory answered without a memory payload." />
            ) : (
              <JsonBlock value={memoryQ.data?.memory} maxChars={6000} />
            )}
          </Panel>
        )}

        {tab === "console" && (
          <Panel title="Factory event console" tight>
            {eventsQ.isPending ? (
              <Skeleton count={4} />
            ) : eventsQ.isError ? (
              <QueryError q={eventsQ} />
            ) : eventsQ.data?.available === false ? (
              <EmptyState message="Factory not mounted" hint={`GET /api/factory/events: ${eventsQ.data.reason ?? ""}`} />
            ) : arr(eventsQ.data?.events).length === 0 ? (
              <EmptyState message="Console is quiet." hint="GET /api/factory/events returned an empty stream for this generation filter." />
            ) : (
              <DataTable headers={[{ label: "time" }, { label: "event" }, { label: "detail" }]}>
                {arr(eventsQ.data?.events)
                  .slice(0, 100)
                  .map((e: Row, i) => (
                    <EventRow key={`evt-${i}`} e={e} />
                  ))}
              </DataTable>
            )}
          </Panel>
        )}
      </div>

      <div style={{ height: 12 }} />
      <Panel title="LLM provider config (safe status — secrets never round-trip)" tight>
        {llmQ.isPending ? (
          <Skeleton count={2} />
        ) : llmQ.isError ? (
          <QueryError q={llmQ} />
        ) : llmQ.data?.available === false ? (
          <EmptyState message="Factory not mounted" hint={`GET /api/factory/llm-config: ${llmQ.data.reason ?? ""}`} />
        ) : !llmQ.data?.status && !llmQ.data?.provider ? (
          <EmptyState message="No LLM config stored." hint="GET /api/factory/llm-config answered without status or provider rows." />
        ) : (
          <dl className="kv">
            <InfoRow label="api_key_present" value={bool(obj(llmQ.data?.status).api_key_present) ? "yes (masked)" : "no"} />
            <InfoRow label="base_url" value={str(obj(llmQ.data?.status).base_url) ?? "—"} />
            <InfoRow label="model" value={str(obj(llmQ.data?.status).model) ?? "—"} />
            <InfoRow label="temperature" value={formatNumber(num(obj(llmQ.data?.status).temperature) ?? NaN, 2)} />
            <InfoRow label="factory enabled" value={<StatusPill status={str(obj(llmQ.data?.status).factory_enabled) ?? str(obj(llmQ.data?.status).enabled) ?? "—"} />} />
          </dl>
        )}
          <div className="row" style={{ marginTop: 8, display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
            <span className="tiny muted">strategy factory feature (CHG-0034 single user control):</span>
            <button className="btn small primary" disabled={cmd.state.running} onClick={() => ask("Enable factory provider", false, () => factoryUseCases.providerToggle(true))}>
              enable
            </button>
            <button className="btn small danger" disabled={cmd.state.running} onClick={() => ask("Disable factory provider", true, () => factoryUseCases.providerToggle(false))}>
              disable
            </button>
            <span className="tiny faint">enabling validates config without a network probe; disabling stops new provider requests only.</span>
          </div>
          <div className="tiny faint" style={{ marginTop: 6 }}>
            Editing the API key deliberately stays out of the alt UI surface (masked-only contract, parity with legacy); configure via the settings
            route owned by the Config feature.
          </div>
      </Panel>

      {pending && (
        <ConfirmModal
          title={`Confirm: ${pending.label}`}
          danger={pending.danger}
          confirmLabel="Send command"
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
              ? "This changes the autonomous loop control state on the backend. The factory never touches the live trading path, but loop start keeps generating/validating candidates until paused/stopped."
              : "Command goes to the factory backend; its response decides the outcome."}
          </div>
        </ConfirmModal>
      )}
    </div>
  );
}
