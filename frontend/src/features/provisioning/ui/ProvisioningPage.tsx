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

import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";

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
import { CANDLE_OPTIONS, classNames, codeOf, errText, failLine, fmtValue, isOkValue, requestIdOf } from "./kit";
import "./provisioning.css";

const POLL_MS = 2000;


/** The ok===false subset of the report checklist (server `failing()`). */
function failingChecks(report: EnvironmentReport | undefined | null): EnvCheck[] {
  if (!report) return [];
  const raw = report.checks;
  return Array.isArray(raw) ? raw.filter((c) => !c.ok) : [];
}

/** One line of the environment checklist (memoized: keystrokes in the train
 *  form below no longer re-render the whole checklist). */
const CheckRow = memo(function CheckRow({
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
});

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
  const [errorRid, setErrorRid] = useState<string | null>(null);
  const [notice, setNotice] = useState<string>("");
  // 4-state ladder for the two discovery reads: each keeps its own loading /
  // error rung instead of funneling everything into one banner + eternal
  // spinner.
  const [statusLoading, setStatusLoading] = useState<boolean>(true);
  const [statusError, setStatusError] = useState<string>("");
  const [statusRid, setStatusRid] = useState<string | null>(null);
  const [statusAt, setStatusAt] = useState<number>(0);
  const [envLoading, setEnvLoading] = useState<boolean>(true);
  const [envError, setEnvError] = useState<string>("");
  const [envRid, setEnvRid] = useState<string | null>(null);
  const [envAt, setEnvAt] = useState<number>(0);
  const seenSeq = useRef<number>(0);
  const statusCtl = useRef<AbortController | null>(null);
  const envCtl = useRef<AbortController | null>(null);

  const refreshStatus = useCallback(async () => {
    // Abort any identical in-flight status load, then fetch with the signal
    // so an unmount/re-run never leaves a zombie request writing state.
    statusCtl.current?.abort();
    const ac = new AbortController();
    statusCtl.current = ac;
    setStatusLoading(true);
    setStatusError("");
    setStatusRid(null);
    try {
      const s = await provisioningApi.status(ac.signal);
      if (ac.signal.aborted) return;
      if (s.success === false) {
        // In-band legacy failure — an honest error rung, verbatim code/message.
        setStatusError(failLine(s));
        setStatusRid(null);
      } else {
        setStatus(s);
        setStatusAt(Date.now());
      }
    } catch (err) {
      if (ac.signal.aborted) return;
      setStatusError(errText(err));
      setStatusRid(requestIdOf(err));
    } finally {
      if (!ac.signal.aborted) setStatusLoading(false);
    }
  }, []);

  const refreshEnv = useCallback(async (be: TrainBackend) => {
    envCtl.current?.abort();
    const ac = new AbortController();
    envCtl.current = ac;
    setEnvLoading(true);
    setEnvError("");
    setEnvRid(null);
    try {
      const e = await provisioningApi.environment(be, ac.signal);
      if (ac.signal.aborted) return;
      if (e.success === false) {
        setEnvError(failLine(e));
        setEnvRid(null);
      } else {
        setEnv(e);
        setEnvAt(Date.now());
        setError("");
        setErrorRid(null);
      }
    } catch (err) {
      if (ac.signal.aborted) return;
      setEnvError(errText(err));
      setEnvRid(requestIdOf(err));
    } finally {
      if (!ac.signal.aborted) setEnvLoading(false);
    }
  }, []);

  // Unmount cleanup: abort both discovery reads (the training-tail effect and
  // the status background refresh below abort their own controllers).
  useEffect(() => () => {
    statusCtl.current?.abort();
    envCtl.current?.abort();
  }, []);

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
    const ac = new AbortController();
    const tick = async () => {
      try {
        const p = await provisioningApi.trainProgress(seenSeq.current, ac.signal);
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
        if (cancelled) return;
        setError(errText(err));
        setErrorRid(requestIdOf(err));
      }
    };
    void tick();
    // Bounded poll: 2s only while a run is active; cleanup cancels the
    // interval AND aborts the in-flight progress request on unmount/stop.
    const id = window.setInterval(tick, POLL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(id);
      ac.abort();
    };
  }, [training]);

  const onInstall = useCallback(async () => {
    setInstalling(true);
    setError("");
    setErrorRid(null);
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
      setErrorRid(requestIdOf(err));
    } finally {
      setInstalling(false);
    }
  }, [backend, refreshEnv]);

  const onOfficial = useCallback(async () => {
    setOfficialBusy(true);
    setError("");
    setErrorRid(null);
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
      setErrorRid(requestIdOf(err));
    } finally {
      setOfficialBusy(false);
    }
  }, [refreshStatus]);

  const onTrain = useCallback(async () => {
    setError("");
    setErrorRid(null);
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
      setErrorRid(requestIdOf(err));
    }
  }, [source, filePath, candles, folds, epochs, backend, prepareEnvironment]);

  const onCancel = useCallback(async () => {
    setError("");
    setErrorRid(null);
    try {
      const out = await provisioningApi.trainCancel();
      setNotice(out.cancel === "REQUESTED" ? "Cancel requested — observed at the next epoch boundary." : "No active run to cancel.");
    } catch (err) {
      setError(errText(err));
      setErrorRid(requestIdOf(err));
    }
  }, []);

  const busy = installing || officialBusy || training;
  const report = env?.report;
  const failures = useMemo(() => failingChecks(report), [report]);
  // The progress <pre> re-joins every event on each keystroke below — memoize
  // the join so train-form typing costs O(1) instead of O(events).
  const eventsLog = useMemo(
    () => events.map((e) => `[${e.stage}] ${String(e.status)} — ${String(e.message ?? "")}`).join("\n"),
    [events],
  );
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

      {error ? (
        <ErrorState
          message={error}
          requestId={errorRid}
          onRetry={() => {
            setError("");
            setErrorRid(null);
          }}
        />
      ) : null}
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

          {envLoading ? (
            <LoadingState label="Resolving environment…" />
          ) : envError ? (
            <ErrorState
              message={envError}
              requestId={envRid}
              onRetry={() => void refreshEnv(backend)}
            />
          ) : env && (env.environment || env.report) ? (
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
            <EmptyState
              message="No environment report returned."
              hint={`GET /api/provisioning/environment?backend=${backend} answered successfully but carried no environment/report payload.`}
            />
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

          <div className="small muted">
            last fetched {envAt ? new Date(envAt).toLocaleTimeString() : "—"} · GET
            /api/provisioning/environment (legacy endpoint returns no freshness field — staleness is
            not claimed)
          </div>
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
          {statusLoading ? (
            <LoadingState label="Resolving slot state…" />
          ) : statusError ? (
            <ErrorState message={statusError} requestId={statusRid} onRetry={() => void refreshStatus()} />
          ) : status?.slot ? (
            <dl className="kv pv-list">
              {Object.entries(status.slot).slice(0, 6).map(([k, v]) => (
                <CheckRow key={k} label={k} ok={isOkValue(v)} note={fmtValue(v)} />
              ))}
            </dl>
          ) : (
            <EmptyState
              message="No slot state yet."
              hint="GET /api/provisioning/status answered without a model slot — the recommended action appears in the header once the backend reports one."
            />
          )}
          <div className="small muted">
            last fetched {statusAt ? new Date(statusAt).toLocaleTimeString() : "—"} · GET
            /api/provisioning/status (legacy endpoint returns no freshness field — staleness is not
            claimed)
          </div>
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
              {eventsLog}
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

