/**
 * features/provisioning/ui/ProvisioningPage.tsx
 *
 * First-Run Model Preparation — the new-web replacement for
 * Web/first_setup.html ("Nexus First Setup — Model Preparation").
 *
 * What this panel IS: the operator surface for preparing a servable model
 * before the engine runs — environment discovery, optional stack install,
 * official-model download, and a local training run with a live event tail.
 *
 * Design rules carried over from the legacy page and the backend contract:
 *  - Environment GET is DISCOVERY ONLY (BUG-301). Installing is the explicit
 *    POST /environment/install action with operator consent.
 *  - Server responses are typed: failures arrive as {success:false, code,
 *    message?, remedy?}. The UI renders the code + remedy verbatim and never
 *    fabricates a message or shows a stack trace.
 *  - One local training run at a time; cancel is observed at the next epoch
 *    boundary, not instantly.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import type { ShellPageProps } from "@/app/featureModule";
import { EmptyState, ErrorState, LoadingState, Segmented } from "@/components/primitives";
import { provisioningApi } from "../api";
import type {
  EnvCheck,
  EnvironmentReport,
  ProgressEvent,
  ProvisioningEnvironmentResponse,
  ProvisioningStatusResponse,
  TrainBackend,
  TrainSource,
} from "../model";
import { TRAIN_BACKENDS, TRAIN_SOURCES } from "../model";

const POLL_MS = 2000;

const CANDLE_OPTIONS = [
  { id: "3000", label: "3k" },
  { id: "10000", label: "10k" },
  { id: "30000", label: "30k" },
  { id: "100000", label: "100k" },
];

function classNames(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(" ");
}

/** The ok===false subset of the report checklist (server `failing()`). */
function failingChecks(report: EnvironmentReport | undefined | null): EnvCheck[] {
  if (!report) return [];
  const raw = report.checks;
  return Array.isArray(raw) ? raw.filter((c) => !c.ok) : [];
}

/** One line of the environment checklist. */
function CheckRow({
  label,
  ok,
  note,
}: {
  label: string;
  ok: boolean | null | undefined;
  note: string | null | undefined;
}) {
  return (
    <div className="kv-row" style={{ gridTemplateColumns: "max-content 1fr", alignItems: "baseline" }}>
      <dt className="inline-mono small">{label}</dt>
      <dd style={{ textAlign: "left", display: "flex", gap: 8, alignItems: "baseline" }}>
        <span
          className={classNames("badge", ok === null || ok === undefined ? "unknown" : ok ? "good" : "bad")}
          style={{ flex: "0 0 auto" }}
        >
          {ok === null || ok === undefined ? "—" : ok ? "OK" : "FAIL"}
        </span>
        {note ? <span className="muted small">{note}</span> : null}
      </dd>
    </div>
  );
}

export default function ProvisioningPage(_props: ShellPageProps) {
  const [status, setStatus] = useState<ProvisioningStatusResponse | null>(null);
  const [env, setEnv] = useState<ProvisioningEnvironmentResponse | null>(null);
  const [backend, setBackend] = useState<TrainBackend>("auto");
  const [source, setSource] = useState<TrainSource>("file");
  const [filePath, setFilePath] = useState<string>("");
  const [candles, setCandles] = useState<string>("10000");
  const [folds, setFolds] = useState<number>(6);
  const [epochs, setEpochs] = useState<number>(4);
  const [prepareEnvironment, setPrepareEnvironment] = useState<boolean>(false);
  const [installing, setInstalling] = useState<boolean>(false);
  const [officialBusy, setOfficialBusy] = useState<boolean>(false);
  const [training, setTraining] = useState<boolean>(false);
  const [events, setEvents] = useState<ProgressEvent[]>([]);
  const [result, setResult] = useState<Record<string, unknown> | null>(null);
  const [error, setError] = useState<string>("");
  const [notice, setNotice] = useState<string>("");
  const seenSeq = useRef<number>(0);

  const refreshStatus = useCallback(async () => {
    try {
      const s = await provisioningApi.status();
      setStatus(s);
    } catch (err) {
      setError(errText(err));
    }
  }, []);

  const refreshEnv = useCallback(
    async (be: TrainBackend) => {
      try {
        const e = await provisioningApi.environment(be);
        setEnv(e);
        setError("");
      } catch (err) {
        setError(errText(err));
      }
    },
    [],
  );

  // Initial loads.
  useEffect(() => {
    void refreshStatus();
  }, [refreshStatus]);
  useEffect(() => {
    void refreshEnv(backend);
  }, [backend, refreshEnv]);

  // Live event tail while a run is active.
  useEffect(() => {
    if (!training) return;
    let cancelled = false;
    const tick = async () => {
      try {
        const p = await provisioningApi.trainProgress(seenSeq.current);
        if (cancelled) return;
        if (Array.isArray(p.events) && p.events.length > 0) {
          setEvents((prev) => [...prev, ...p.events]);
          const maxSeq = p.events.reduce((m, e) => Math.max(m, Number(e.seq ?? 0)), seenSeq.current);
          seenSeq.current = maxSeq;
        }
        if (!p.active) {
          setTraining(false);
          setResult(p.result ?? null);
          if (p.cancelled) setNotice("Run cancelled — observed at the epoch boundary.");
          else if (!p.success) setError(codeOf(p));
          else setNotice("Run finished.");
        }
      } catch (err) {
        if (!cancelled) setError(errText(err));
      }
    };
    void tick();
    const id = window.setInterval(tick, POLL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [training]);

  const onInstall = useCallback(async () => {
    setInstalling(true);
    setError("");
    setNotice("");
    try {
      const out = await provisioningApi.install({ backend });
      if (!out.success) {
        setError(codeOf(out) + (out.remedy ? ` — ${out.remedy}` : ""));
      } else {
        setNotice("Environment install finished.");
        await refreshEnv(backend);
      }
    } catch (err) {
      setError(errText(err));
    } finally {
      setInstalling(false);
    }
  }, [backend, refreshEnv]);

  const onOfficial = useCallback(async () => {
    setOfficialBusy(true);
    setError("");
    setNotice("");
    try {
      const out = await provisioningApi.official({});
      if (!out.success) {
        setError(codeOf(out) + (out.remedy ? ` — ${out.remedy}` : ""));
      } else {
        setNotice(out.ok ? "Official model installed and servable." : "Official model downloaded; see the status slot.");
        await refreshStatus();
      }
    } catch (err) {
      setError(errText(err));
    } finally {
      setOfficialBusy(false);
    }
  }, [refreshStatus]);

  const onTrain = useCallback(async () => {
    setError("");
    setNotice("");
    setEvents([]);
    setResult(null);
    seenSeq.current = 0;
    const candleNum = candles ? Number(candles) : null;
    try {
      const out = await provisioningApi.trainStart({
        source,
        file: source === "file" ? filePath || null : null,
        candles: source === "broker" ? candleNum : null,
        folds,
        epochs,
        backend,
        prepare_environment: prepareEnvironment,
      });
      if (!out.success) {
        setError(codeOf(out) + (out.detail ? ` — ${out.detail}` : ""));
        return;
      }
      setTraining(true);
      setNotice("Training run started.");
    } catch (err) {
      setError(errText(err));
    }
  }, [source, filePath, candles, folds, epochs, backend, prepareEnvironment]);

  const onCancel = useCallback(async () => {
    setError("");
    try {
      const out = await provisioningApi.trainCancel();
      setNotice(out.cancel === "REQUESTED" ? "Cancel requested — observed at the next epoch boundary." : "No active run to cancel.");
    } catch (err) {
      setError(errText(err));
    }
  }, []);

  const busy = installing || officialBusy || training;
  const report = env?.report;
  const failures = failingChecks(report);
  const trainingReady = Boolean(report?.training_ready);
  const recommended = status?.recommended;
  const allowedRoots = status?.allowed_import_roots ?? [];

  return (
    <div className="space-y-5">
      {/* ------------------------------------------------------------- header */}
      <div className="panel">
        <div className="flex items-center gap-2">
          <h2 className="text-base font-bold text-white">First-Run Model Preparation</h2>
          <span
            className={classNames(
              "badge",
              trainingReady ? "good" : report ? "warn" : "unknown",
            )}
          >
            {report ? (trainingReady ? "ENV READY" : "ENV NOT READY") : "ENV —"}
          </span>
        </div>
        <p className="mt-1 small muted">
          Prepare a servable model before the engine runs. Environment check is discovery only —
          installing is an explicit, consented action. One local training run at a time.
        </p>
        {recommended ? (
          <div className="banner info mt-2" role="status">
            Recommended next action: <span className="inline-mono">{String(recommended)}</span>
          </div>
        ) : null}
      </div>

      {error ? <ErrorState message={error} onRetry={() => setError("")} /> : null}
      {notice ? (
        <div className="banner good" role="status">
          {notice}
        </div>
      ) : null}

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        {/* ------------------------------------------------ environment card */}
        <section className="panel">
          <div className="flex items-center justify-between gap-3">
            <h3 className="text-sm font-bold text-cyan-400">Training Environment</h3>
            <Segmented
              options={TRAIN_BACKENDS.map((b) => ({ id: b, label: b.toUpperCase() }))}
              value={backend}
              onChange={setBackend}
            />
          </div>
          <p className="small muted mt-1">
            Backend <span className="inline-mono">{backend}</span> — discovery only, nothing is installed by this check.
          </p>

          {env ? (
            <dl className="kv mt-3">
              <CheckRow
                label="python"
                ok={Boolean(env.environment?.python)}
                note={env.environment?.python ? String(env.environment.python) : "not detected"}
              />
              <CheckRow
                label="pytorch"
                ok={Boolean(env.environment?.torch)}
                note={env.environment?.torch ? String(env.environment.torch) : "not detected"}
              />
              <CheckRow
                label="cuda"
                ok={env.environment?.cuda ?? null}
                note={env.environment?.gpu_name ? String(env.environment.gpu_name) : "no GPU required"}
              />
              <CheckRow label="training_ready" ok={trainingReady} note={trainingReady ? "can train now" : "blocked"} />
            </dl>
          ) : (
            <LoadingState label="Resolving environment…" />
          )}

          {failures.length > 0 ? (
            <div className="mt-3 rounded border border-red-500/30 bg-red-500/5 p-3">
              <div className="small font-bold text-red-400">Blocking checks</div>
              <ul className="mt-1 list-disc pl-4 text-[11px] text-slate-300">
                {failures.map((f, i) => (
                  <li key={`${f.stage}-${i}`}>
                    <span className="inline-mono">{f.stage}</span>:{" "}
                    <span className="inline-mono">{f.code || "FAIL"}</span>
                    {f.remedy ? <span className="muted"> — {f.remedy}</span> : null}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}

          <div className="mt-3 flex items-center gap-2">
            <button
              type="button"
              className="btn"
              disabled={busy}
              onClick={() => void onInstall()}
              title="Explicit opt-in: installs pinned python/torch for the chosen backend"
            >
              {installing ? "Installing…" : "Install training stack"}
            </button>
            <span className="small muted">
              Explicit opt-in (pinned variants). Training never auto-starts from here.
            </span>
          </div>
        </section>

        {/* ------------------------------------------------ official model card */}
        <section className="panel">
          <h3 className="text-sm font-bold text-cyan-400">Official Model</h3>
          <p className="small muted mt-1">
            Download and verify the published model bundle. Nothing is installed unless every
            verification check passes.
          </p>
          {status?.slot ? (
            <dl className="kv mt-3">
              {Object.entries(status.slot).slice(0, 6).map(([k, v]) => (
                <CheckRow key={k} label={k} ok={isOkValue(v)} note={fmtValue(v)} />
              ))}
            </dl>
          ) : (
            <EmptyState message="No slot state yet." hint="The recommended action appears in the header once known." />
          )}
          <div className="mt-3">
            <button type="button" className="btn" disabled={busy} onClick={() => void onOfficial()}>
              {officialBusy ? "Downloading…" : "Download & verify official model"}
            </button>
          </div>
        </section>
      </div>

      {/* ---------------------------------------------------------- train card */}
      <section className="panel">
        <h3 className="text-sm font-bold text-cyan-400">Local Training Run</h3>
        <p className="small muted mt-1">
          Train a model from an imported file or from broker-borrowed history. Validation is
          server side; broker source requires an explicit candle count.
        </p>

        <div className="mt-3 flex flex-wrap items-center gap-3">
          <Segmented
            options={TRAIN_SOURCES.map((s) => ({ id: s, label: s === "file" ? "Import file" : "Broker history" }))}
            value={source}
            onChange={setSource}
          />
          <Segmented
            options={TRAIN_BACKENDS.map((b) => ({ id: b, label: b.toUpperCase() }))}
            value={backend}
            onChange={setBackend}
          />
        </div>

        {source === "file" ? (
          <div className="mt-3">
            <label className="small muted" htmlFor="prov-file">
              Dataset file (CSV/Parquet) — must be inside an allowed import root
            </label>
            <input
              id="prov-file"
              type="text"
              className="input mt-1 w-full"
              placeholder="e.g. data/XAUUSD_M1.csv"
              value={filePath}
              onChange={(e) => setFilePath(e.target.value)}
              disabled={busy}
            />
            {allowedRoots.length > 0 ? (
              <div className="small muted mt-1">
                Allowed roots:{" "}
                {allowedRoots.map((r, i) => (
                  <span key={r} className="inline-mono">
                    {r}
                    {i < allowedRoots.length - 1 ? ", " : ""}
                  </span>
                ))}
              </div>
            ) : null}
          </div>
        ) : (
          <div className="mt-3">
            <label className="small muted">Candles (most recent N bars — chronological tail)</label>
            <div className="mt-1">
              <Segmented options={CANDLE_OPTIONS} value={candles} onChange={setCandles} />
            </div>
          </div>
        )}

        <div className="mt-3 flex flex-wrap items-end gap-4">
          <label className="small muted">
            Folds
            <input
              type="number"
              className="input mt-1 w-24"
              min={1}
              max={1000}
              value={folds}
              onChange={(e) => setFolds(Number(e.target.value) || 0)}
              disabled={busy}
            />
          </label>
          <label className="small muted">
            Epochs
            <input
              type="number"
              className="input mt-1 w-24"
              min={1}
              max={1000}
              value={epochs}
              onChange={(e) => setEpochs(Number(e.target.value) || 0)}
              disabled={busy}
            />
          </label>
          <label className="small muted flex items-center gap-2">
            <input
              type="checkbox"
              checked={prepareEnvironment}
              onChange={(e) => setPrepareEnvironment(e.target.checked)}
              disabled={busy}
            />
            Prepare environment if not ready (explicit consent)
          </label>
        </div>

        <div className="mt-4 flex items-center gap-2">
          <button
            type="button"
            className="btn primary"
            disabled={busy || (source === "file" && !filePath)}
            onClick={() => void onTrain()}
          >
            {training ? "Training…" : "Start training run"}
          </button>
          <button type="button" className="btn" disabled={!training} onClick={() => void onCancel()}>
            Cancel run
          </button>
        </div>

        {events.length > 0 ? (
          <div className="mt-4">
            <div className="small font-bold text-slate-300">Progress</div>
            <pre
              className="mt-1 max-h-[260px] overflow-auto rounded border border-borderClr/40 bg-darkBg/60 p-3 text-[10px] font-mono text-slate-300"
              aria-live="polite"
            >
              {events
                .map((e) => `[${e.stage}] ${String(e.status)} — ${String(e.message ?? "")}`)
                .join("\n")}
            </pre>
          </div>
        ) : null}

        {result ? (
          <div className="mt-3 rounded border border-cyan-500/30 bg-cyan-500/5 p-3">
            <div className="small font-bold text-cyan-400">Run result</div>
            <pre className="mt-1 overflow-auto text-[10px] font-mono text-slate-300">
              {JSON.stringify(result, null, 2)}
            </pre>
          </div>
        ) : null}
      </section>
    </div>
  );
}

/** Render an error: the backend `detail` string when present, else the typed code. */
function errText(err: unknown): string {
  const e = err as { detail?: unknown; message?: string; code?: string };
  if (e?.detail) return String(e.detail);
  if (e?.message) return e.message;
  if (e?.code) return e.code;
  return String(err);
}

/** Pull the typed failure code out of a resolved backend body. */
function codeOf(body: unknown): string {
  const b = body as { code?: string; message?: string; error?: { code?: string; message?: string } };
  const nested = b?.error;
  return String((nested?.code ?? b?.code ?? b?.message ?? "UNKNOWN_ERROR"));
}

function isOkValue(v: unknown): boolean {
  if (typeof v === "boolean") return v;
  if (v === null || v === undefined) return false;
  return String(v).toUpperCase() === "OK";
}

function fmtValue(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}
