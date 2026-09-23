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

import { useCallback, useEffect, useState } from "react";

import type { ShellPageProps } from "@/app/featureModule";

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

const REFRESH_MS = 3000;

/** Majority-class baseline is what any constant classifier scores; a model that
 *  cannot beat it is shown BELOW BASELINE, never hidden. The server reports it
 *  per-sweep; this is the fallback constant when the sweep value is absent. */
const MAJORITY_BASELINE_FALLBACK = 0.682;

function classNames(...parts: (string | false | null | undefined)[]): string {
  return parts.filter(Boolean).join(" ");
}

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

/** OOS accuracy vs the majority-class baseline: never silently green. */
function belowBaseline(m: AdviserModelDto, baseline: number | null): boolean {
  const ref = baseline ?? MAJORITY_BASELINE_FALLBACK;
  return m.oos_accuracy != null && m.oos_accuracy < ref;
}

type DistSeg = { key: string; pct: number };

/** OOS prediction distribution -> stacked meter segments (percent of total). */
function distSegments(dist: Record<string, number>): DistSeg[] {
  const total = Object.values(dist).reduce((s, v) => s + (v || 0), 0);
  if (total <= 0) return [];
  return Object.entries(dist)
    .filter(([, v]) => (v || 0) > 0)
    .map(([k, v]) => ({ key: k.toUpperCase(), pct: ((v || 0) / total) * 100 }));
}

function distClass(key: string): string {
  if (key === "KEEP") return "keep";
  if (key === "CLOSE") return "close";
  if (key === "REDUCE") return "reduce";
  return "other";
}

function fmt(n: number | null | undefined, digits = 4): string {
  return typeof n === "number" && Number.isFinite(n) ? n.toFixed(digits) : "--";
}

function fmtTime(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleTimeString();
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
  const [notice, setNotice] = useState<string>("");
  const [loading, setLoading] = useState<boolean>(true);

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

  const firstDataset = datasets.length > 0 ? datasets[0] : null;
  const effectiveDataset = trainDataset || (firstDataset ? firstDataset.path : "");

  const refresh = useCallback(async () => {
    try {
      const [s, m, a, ds] = await Promise.all([
        positionAdviserApi.status(),
        positionAdviserApi.models(),
        positionAdviserApi.advisories(30),
        positionAdviserApi.datasets(),
      ]);
      setStatus(s);
      setModels(m.models || []);
      setActiveModelId(m.active_adviser_id || "");
      setAdvisories(a.advisories || []);
      setDatasets(ds.datasets || []);
      setError("");
    } catch (err) {
      // The backend stays the source of truth; show its message verbatim.
      const detail = (err as { detail?: string })?.detail ?? String(err);
      setError(typeof detail === "string" ? detail : JSON.stringify(detail));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
    const id = window.setInterval(refresh, REFRESH_MS);
    return () => window.clearInterval(id);
  }, [refresh]);

  const runChecks = useCallback(async () => {
    setBusy(true);
    setError("");
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
      const detail = (err as { detail?: string })?.detail ?? String(err);
      setError(typeof detail === "string" ? detail : JSON.stringify(detail));
    } finally {
      setBusy(false);
    }
  }, []);

  const onActivate = useCallback(
    async (target: AdviserActivation) => {
      setBusy(true);
      setError("");
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
        const detail = (err as { detail?: string })?.detail ?? String(err);
        setError(typeof detail === "string" ? detail : JSON.stringify(detail));
      } finally {
        setBusy(false);
      }
    },
    [checks, refresh],
  );

  const onUnload = useCallback(async () => {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const out = await positionAdviserApi.unload();
      setNotice(out.message || "adviser unloaded; activation reset to DISABLED");
      await refresh();
    } catch (err) {
      const detail = (err as { detail?: string })?.detail ?? String(err);
      setError(typeof detail === "string" ? detail : JSON.stringify(detail));
    } finally {
      setBusy(false);
    }
  }, [refresh]);

  const onTrain = useCallback(async () => {
    if (!effectiveDataset) {
      setError("No position dataset available. Generate one in the Neural Studio first.");
      return;
    }
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const out = await positionAdviserApi.train({
        dataset_path: effectiveDataset,
        epochs: trainEpochs,
      });
      setNotice(out.message || "training complete");
      await refresh();
    } catch (err) {
      const detail = (err as { detail?: string })?.detail ?? String(err);
      setError(typeof detail === "string" ? detail : JSON.stringify(detail));
    } finally {
      setBusy(false);
    }
  }, [effectiveDataset, trainEpochs, refresh]);

  const onAutoTune = useCallback(async () => {
    if (!effectiveDataset) {
      setError("No position dataset available. Generate one in the Neural Studio first.");
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
      return;
    }
    if (lrs.length === 0 || bss.length === 0 || seeds.length === 0) {
      setError("auto-tune needs at least one learning rate, batch size, and seed.");
      return;
    }

    setBusy(true);
    setError("");
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
      const detail = (err as { detail?: string })?.detail ?? String(err);
      setError(typeof detail === "string" ? detail : JSON.stringify(detail));
    } finally {
      setBusy(false);
    }
  }, [effectiveDataset, tuneEpochs, tuneMaxTrials, tuneLrs, tuneBatchSizes, tuneSeeds, refresh]);

  const onLoad = useCallback(
    async (m: AdviserModelDto) => {
      setBusy(true);
      setError("");
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
        const detail = (err as { detail?: string })?.detail ?? String(err);
        setError(typeof detail === "string" ? detail : JSON.stringify(detail));
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

      {error ? (
        <div className="pa-notice err" role="alert">
          <span className="pa-notice-glyph">✕</span>
          <span>{error}</span>
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
                <li key={c.name} className={classNames("pa-check", c.passed ? "ok" : "fail")}>
                  <span className="pa-check-mark" aria-hidden="true">
                    {c.passed ? "✓" : "✕"}
                  </span>
                  <span>
                    <span className="pa-check-name">{c.name}</span>
                    <span className="pa-check-detail"> — {c.detail}</span>
                  </span>
                </li>
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
        <div className="skeleton-line" aria-hidden="true">
          <div className="skeleton" style={{ height: 120 }} />
          <div className="skeleton" style={{ height: 92, width: "72%" }} />
          <div className="skeleton" style={{ height: 92, width: "58%" }} />
        </div>
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
                      <span>No position datasets yet — generate one in the Neural Studio (M1/M5 source).</span>
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
              <div className="pa-field-row" style={{ marginTop: 14 }}>
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
                        {tuneResult.trials.map((t) => {
                          const isWinner = t.model_id === tuneResult.best.model_id;
                          return (
                            <tr
                              key={t.model_id}
                              className={classNames(
                                isWinner && "is-winner",
                                t.failed && "is-failed",
                              )}
                            >
                              <td className="mono-id">
                                {t.failed ? "✗ " : isWinner ? "★ " : "· "}
                                {t.model_id}
                              </td>
                              <td>{t.learning_rate}</td>
                              <td>{t.batch_size}</td>
                              <td>{t.seed}</td>
                              <td>{t.failed ? "failed" : fmt(t.oos_loss)}</td>
                              <td>{t.failed ? "—" : fmt(t.oos_accuracy)}</td>
                            </tr>
                          );
                        })}
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
                {status?.config?.artifact_dir ?? "artifacts/position_adviser"}.
              </span>
            </div>
          ) : (
            <div className="pa-models">
              {models.map((m) => {
                const isActive = m.model_id === activeModelId;
                const isBelow = belowBaseline(m, tuneBaseline);
                const segs = distSegments(m.oos_action_distribution || {});
                return (
                  <div key={m.model_id} className={classNames("pa-model", isActive && "is-active")}>
                    <div className="pa-model-top">
                      <span className="pa-model-id">{m.model_id}</span>
                      {isActive ? <span className="badge good">LOADED</span> : null}
                      {isBelow ? <span className="badge bad">BELOW BASELINE</span> : null}
                      {!m.has_scaler ? <span className="badge warn">NO SCALER</span> : null}
                    </div>
                    <div className="pa-model-facts">
                      <span>
                        OOS acc <b>{fmt(m.oos_accuracy)}</b>
                      </span>
                      <span>
                        OOS loss <b>{fmt(m.oos_loss)}</b>
                      </span>
                      <span>
                        val loss <b>{fmt(m.best_val_loss)}</b>
                      </span>
                      <span>
                        train/oos <b>{m.train_rows ?? "--"}/{m.oos_rows ?? "--"}</b>
                      </span>
                      <span>
                        epochs <b>{m.epochs ?? "--"}</b>
                      </span>
                      {m.created_at ? <span title={m.created_at}>{fmtTime(m.created_at)}</span> : null}
                    </div>
                    {segs.length > 0 ? (
                      <div className="pa-dist">
                        <span className="pa-dist-lab">OOS predictions</span>
                        <span className="pa-dist-track">
                          {segs.map((s) => (
                            <span
                              key={s.key}
                              className={classNames("pa-dist-seg", distClass(s.key))}
                              style={{ width: `${s.pct}%` }}
                              title={`${s.key}: ${s.pct.toFixed(1)}%`}
                            />
                          ))}
                        </span>
                        <span className="pa-dist-legend">
                          {segs.map((s) => (
                            <span key={s.key}>
                              <i className={distClass(s.key)} />
                              {s.key} {s.pct.toFixed(0)}%
                            </span>
                          ))}
                        </span>
                      </div>
                    ) : null}
                    <div>
                      <button
                        type="button"
                        disabled={busy || !m.has_scaler}
                        onClick={() => void onLoad(m)}
                        className="pa-btn pa-btn-ghost"
                      >
                        {m.has_scaler ? "Load into live memory" : "No scaler sidecar — load refused"}
                      </button>
                    </div>
                  </div>
                );
              })}
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
                system, then LIVE once the checks pass.
              </span>
            </div>
          ) : (
            <div className="pa-feed">
              {advisories.map((a) => {
                const dist = distSegments(a.probabilities || {});
                return (
                  <div key={a.advisory_id} className="pa-feed-item">
                    <div className="pa-feed-top">
                      <span className="pa-feed-ticket">#{a.ticket}</span>
                      <span
                        className={classNames(
                          "badge",
                          a.action === "CLOSE"
                            ? "bad"
                            : a.action === "REDUCE"
                              ? "warn"
                              : "good",
                        )}
                      >
                        {a.action}
                      </span>
                      <span className="pa-conf" title="adviser confidence">
                        <span className="pa-conf-track">
                          <i style={{ width: `${Math.max(0, Math.min(1, a.confidence)) * 100}%` }} />
                        </span>
                        <span className="pa-conf-val">{a.confidence.toFixed(3)}</span>
                      </span>
                      <span
                        className={a.hold_score_adjustment < 0 ? "pa-hold-neg" : "pa-hold-zero"}
                      >
                        hold adj {a.hold_score_adjustment.toFixed(2)}
                      </span>
                      <span
                        className={classNames(
                          "badge",
                          a.applied ? "good" : "neutral",
                        )}
                      >
                        {a.applied ? "APPLIED" : "LOGGED ONLY"}
                      </span>
                      <span className="pa-feed-time">
                        {a.latency_ms.toFixed(2)} ms · {fmtTime(a.evaluated_at)}
                      </span>
                    </div>
                    {dist.length > 0 ? (
                      <span className="pa-dist-track" style={{ height: 4 }}>
                        {dist.map((s) => (
                          <span
                            key={s.key}
                            className={classNames("pa-dist-seg", distClass(s.key))}
                            style={{ width: `${s.pct}%` }}
                            title={`${s.key}: ${s.pct.toFixed(1)}%`}
                          />
                        ))}
                      </span>
                    ) : null}
                    {a.not_applied_reason ? (
                      <div className="pa-feed-reason">{a.not_applied_reason}</div>
                    ) : null}
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </section>
    </div>
  );
}
