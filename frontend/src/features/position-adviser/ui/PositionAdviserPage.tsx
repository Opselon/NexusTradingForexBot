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

const REFRESH_MS = 3000;

function classNames(...parts: (string | false | null | undefined)[]): string {
  return parts.filter(Boolean).join(" ");
}

function activationTone(a: string): string {
  if (a === "LIVE") return "text-emerald-400";
  if (a === "PAPER") return "text-amber-400";
  return "text-slate-400";
}

function activationDot(a: string): string {
  if (a === "LIVE") return "bg-emerald-400";
  if (a === "PAPER") return "bg-amber-400";
  return "bg-slate-600";
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
  const cur = ACTIVATION_LADDER.find((a) => a === activation) ?? "DISABLED";

  return (
    <div className="space-y-5">
      {/* ---------------------------------------------------------- header */}
      <div className="rounded-lg border border-slate-800 bg-slate-900/60 p-4">
        <div className="flex items-center gap-2">
          <span
            className={classNames("h-2.5 w-2.5 rounded-full", activationDot(activation))}
            aria-hidden
          />
          <h2 className="text-base font-bold text-white">Layer-2 Position Decision Adviser</h2>
          <span className={classNames("text-xs font-extrabold uppercase", activationTone(activation))}>
            {cur}
          </span>
        </div>
        <p className="mt-1 text-xs text-slate-400">
          Optional, opt-in. When enabled it can only make a keep/close verdict MORE conservative —
          it never opens, sizes, extends, or weakens a position. Default is DISABLED, so the decide
          system runs exactly as before.
        </p>
        <div className="mt-3 text-xs text-slate-400">
          {ACTIVATION_HELP[cur as AdviserActivation]}
        </div>
        {status?.last_error ? (
          <div className="mt-2 rounded border border-rose-800 bg-rose-950/40 p-2 text-xs text-rose-300">
            last adviser error: {String(status.last_error)}
          </div>
        ) : null}
      </div>

      {error ? (
        <div className="rounded border border-rose-800 bg-rose-950/40 p-3 text-xs text-rose-300">
          {error}
        </div>
      ) : null}
      {notice ? (
        <div className="rounded border border-emerald-800 bg-emerald-950/30 p-3 text-xs text-emerald-300">
          {notice}
        </div>
      ) : null}

      {/* ------------------------------------------------- activation ladder */}
      <div className="rounded-lg border border-slate-800 bg-slate-900/60 p-4">
        <div className="text-sm font-bold text-cyan-400">Activation Ladder</div>
        <p className="mt-1 text-xs text-slate-400">
          Each step must be verified with real broker/position checks before the next becomes
          selectable. A LIVE activation that skips the checks is refused by the server.
        </p>
        <div className="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-3">
          {ACTIVATION_LADDER.map((a) => {
            const isCurrent = a === activation;
            const disabled =
              busy ||
              isCurrent ||
              (a === "LIVE" && !liveAvailable) ||
              (a === "PAPER" && !status?.ready);
            return (
              <button
                key={a}
                type="button"
                disabled={disabled}
                onClick={() => void onActivate(a)}
                className={classNames(
                  "rounded border px-3 py-2 text-xs font-semibold transition",
                  isCurrent
                    ? "border-cyan-500 bg-cyan-950/40 text-cyan-300"
                    : disabled
                      ? "cursor-not-allowed border-slate-800 bg-slate-950 text-slate-600"
                      : "border-slate-700 bg-slate-950 text-slate-200 hover:border-cyan-600 hover:text-white",
                )}
              >
                {isCurrent ? "● " : ""}
                {a}
                {a === "LIVE" && !liveAvailable ? " (checks required)" : ""}
              </button>
            );
          })}
        </div>
        <div className="mt-3">
          <button
            type="button"
            disabled={busy}
            onClick={() => void runChecks()}
            className="rounded border border-slate-700 bg-slate-950 px-3 py-1.5 text-xs font-semibold text-slate-200 hover:border-cyan-600 hover:text-white disabled:opacity-50"
          >
            {busy ? "Running…" : "Run Broker & Position Checks"}
          </button>
        </div>
        {checks.length > 0 ? (
          <ul className="mt-3 space-y-1 text-xs">
            {checks.map((c) => (
              <li key={c.name} className="flex items-start gap-2">
                <span className={c.passed ? "text-emerald-400" : "text-rose-400"}>
                  {c.passed ? "✓" : "✗"}
                </span>
                <span className="font-mono text-slate-300">{c.name}</span>
                <span className="text-slate-500">— {c.detail}</span>
              </li>
            ))}
          </ul>
        ) : null}
      </div>

      {/* ----------------------------------------------------- live counters */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        {[
          { label: "Model", value: status?.model_id || "NONE" },
          { label: "Feature dim", value: status?.feature_dim != null ? String(status.feature_dim) : "--" },
          { label: "Applied", value: String(status?.applied_count ?? 0) },
          { label: "Evaluated", value: String(status?.evaluated_count ?? 0) },
        ].map((cell) => (
          <div key={cell.label} className="rounded border border-slate-800 bg-slate-900/60 p-3">
            <div className="text-[10px] uppercase tracking-wide text-slate-500">{cell.label}</div>
            <div className="truncate text-sm font-bold text-white">{cell.value}</div>
          </div>
        ))}
      </div>

      {/* ------------------------------------------------------ training row */}
      <div className="rounded-lg border border-slate-800 bg-slate-900/60 p-4">
        <div className="text-sm font-bold text-cyan-400">Build / Fine-Tune an Adviser</div>
        <p className="mt-1 text-xs text-slate-400">
          Trains on a generated Position dataset (KEEP / CLOSE / REDUCE labels). OOS accuracy is
          measured on a held-out split that the trainer never fits — a model below the majority-class
          baseline is reported as such, never hidden.
        </p>
        <div className="mt-3 flex flex-wrap items-end gap-3 text-xs">
          <div className="min-w-[240px] flex-1">
            <label className="mb-1 block font-semibold text-slate-400">Position dataset</label>
            {datasets.length > 0 ? (
              <select
                value={trainDataset}
                onChange={(e) => setTrainDataset(e.target.value)}
                className="w-full rounded border border-slate-800 bg-slate-950 px-2.5 py-2 font-mono text-slate-300"
              >
                {datasets.map((d) => (
                  <option key={d.path} value={d.path}>
                    {d.name} {d.timeframe ? `(${d.timeframe})` : ""}
                  </option>
                ))}
              </select>
            ) : (
              <div className="rounded border border-amber-800 bg-amber-950/30 px-2.5 py-2 font-mono text-amber-300">
                No position datasets yet — generate one in the Neural Studio (M1/M5 source).
              </div>
            )}
          </div>
          <div>
            <label className="mb-1 block font-semibold text-slate-400">Epochs</label>
            <input
              type="number"
              min={1}
              max={100}
              value={trainEpochs}
              onChange={(e) => setTrainEpochs(parseInt(e.target.value, 10) || 12)}
              className="w-24 rounded border border-slate-800 bg-slate-950 px-2 py-2 text-white"
            />
          </div>
          <button
            type="button"
            disabled={busy || !effectiveDataset}
            onClick={() => void onTrain()}
            className="rounded border border-cyan-600 bg-cyan-950/40 px-4 py-2 font-semibold text-cyan-300 hover:bg-cyan-900/50 disabled:opacity-50"
          >
            {busy ? "Training…" : "Train Adviser"}
          </button>
        </div>
      </div>

      {/* ------------------------------------------------------ auto mode */}
      <div className="rounded-lg border border-purple-800 bg-purple-950/20 p-4">
        <div className="flex items-center gap-2">
          <span className="text-sm font-bold text-purple-300">✦ Auto Mode — Find the Best Adviser</span>
        </div>
        <p className="mt-1 text-xs text-slate-400">
          Runs a bounded grid sweep over learning rate, batch size and seed, then keeps the model
          with the lowest <strong>out-of-sample loss</strong> — the only split the trainer never fits
          or early-stops on — and loads it. Losing checkpoints are pruned; the winner and its real
          OOS metrics are reported below. A sweep that cannot beat the majority-class baseline is
          shown as such, never hidden.
        </p>
        <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4 text-xs">
          <div>
            <label className="mb-1 block font-semibold text-slate-400">Epochs / trial</label>
            <input
              type="number"
              min={1}
              max={100}
              value={tuneEpochs}
              onChange={(e) => setTuneEpochs(parseInt(e.target.value, 10) || 12)}
              className="w-full rounded border border-slate-800 bg-slate-950 px-2.5 py-2 text-white"
            />
          </div>
          <div>
            <label className="mb-1 block font-semibold text-slate-400">Max trials</label>
            <input
              type="number"
              min={1}
              max={60}
              value={tuneMaxTrials}
              onChange={(e) => setTuneMaxTrials(parseInt(e.target.value, 10) || 6)}
              className="w-full rounded border border-slate-800 bg-slate-950 px-2.5 py-2 text-white"
            />
          </div>
          <div>
            <label className="mb-1 block font-semibold text-slate-400">Learning rates</label>
            <input
              type="text"
              value={tuneLrs}
              onChange={(e) => setTuneLrs(e.target.value)}
              className="w-full rounded border border-slate-800 bg-slate-950 px-2.5 py-2 font-mono text-white"
            />
          </div>
          <div>
            <label className="mb-1 block font-semibold text-slate-400">Batch sizes</label>
            <input
              type="text"
              value={tuneBatchSizes}
              onChange={(e) => setTuneBatchSizes(e.target.value)}
              className="w-full rounded border border-slate-800 bg-slate-950 px-2.5 py-2 font-mono text-white"
            />
          </div>
        </div>
        <div className="mt-3 flex flex-wrap items-end gap-3 text-xs">
          <div className="min-w-[200px] flex-1">
            <label className="mb-1 block font-semibold text-slate-400">Seeds</label>
            <input
              type="text"
              value={tuneSeeds}
              onChange={(e) => setTuneSeeds(e.target.value)}
              className="w-full rounded border border-slate-800 bg-slate-950 px-2.5 py-2 font-mono text-white"
            />
          </div>
          <button
            type="button"
            disabled={busy || !effectiveDataset}
            onClick={() => void onAutoTune()}
            className="rounded border border-purple-500 bg-purple-900/50 px-4 py-2 font-extrabold text-purple-200 hover:bg-purple-800/60 disabled:opacity-50"
          >
            {busy ? "Sweeping…" : "▶ Auto-Tune & Load Best"}
          </button>
        </div>

        {tuneResult ? (
          <div className="mt-4 space-y-3 rounded border border-slate-800 bg-slate-950/60 p-3 text-xs">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-bold text-purple-300">Winner</span>
              <span className="font-mono text-white">
                {String(tuneResult.best.model_id ?? "--")}
              </span>
              {tuneResult.loaded ? (
                <span className="rounded bg-emerald-950/60 px-1.5 py-0.5 text-[10px] font-bold text-emerald-300">
                  LOADED
                </span>
              ) : (
                <span className="rounded bg-amber-950/60 px-1.5 py-0.5 text-[10px] font-bold text-amber-300">
                  NOT LOADED
                </span>
              )}
              {tuneResult.beats_majority_baseline ? (
                <span className="rounded bg-emerald-950/60 px-1.5 py-0.5 text-[10px] font-bold text-emerald-300">
                  BEATS BASELINE
                </span>
              ) : (
                <span className="rounded bg-rose-950/60 px-1.5 py-0.5 text-[10px] font-bold text-rose-300">
                  BELOW MAJORITY BASELINE
                </span>
              )}
            </div>
            <div className="flex flex-wrap gap-x-4 gap-y-1 text-slate-400">
              <span>
                OOS loss:{" "}
                {typeof tuneResult.best.oos_loss === "number"
                  ? tuneResult.best.oos_loss.toFixed(4)
                  : "--"}
              </span>
              <span>
                OOS acc:{" "}
                {typeof tuneResult.best.oos_accuracy === "number"
                  ? tuneResult.best.oos_accuracy.toFixed(4)
                  : "--"}
              </span>
              <span>
                majority baseline:{" "}
                {tuneResult.majority_baseline_accuracy != null
                  ? tuneResult.majority_baseline_accuracy.toFixed(4)
                  : "--"}
              </span>
              <span>
                lr: {String(tuneResult.best.learning_rate ?? "--")} · bs:{" "}
                {String(tuneResult.best.batch_size ?? "--")} · seed:{" "}
                {String(tuneResult.best.seed ?? "--")}
              </span>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full border-collapse text-left">
                <thead>
                  <tr className="border-b border-slate-800 text-slate-500">
                    <th className="py-1 pr-3 font-bold">Trial</th>
                    <th className="py-1 pr-3 font-bold">lr</th>
                    <th className="py-1 pr-3 font-bold">bs</th>
                    <th className="py-1 pr-3 font-bold">seed</th>
                    <th className="py-1 pr-3 font-bold">OOS loss</th>
                    <th className="py-1 font-bold">OOS acc</th>
                  </tr>
                </thead>
                <tbody>
                  {tuneResult.trials.map((t) => (
                    <tr key={t.model_id} className="border-b border-slate-900">
                      <td className="py-1 pr-3 font-mono text-slate-300">
                        {t.failed ? "✗ " : "· "}
                        {t.model_id}
                      </td>
                      <td className="py-1 pr-3 font-mono text-slate-400">{t.learning_rate}</td>
                      <td className="py-1 pr-3 font-mono text-slate-400">{t.batch_size}</td>
                      <td className="py-1 pr-3 font-mono text-slate-400">{t.seed}</td>
                      <td className="py-1 pr-3 font-mono text-slate-300">
                        {t.failed ? "failed" : t.oos_loss != null ? t.oos_loss.toFixed(4) : "--"}
                      </td>
                      <td className="py-1 font-mono text-slate-300">
                        {t.failed ? "—" : t.oos_accuracy != null ? t.oos_accuracy.toFixed(4) : "--"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {tuneResult.best.load_error ? (
              <div className="rounded border border-amber-800 bg-amber-950/30 p-2 text-amber-300">
                The sweep succeeded but loading the winner failed: {String(tuneResult.best.load_error)}
              </div>
            ) : null}
          </div>
        ) : null}
      </div>

      {/* --------------------------------------------------------- models list */}
      <div className="rounded-lg border border-slate-800 bg-slate-900/60 p-4">
        <div className="text-sm font-bold text-cyan-400">Trained Advisers</div>
        {models.length === 0 ? (
          <div className="mt-2 text-xs text-slate-500">
            No adviser checkpoints yet. Train one above — it lands in{" "}
            <span className="font-mono">{status?.config?.artifact_dir ?? "artifacts/position_adviser"}</span>.
          </div>
        ) : (
          <ul className="mt-2 divide-y divide-slate-800">
            {models.map((m) => {
              const isActive = m.model_id === activeModelId;
              const belowBaseline = m.oos_accuracy != null && m.oos_accuracy < 0.682;
              return (
                <li key={m.model_id} className="py-3">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-mono text-sm text-white">{m.model_id}</span>
                    {isActive ? (
                      <span className="rounded bg-cyan-950/60 px-1.5 py-0.5 text-[10px] font-bold text-cyan-300">
                        LOADED
                      </span>
                    ) : null}
                    {belowBaseline ? (
                      <span className="rounded bg-rose-950/50 px-1.5 py-0.5 text-[10px] font-bold text-rose-300">
                        BELOW MAJORITY BASELINE
                      </span>
                    ) : null}
                  </div>
                  <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-xs text-slate-400">
                    <span>OOS acc: {m.oos_accuracy != null ? m.oos_accuracy.toFixed(4) : "--"}</span>
                    <span>OOS loss: {m.oos_loss != null ? m.oos_loss.toFixed(4) : "--"}</span>
                    <span>val loss: {m.best_val_loss != null ? m.best_val_loss.toFixed(4) : "--"}</span>
                    <span>train/oos: {m.train_rows ?? "--"}/{m.oos_rows ?? "--"}</span>
                  </div>
                  {m.oos_action_distribution &&
                  Object.keys(m.oos_action_distribution).length > 0 ? (
                    <div className="mt-1 text-xs text-slate-500">
                      OOS predictions:{" "}
                      {Object.entries(m.oos_action_distribution)
                        .map(([k, v]) => `${k}=${v}`)
                        .join(" · ")}
                    </div>
                  ) : null}
                  <div className="mt-2">
                    <button
                      type="button"
                      disabled={busy || !m.has_scaler}
                      onClick={() => void onLoad(m)}
                      className="rounded border border-slate-700 bg-slate-950 px-3 py-1.5 text-xs font-semibold text-slate-200 hover:border-cyan-600 hover:text-white disabled:opacity-50"
                    >
                      {m.has_scaler ? "Load into live memory" : "No scaler sidecar — load refused"}
                    </button>
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </div>

      {/* ------------------------------------------------------ activity feed */}
      <div className="rounded-lg border border-slate-800 bg-slate-900/60 p-4">
        <div className="text-sm font-bold text-cyan-400">Live Advisories</div>
        {advisories.length === 0 ? (
          <div className="mt-2 text-xs text-slate-500">
            No advisories yet. Enable PAPER to start computing them without touching the decide
            system, then LIVE once the checks pass.
          </div>
        ) : (
          <ul className="mt-2 divide-y divide-slate-800">
            {advisories.map((a) => (
              <li key={a.advisory_id} className="py-2.5">
                <div className="flex flex-wrap items-center gap-2 text-xs">
                  <span className="font-mono text-slate-400">#{a.ticket}</span>
                  <span
                    className={classNames(
                      "rounded px-1.5 py-0.5 text-[10px] font-bold",
                      a.action === "CLOSE"
                        ? "bg-rose-950/60 text-rose-300"
                        : a.action === "REDUCE"
                          ? "bg-amber-950/60 text-amber-300"
                          : "bg-emerald-950/60 text-emerald-300",
                    )}
                  >
                    {a.action}
                  </span>
                  <span className="text-slate-400">conf {a.confidence.toFixed(4)}</span>
                  <span
                    className={a.hold_score_adjustment < 0 ? "text-rose-400" : "text-slate-500"}
                  >
                    hold adj {a.hold_score_adjustment.toFixed(2)}
                  </span>
                  <span
                    className={a.applied ? "text-emerald-400" : "text-slate-600"}
                  >
                    {a.applied ? "APPLIED" : "logged only"}
                  </span>
                  <span className="ml-auto text-slate-600">{a.latency_ms.toFixed(2)} ms</span>
                </div>
                {a.not_applied_reason ? (
                  <div className="mt-0.5 text-[11px] text-slate-600">{a.not_applied_reason}</div>
                ) : null}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
