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
 *
 * Presentation: shared Panel/Segmented primitives + ./provisioning.css
 * (.pv-* namespace, theme tokens only).
 */

import { useCallback, useEffect, useRef, useState } from "react";

import type { ShellPageProps } from "@/app/featureModule";
import { EmptyState, ErrorState, LoadingState, Panel, Segmented } from "@/components/primitives";
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
import { CANDLE_OPTIONS, classNames, codeOf, errText, fmtValue, isOkValue } from "./kit";
import "./provisioning.css";

const POLL_MS = 2000;


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
    <div className="kv-row">
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
    <div className="pv-page">
      {/* ------------------------------------------------------------- header */}
      <Panel
        title="First-Run Model Preparation"
        accent
        right={
          <span className={classNames("badge", trainingReady ? "good" : report ? "warn" : "unknown")}>
            {report ? (trainingReady ? "ENV READY" : "ENV NOT READY") : "ENV —"}
          </span>
        }
      >
        <p className="small muted">
          Prepare a servable model before the engine runs. Environment check is discovery only —
          installing is an explicit, consented action. One local training run at a time.
        </p>
        {recommended ? (
          <div className="banner info pv-banner" role="status">
            Recommended next action: <span className="inline-mono">{String(recommended)}</span>
          </div>
        ) : null}
      </Panel>

      {error ? <ErrorState message={error} onRetry={() => setError("")} /> : null}
      {notice ? (
        <div className="banner good pv-banner" role="status">
          {notice}
        </div>
      ) : null}

      <div className="pv-grid">
        {/* ------------------------------------------------ environment card */}
        <Panel
          title="Training Environment"
          right={
            <Segmented
              options={TRAIN_BACKENDS.map((b) => ({ id: b, label: b.toUpperCase() }))}
              value={backend}
              onChange={setBackend}
            />
          }
        >
          <p className="small muted">
            Backend <span className="inline-mono">{backend}</span> — discovery only, nothing is installed by this check.
          </p>

          {env ? (
            <dl className="kv pv-list">
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
            <div className="pv-callout bad">
              <div className="head">Blocking checks</div>
              <ul>
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

          <div className="pv-toolbar">
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
        </Panel>

        {/* ------------------------------------------------ official model card */}
        <Panel title="Official Model">
          <p className="small muted">
            Download and verify the published model bundle. Nothing is installed unless every
            verification check passes.
          </p>
          {status?.slot ? (
            <dl className="kv pv-list">
              {Object.entries(status.slot).slice(0, 6).map(([k, v]) => (
                <CheckRow key={k} label={k} ok={isOkValue(v)} note={fmtValue(v)} />
              ))}
            </dl>
          ) : (
            <EmptyState message="No slot state yet." hint="The recommended action appears in the header once known." />
          )}
          <div className="pv-toolbar">
            <button type="button" className="btn" disabled={busy} onClick={() => void onOfficial()}>
              {officialBusy ? "Downloading…" : "Download & verify official model"}
            </button>
          </div>
        </Panel>
      </div>

      {/* ---------------------------------------------------------- train card */}
      <Panel title="Local Training Run">
        <p className="small muted">
          Train a model from an imported file or from broker-borrowed history. Validation is
          server side; broker source requires an explicit candle count.
        </p>

        <div className="pv-toolbar">
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
          <div className="pv-field">
            <label className="small muted" htmlFor="prov-file">
              Dataset file (CSV/Parquet) — must be inside an allowed import root
            </label>
            <input
              id="prov-file"
              type="text"
              className="input pv-file"
              placeholder="e.g. data/XAUUSD_M1.csv"
              value={filePath}
              onChange={(e) => setFilePath(e.target.value)}
              disabled={busy}
            />
            {allowedRoots.length > 0 ? (
              <div className="small muted">
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
          <div className="pv-field">
            <label className="small muted">Candles (most recent N bars — chronological tail)</label>
            <div>
              <Segmented options={CANDLE_OPTIONS} value={candles} onChange={setCandles} />
            </div>
          </div>
        )}

        <div className="pv-toolbar end">
          <label className="small muted">
            Folds
            <input
              type="number"
              className="input pv-num"
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
              className="input pv-num"
              min={1}
              max={1000}
              value={epochs}
              onChange={(e) => setEpochs(Number(e.target.value) || 0)}
              disabled={busy}
            />
          </label>
          <label className="pv-consent">
            <input
              type="checkbox"
              checked={prepareEnvironment}
              onChange={(e) => setPrepareEnvironment(e.target.checked)}
              disabled={busy}
            />
            Prepare environment if not ready (explicit consent)
          </label>
        </div>

        <div className="pv-toolbar">
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
          <div className="pv-field">
            <div className="small faint uppercase font-bold">Progress</div>
            <pre tabIndex={0} className="pv-log" aria-live="polite">
              {events
                .map((e) => `[${e.stage}] ${String(e.status)} — ${String(e.message ?? "")}`)
                .join("\n")}
            </pre>
          </div>
        ) : null}

        {result ? (
          <div className="pv-callout ok">
            <div className="head">Run result</div>
            <pre tabIndex={0} className="pv-json">{JSON.stringify(result, null, 2)}</pre>
          </div>
        ) : null}
      </Panel>
    </div>
  );
}

