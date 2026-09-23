/**
 * features/position-adviser/ui/PositionAdviserPage.tsx
 *
 * Layer-2 Position Decision Adviser (TASK-POSA-001).
 *
 * What this panel IS: the operator surface for an OPTIONAL, opt-in adviser that
 * can only ever make a keep/close verdict MORE conservative. It shows live
 * activation state, the prerequisite checks, the trained models with honest OOS
 * metrics, and a live feed of advisories.
 *
 * What this panel NEVER does: open/size/extend a position, or present an
 * activation as safe when a prerequisite check has not passed. LIVE is gated
 * behind real broker/position probes; a check that could not run is reported as
 * NOT PASSED, never silently green.
 *
 * Presentation only: every value on screen is backend-authoritative (status
 * words, error `detail` strings, OOS metrics). Styled via ./position-adviser.css
 * on the shared theme tokens — no CSS framework (BUG-047 lineage).
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import type { ShellPageProps } from "@/app/featureModule";
import { ErrorState, LoadingState } from "@/components/primitives";

import { positionAdviserApi } from "../api";
import type {
  ActivationCheckDto,
  ActivationChecksResponse,
  AdviserActivation,
  AdviserAutoTuneResponse,
  AdviserDatasetDto,
  AdviserModelDto,
  AdviserStatusResponse,
  AdviserAdvisoryDto,
} from "../model";
import { ACTIVATION_HELP, ACTIVATION_LADDER } from "../model";
import "./position-adviser.css";
import {
  AdvisoryRow,
  CheckItem,
  MAJORITY_BASELINE_FALLBACK,
  ModelCard,
  TrialRow,
  classNames,
  fmt,
  fmtTime,
} from "./PositionAdviserRows";

const REFRESH_MS = 3000;

/** Per-rung state class for the big activation word + ladder highlight. */
function stateClass(a: string): string {
  if (a === "LIVE") return "live";
  if (a === "PAPER") return "paper";
  return "disabled";
}

/** Relative position on the ladder — rungs below the current one are "done". */
function ladderIndex(a: string): number {
  const i = ACTIVATION_LADDER.indexOf(a as AdviserActivation);
  return i < 0 ? 0 : i;
}

/** Failure text of a legacy transport error — backend `detail` verbatim. */
function errDetail(err: unknown): string {
  const detail = (err as { detail?: string })?.detail ?? String(err);
  return typeof detail === "string" ? detail : JSON.stringify(detail);
}

/** Correlation id (ApiError.request_id / raw request_id) for ErrorState. */
function errRid(err: unknown): string | null {
  const e = err as { requestId?: unknown; request_id?: unknown };
  const rid = e?.requestId ?? e?.request_id;
  return typeof rid === "string" && rid !== "" ? rid : null;
}

export default function PositionAdviserPage(_props: ShellPageProps) {
  const [status, setStatus] = useState<AdviserStatusResponse | null>(null);
  const [models, setModels] = useState<AdviserModelDto[]>([]);
  const [activeModelId, setActiveModelId] = useState<string>("");
  const [advisories, setAdvisories] = useState<AdviserAdvisoryDto[]>([]);
  const [datasets, setDatasets] = useState<AdviserDatasetDto[]>([]);
  const [checks, setChecks] = useState<ActivationCheckDto[]>([]);
  const [checksAllPassed, setChecksAllPassed] = useState<boolean>(false);
  const [busy, setBusy] = useState<boolean>(false);
  const [error, setError] = useState<string>("");
  const [errorRid, setErrorRid] = useState<string | null>(null);
  const [notice, setNotice] = useState<string>("");
  const [loading, setLoading] = useState<boolean>(true);
  // Read rung (the 3s poll over status/models/advisories/datasets): its own
  // error + request_id + last-success timestamp, separate from action errors.
  const [readError, setReadError] = useState<string>("");
  const [readRid, setReadRid] = useState<string | null>(null);
  const [readAt, setReadAt] = useState<number>(0);
  const readCtl = useRef<AbortController | null>(null);

  // The generator writes pos_ds_*.parquet to artifacts/datasets; the operator
  // picks one here instead of a hardcoded path, so a newly generated M1
  // dataset is immediately trainable.
  const [trainDataset, setTrainDataset] = useState<string>("");
  const [trainEpochs, setTrainEpochs] = useState<number>(12);

  // Auto-tune (auto mode): a bounded grid sweep. Fine-tuning by hand stays
  // available — these are the exact knobs the sweep ranges over.
  const [tuneEpochs, setTuneEpochs] = useState<number>(12);
  const [tuneMaxTrials, setTuneMaxTrials] = useState<number>(6);
  const [tuneLrs, setTuneLrs] = useState<string>("5e-4, 1e-3, 2e-3");
  const [tuneBatchSizes, setTuneBatchSizes] = useState<string>("64, 128, 256");
  const [tuneSeeds, setTuneSeeds] = useState<string>("42, 1337, 2024");
  const [tuneResult, setTuneResult] = useState<AdviserAutoTuneResponse | null>(null);

  const effectiveDataset = useMemo(
    () => trainDataset || (datasets[0]?.path ?? ""),
    [trainDataset, datasets],
  );

  const refresh = useCallback(async () => {
    // Abort-then-start: an interval tick or an action-triggered refresh while
    // the previous poll is still in flight would otherwise run an identical
    // second copy of all four queries concurrently.
    readCtl.current?.abort();
    const ac = new AbortController();
    readCtl.current = ac;
    try {
      const [s, m, a, ds] = await Promise.all([
        positionAdviserApi.status(ac.signal),
        positionAdviserApi.models(ac.signal),
        positionAdviserApi.advisories(30, ac.signal),
        positionAdviserApi.datasets(ac.signal),
      ]);
      if (ac.signal.aborted) return;
      setStatus(s);
      setModels(m.models || []);
      setActiveModelId(m.active_adviser_id || "");
      setAdvisories(a.advisories || []);
      setDatasets(ds.datasets || []);
      setReadError("");
      setReadRid(null);
      setReadAt(Date.now());
    } catch (err) {
      // The backend stays the source of truth; show its message verbatim.
      if (ac.signal.aborted) return;
      setReadError(errDetail(err));
      setReadRid(errRid(err));
    } finally {
      if (!ac.signal.aborted) setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
    // Bounded poll: 3s, skipped while the document is hidden (the next tick
    // resumes when visible); cleanup clears the interval AND aborts whatever
    // read is in flight on unmount.
    const id = window.setInterval(() => {
      if (document.hidden) return;
      void refresh();
    }, REFRESH_MS);
    return () => {
      window.clearInterval(id);
      readCtl.current?.abort();
    };
  }, [refresh]);

  const runChecks = useCallback(async () => {
    setBusy(true);
    setError(""); setErrorRid(null);
    try {
      const res: ActivationChecksResponse = await positionAdviserApi.runChecks();
      setChecks(res.checks || []);
      setChecksAllPassed(Boolean(res.all_passed));
      if (!res.all_passed) {
        setNotice("One or more prerequisite checks failed or could not run — LIVE stays unavailable.");
      } else {
        setNotice("All prerequisite checks passed. LIVE activation is available.");
      }
    } catch (err) {
      setError(errDetail(err));
      setErrorRid(errRid(err));
    } finally {
      setBusy(false);
    }
  }, []);

  const onActivate = useCallback(
    async (target: AdviserActivation) => {
      setBusy(true);
      setError(""); setErrorRid(null);
      setNotice("");
      try {
        // LIVE requires the prerequisite checks; run them first if we have none.
        let supplied = checks;
        if (target === "LIVE" && supplied.length === 0) {
          const res = await positionAdviserApi.runChecks();
          supplied = res.checks || [];
          setChecks(supplied);
          setChecksAllPassed(Boolean(res.all_passed));
        }
        const payload =
          target === "LIVE"
            ? { activation: target, checks: supplied.map((c) => ({ ...c, evidence: c.evidence ?? {} })) }
            : { activation: target };
        const out = await positionAdviserApi.activate(payload);
        setNotice(out.message || `activation set to ${target}`);
        await refresh();
      } catch (err) {
        setError(errDetail(err));
        setErrorRid(errRid(err));
      } finally {
        setBusy(false);
      }
    },
    [checks, refresh],
  );

  const onUnload = useCallback(async () => {
    setBusy(true);
    setError(""); setErrorRid(null);
    setNotice("");
    try {
      const out = await positionAdviserApi.unload();
      setNotice(out.message || "adviser unloaded; activation reset to DISABLED");
      await refresh();
    } catch (err) {
      setError(errDetail(err));
      setErrorRid(errRid(err));
    } finally {
      setBusy(false);
    }
  }, [refresh]);

  const onTrain = useCallback(async () => {
    if (!effectiveDataset) {
      setError("No position dataset available. Generate one in the Neural Studio first.");
      setErrorRid(null);
      return;
    }
    setBusy(true);
    setError(""); setErrorRid(null);
    setNotice("");
    try {
      const out = await positionAdviserApi.train({
        dataset_path: effectiveDataset,
        epochs: trainEpochs,
      });
      setNotice(out.message || "training complete");
      await refresh();
    } catch (err) {
      setError(errDetail(err));
      setErrorRid(errRid(err));
    } finally {
      setBusy(false);
    }
  }, [effectiveDataset, trainEpochs, refresh]);

  const onAutoTune = useCallback(async () => {
    if (!effectiveDataset) {
      setError("No position dataset available. Generate one in the Neural Studio first.");
      setErrorRid(null);
      return;
    }
    // Parse the comma-separated knob strings; malformed input fails loud here
    // rather than reaching the server.
    const parseNums = <T,>(raw: string, cast: (v: string) => T): T[] =>
      raw
        .split(",")
        .map((p) => p.trim())
        .filter((p) => p.length > 0)
        .map(cast);

    let lrs: number[];
    let bss: number[];
    let seeds: number[];
    try {
      lrs = parseNums(tuneLrs, Number);
      bss = parseNums(tuneBatchSizes, (v) => parseInt(v, 10));
      seeds = parseNums(tuneSeeds, (v) => parseInt(v, 10));
    } catch (err) {
      setError(`invalid auto-tune parameter: ${err}`);
      setErrorRid(null);
      return;
    }
    if (lrs.length === 0 || bss.length === 0 || seeds.length === 0) {
      setError("auto-tune needs at least one learning rate, batch size, and seed.");
      setErrorRid(null);
      return;
    }

    setBusy(true);
    setError(""); setErrorRid(null);
    setNotice("");
    setTuneResult(null);
    try {
      const out = await positionAdviserApi.autoTune({
        dataset_path: effectiveDataset,
        epochs: tuneEpochs,
        seeds,
        learning_rates: lrs,
        batch_sizes: bss,
        max_trials: tuneMaxTrials,
        auto_load: true,
      });
      setTuneResult(out);
      const baseline = out.majority_baseline_accuracy;
      setNotice(
        `${out.message}` +
          (baseline != null
            ? ` (majority-class baseline ${baseline.toFixed(4)} — ${out.beats_majority_baseline ? "beats" : "does NOT beat"} it)`
            : ""),
      );
      await refresh();
    } catch (err) {
      setError(errDetail(err));
      setErrorRid(errRid(err));
    } finally {
      setBusy(false);
    }
  }, [effectiveDataset, tuneEpochs, tuneMaxTrials, tuneLrs, tuneBatchSizes, tuneSeeds, refresh]);

  const onLoad = useCallback(
    async (m: AdviserModelDto) => {
      setBusy(true);
      setError(""); setErrorRid(null);
      setNotice("");
      try {
        if (!m.has_scaler) {
          setError("This checkpoint has no companion scaler sidecar; load refused.");
          return;
        }
        const out = await positionAdviserApi.load({
          weights_path: m.weights_path,
          scaler_path: m.scaler_path,
          model_id: m.model_id,
        });
        setNotice(out.message || "adviser loaded; activation is still DISABLED until you enable it");
        await refresh();
      } catch (err) {
        setError(errDetail(err));
        setErrorRid(errRid(err));
      } finally {
        setBusy(false);
      }
    },
    [refresh],
  );

  const activation = (status?.activation as string) || "DISABLED";
  const liveAvailable = checksAllPassed && Boolean(status?.ready);
  const curIdx = ladderIndex(activation);
  const tuneBaseline = tuneResult?.majority_baseline_accuracy ?? null;
  const baselineRef = tuneBaseline ?? MAJORITY_BASELINE_FALLBACK;

  // First load failed and NOTHING was ever fetched: the panels below would
  // otherwise render empty rungs ("no checkpoints yet") that lie about an
  // error. Show the failure with request_id + Retry instead.
  const neverFetched = !loading && readAt === 0 && readError !== "";
  if (neverFetched) {
    return (
      <div className="pa-page">
        <ErrorState message={readError} requestId={readRid} onRetry={() => void refresh()} />
      </div>
    );
  }

  return (
    <div className="pa-page">
      {/* ---------------------------------------------------------- header */}
      <header className="pa-hero" aria-label="Position adviser">
        <div className="pa-hero-left">
          <span className="pa-scales-icon" aria-hidden="true">
            ⚖
          </span>
          <div className="pa-title-wrap">
            <h2 className="pa-title">
              Layer-2 Position Decision Adviser
              <span className={classNames("pa-state", stateClass(activation))} title="current activation rung">
                <span className="pa-state-dot" aria-hidden="true" />
                {activation}
              </span>
            </h2>
            <p className="pa-title-subtitle">
              Optional, opt-in. When enabled it can only make a keep/close verdict MORE conservative —
              it never opens, sizes, extends, or weakens a position. Default is DISABLED, so the decide
              system runs exactly as before.
            </p>
          </div>
        </div>
        <div className="pa-hero-right">
          <div className="pa-rung-help">{ACTIVATION_HELP[activation as AdviserActivation]}</div>
          {status?.last_error ? (
            <div className="pa-notice err" role="alert">
              <span className="pa-notice-glyph">!</span>
              <span>last adviser error: {String(status.last_error)}</span>
            </div>
          ) : null}
        </div>
      </header>

      {/* Stale rung: backend freshness only — last successful client fetch +
          the backend clock when the status payload carries one. The legacy
          endpoints send no staleness verdict, so none is claimed. */}
      <div className="pa-freshness" role="status">
        {readAt > 0 ? `updated ${new Date(readAt).toLocaleTimeString()}` : "not fetched yet"}
        {status?.now ? ` · backend clock ${fmtTime(status.now)}` : ""}
        {` · GET status/models/advisories/datasets every ${REFRESH_MS / 1000}s while this tab is open (no backend staleness field)`}
      </div>

      {readError && readAt > 0 ? (
        <ErrorState
          message={readError}
          requestId={readRid}
          onRetry={() => void refresh()}
        />
      ) : null}
      {error ? (
        <div className="pa-notice err" role="alert">
          <span className="pa-notice-glyph">✕</span>
          <span>
            {error}
            {errorRid ? <span className="pa-mono"> · request_id {errorRid}</span> : null}
          </span>
        </div>
      ) : null}
      {notice ? (
        <div className="pa-notice ok" role="status">
          <span className="pa-notice-glyph">✓</span>
          <span>{notice}</span>
        </div>
      ) : null}

      {/* ------------------------------------------------- activation ladder */}
      <section className="pa-panel">
        <div className="pa-panel-head">
          <span className="pa-dot" aria-hidden="true" />
          <span className="pa-panel-title">Activation Ladder</span>
          <span className="pa-panel-sub">
            Each step must be verified with real broker/position checks before the next becomes
            selectable. A LIVE activation that skips the checks is refused by the server.
          </span>
        </div>
        <div className="pa-panel-body">
          <div className="pa-ladder">
            {ACTIVATION_LADDER.map((a, i) => {
              const isCurrent = a === activation;
              const isDone = !isCurrent && i < curIdx;
              const disabled =
                busy ||
                isCurrent ||
                (a === "LIVE" && !liveAvailable) ||
                (a === "PAPER" && !status?.ready);
              const needsChecks = a === "LIVE" && !liveAvailable;
              return (
                <button
                  key={a}
                  type="button"
                  data-step={i + 1}
                  disabled={disabled}
                  onClick={() => void onActivate(a)}
                  className={classNames(
                    "pa-step",
                    isCurrent && "current",
                    isDone && "done",
                    a === "LIVE" && liveAvailable && "live-ready",
                  )}
                  aria-pressed={isCurrent}
                >
                  <span className="pa-step-name">{a}</span>
                  <span className="pa-step-hint">{ACTIVATION_HELP[a].split(".")[0]}.</span>
                  {needsChecks ? (
                    <span className="pa-step-note">checks required</span>
                  ) : isCurrent ? (
                    <span className="pa-step-note">current</span>
                  ) : null}
                </button>
              );
            })}
          </div>
          <div className="pa-actions">
            <button
              type="button"
              disabled={busy}
              onClick={() => void runChecks()}
              className="pa-btn pa-btn-ghost"
            >
              {busy ? "Running…" : "Run Broker & Position Checks"}
            </button>
            {status && Boolean(status.model_id) && activation !== "DISABLED" ? (
              <button
                type="button"
                disabled={busy}
                onClick={() => void onUnload()}
                className="pa-btn pa-btn-danger"
                title="unload the in-memory adviser and reset activation to DISABLED"
              >
                Unload & Disable
              </button>
            ) : null}
          </div>
          {checks.length > 0 ? (
            <ul className="pa-checks">
              {checks.map((c) => (
                <CheckItem key={c.name} c={c} />
              ))}
            </ul>
          ) : null}
        </div>
      </section>

      {/* ----------------------------------------------------- live counters */}
      <div className="pa-metrics">
        <div className="pa-metric">
          <div className="k">Loaded model</div>
          <div className={classNames("v", status?.model_id ? "" : "dim")}>
            {status?.model_id || "NONE"}
          </div>
          <div className="s">weights sha {status?.weights_sha256 ? status.weights_sha256.slice(0, 12) : "—"}</div>
        </div>
        <div className="pa-metric">
          <div className="k">Feature dim</div>
          <div className="v">{status?.feature_dim != null ? String(status.feature_dim) : "--"}</div>
          <div className="s">input width</div>
        </div>
        <div className="pa-metric">
          <div className="k">Applied</div>
          <div className={classNames("v", (status?.applied_count ?? 0) > 0 ? "good" : "dim")}>
            {String(status?.applied_count ?? 0)}
          </div>
          <div className="s">hold-score penalties</div>
        </div>
        <div className="pa-metric">
          <div className="k">Evaluated</div>
          <div className="v">{String(status?.evaluated_count ?? 0)}</div>
          <div className="s">refused: {String(status?.refused_count ?? 0)}</div>
        </div>
      </div>

      {loading ? (
        <LoadingState label="Loading position adviser status, models and advisories…" />
      ) : (
        <div className="pa-split">
          {/* ------------------------------------------------------ training */}
          <section className="pa-panel">
            <div className="pa-panel-head">
              <span className="pa-dot" aria-hidden="true" />
              <span className="pa-panel-title">Build / Fine-Tune an Adviser</span>
              <span className="pa-panel-sub">
                Trains on a generated Position dataset (KEEP / CLOSE / REDUCE labels). OOS accuracy is
                measured on a held-out split that the trainer never fits — a model below the
                majority-class baseline is reported as such, never hidden.
              </span>
            </div>
            <div className="pa-panel-body">
              <div className="pa-form">
                <div className="pa-field">
                  <label htmlFor="pa-train-dataset">Position dataset</label>
                  {datasets.length > 0 ? (
                    <select
                      id="pa-train-dataset"
                      value={trainDataset}
                      onChange={(e) => setTrainDataset(e.target.value)}
                      className="pa-select"
                    >
                      {datasets.map((d) => (
                        <option key={d.path} value={d.path}>
                          {d.name} {d.timeframe ? `(${d.timeframe})` : ""}
                        </option>
                      ))}
                    </select>
                  ) : (
                    <div className="pa-empty-input">
                      <span>∅</span>
                      <span>
                        No position datasets yet — generate one in the Neural Studio (M1/M5 source).
                        GET /api/position-adviser/datasets returned an empty list.
                      </span>
                    </div>
                  )}
                </div>
                <div className="pa-field-row">
                  <div className="pa-field narrow">
                    <label htmlFor="pa-train-epochs">Epochs</label>
                    <input
                      id="pa-train-epochs"
                      type="number"
                      min={1}
                      max={100}
                      value={trainEpochs}
                      onChange={(e) => setTrainEpochs(parseInt(e.target.value, 10) || 12)}
                      className="pa-input"
                    />
                  </div>
                  <button
                    type="button"
                    disabled={busy || !effectiveDataset}
                    onClick={() => void onTrain()}
                    className="pa-btn pa-btn-primary"
                  >
                    {busy ? "Training…" : "Train Adviser"}
                  </button>
                </div>
              </div>
            </div>
          </section>

          {/* ------------------------------------------------------ auto mode */}
          <section className="pa-panel">
            <div className="pa-panel-head">
              <span className="pa-dot violet" aria-hidden="true" />
              <span className="pa-panel-title">✦ Auto Mode — Find the Best Adviser</span>
              <span className="pa-panel-sub">
                Runs a bounded grid sweep over learning rate, batch size and seed, then keeps the model
                with the lowest <strong>out-of-sample loss</strong> — the only split the trainer never
                fits or early-stops on — and loads it. Losing checkpoints are pruned; the winner and its
                real OOS metrics are reported below. A sweep that cannot beat the majority-class
                baseline is shown as such, never hidden.
              </span>
            </div>
            <div className="pa-panel-body">
              <div className="pa-grid-4">
                <div className="pa-field">
                  <label htmlFor="pa-tune-epochs">Epochs / trial</label>
                  <input
                    id="pa-tune-epochs"
                    type="number"
                    min={1}
                    max={100}
                    value={tuneEpochs}
                    onChange={(e) => setTuneEpochs(parseInt(e.target.value, 10) || 12)}
                    className="pa-input"
                  />
                </div>
                <div className="pa-field">
                  <label htmlFor="pa-tune-trials">Max trials</label>
                  <input
                    id="pa-tune-trials"
                    type="number"
                    min={1}
                    max={60}
                    value={tuneMaxTrials}
                    onChange={(e) => setTuneMaxTrials(parseInt(e.target.value, 10) || 6)}
                    className="pa-input"
                  />
                </div>
                <div className="pa-field">
                  <label htmlFor="pa-tune-lrs">Learning rates</label>
                  <input
                    id="pa-tune-lrs"
                    type="text"
                    value={tuneLrs}
                    onChange={(e) => setTuneLrs(e.target.value)}
                    className="pa-input"
                  />
                </div>
                <div className="pa-field">
                  <label htmlFor="pa-tune-bs">Batch sizes</label>
                  <input
                    id="pa-tune-bs"
                    type="text"
                    value={tuneBatchSizes}
                    onChange={(e) => setTuneBatchSizes(e.target.value)}
                    className="pa-input"
                  />
                </div>
              </div>
              <div className="pa-field-row pa-tune-row">
                <div className="pa-field">
                  <label htmlFor="pa-tune-seeds">Seeds</label>
                  <input
                    id="pa-tune-seeds"
                    type="text"
                    value={tuneSeeds}
                    onChange={(e) => setTuneSeeds(e.target.value)}
                    className="pa-input"
                  />
                </div>
                <button
                  type="button"
                  disabled={busy || !effectiveDataset}
                  onClick={() => void onAutoTune()}
                  className="pa-btn pa-btn-violet"
                >
                  {busy ? "Sweeping…" : "▶ Auto-Tune & Load Best"}
                </button>
              </div>

              {tuneResult ? (
                <div className="pa-winner">
                  <div className="pa-winner-top">
                    <span className="pa-winner-label">Winner</span>
                    <span className="pa-winner-name">{String(tuneResult.best.model_id ?? "--")}</span>
                    {tuneResult.loaded ? (
                      <span className="badge good">LOADED</span>
                    ) : (
                      <span className="badge warn">NOT LOADED</span>
                    )}
                    {tuneResult.beats_majority_baseline ? (
                      <span className="badge good">BEATS BASELINE</span>
                    ) : (
                      <span className="badge bad">BELOW MAJORITY BASELINE</span>
                    )}
                  </div>
                  <div className="pa-facts">
                    <span className="pa-fact">
                      OOS loss <b>{fmt(tuneResult.best.oos_loss)}</b>
                    </span>
                    <span className="pa-fact">
                      OOS acc <b>{fmt(tuneResult.best.oos_accuracy)}</b>
                    </span>
                    <span className="pa-fact">
                      majority baseline <b>{fmt(tuneResult.majority_baseline_accuracy)}</b>
                    </span>
                    <span className="pa-fact">
                      lr <b>{String(tuneResult.best.learning_rate ?? "--")}</b> · bs{" "}
                      <b>{String(tuneResult.best.batch_size ?? "--")}</b> · seed{" "}
                      <b>{String(tuneResult.best.seed ?? "--")}</b>
                    </span>
                  </div>
                  <div tabIndex={0} className="pa-table-wrap">
                    <table className="pa-table">
                      <thead>
                        <tr>
                          <th scope="col">Trial</th>
                          <th scope="col">lr</th>
                          <th scope="col">bs</th>
                          <th scope="col">seed</th>
                          <th scope="col">OOS loss</th>
                          <th scope="col">OOS acc</th>
                        </tr>
                      </thead>
                      <tbody>
                        {tuneResult.trials.map((t) => (
                          <TrialRow key={t.model_id} t={t} bestModelId={tuneResult.best.model_id} />
                        ))}
                      </tbody>
                    </table>
                  </div>
                  {tuneResult.best.load_error ? (
                    <div className="pa-notice warn">
                      <span className="pa-notice-glyph">!</span>
                      <span>The sweep succeeded but loading the winner failed: {String(tuneResult.best.load_error)}</span>
                    </div>
                  ) : null}
                </div>
              ) : null}
            </div>
          </section>
        </div>
      )}

      {/* --------------------------------------------------------- models list */}
      <section className="pa-panel">
        <div className="pa-panel-head">
          <span className="pa-dot" aria-hidden="true" />
          <span className="pa-panel-title">Trained Advisers</span>
          <span className="pa-panel-sub">
            Checkpoints in{" "}
            <span className="pa-mono">{status?.config?.artifact_dir ?? "artifacts/position_adviser"}</span>{" "}
            — the majority-class baseline is{" "}
            <span className="pa-mono">{baselineRef.toFixed(4)}</span>; anything below it is flagged,
            never hidden.
          </span>
        </div>
        <div className="pa-panel-body">
          {models.length === 0 ? (
            <div className="pa-empty-input">
              <span>∅</span>
              <span>
                No adviser checkpoints yet. Train one above — it lands in{" "}
                {status?.config?.artifact_dir ?? "artifacts/position_adviser"}. GET
                /api/position-adviser/models returned an empty list.
              </span>
            </div>
          ) : (
            <div className="pa-models">
              {models.map((m) => (
                <ModelCard
                  key={m.model_id}
                  m={m}
                  isActive={m.model_id === activeModelId}
                  baseline={tuneBaseline}
                  busy={busy}
                  onLoad={onLoad}
                />
              ))}
            </div>
          )}
        </div>
      </section>

      {/* ------------------------------------------------------ activity feed */}
      <section className="pa-panel">
        <div className="pa-panel-head">
          <span className="pa-dot" aria-hidden="true" />
          <span className="pa-panel-title">Live Advisories</span>
          <span className="pa-panel-sub">
            Enable PAPER to start computing them without touching the decide system, then LIVE once the
            checks pass. {advisories.length > 0 ? `most recent ${advisories.length} shown` : ""}
          </span>
        </div>
        <div className="pa-panel-body">
          {advisories.length === 0 ? (
            <div className="pa-empty-input">
              <span>∅</span>
              <span>
                No advisories yet. Enable PAPER to start computing them without touching the decide
                system, then LIVE once the checks pass. GET /api/position-adviser/advisories?limit=30
                returned an empty list.
              </span>
            </div>
          ) : (
            <div className="pa-feed">
              {advisories.map((a) => (
                <AdvisoryRow key={a.advisory_id} a={a} />
              ))}
            </div>
          )}
        </div>
      </section>
    </div>
  );
}
