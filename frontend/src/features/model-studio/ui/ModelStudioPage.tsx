import { useState, useEffect } from "react";

import { modelStudioApi } from "../api";
import type {
  ActiveModelResponse,
  DatasetDownloadResponse,
  HotLoadResponse,
  InspectFeaturesResponse,
  InspectScalerResponse,
  ModelRecordDto,
  ModelStudioDatasetItem,
  ModelStudioOverviewDto,
  PositionDatasetResponse,
  PredictResponse,
  StressTestResultRow,
  VerifyModelResponse,
} from "../model";

export default function ModelStudioPage() {
  const [dimension, setDimension] = useState<number>(50);
  const [overview, setOverview] = useState<ModelStudioOverviewDto | null>(null);
  const [datasets, setDatasets] = useState<ModelStudioDatasetItem[]>([]);
  const [loading, setLoading] = useState<boolean>(false);
  const [useLive, setUseLive] = useState<boolean>(true);
  const [noise, setNoise] = useState<number>(0);
  const [threshold, setThreshold] = useState<number>(0.35);
  const [predictData, setPredictData] = useState<PredictResponse | null>(null);

  // AI Hub / Model Registry & Hot-Loader state
  const [models, setModels] = useState<ModelRecordDto[]>([]);
  const [activeModel, setActiveModel] = useState<ActiveModelResponse["active_model"]>(null);
  const [selectedModelId, setSelectedModelId] = useState<string>("");
  const [enableFineTune, setEnableFineTune] = useState<boolean>(false);
  const [attachScaler, setAttachScaler] = useState<boolean>(true);
  const [hotLoadBusy, setHotLoadBusy] = useState<boolean>(false);
  const [hotLoadResult, setHotLoadResult] = useState<HotLoadResponse | null>(null);
  const [hotLoadError, setHotLoadError] = useState<string>("");
  const [verifyBusy, setVerifyBusy] = useState<boolean>(false);
  const [verifyResult, setVerifyResult] = useState<VerifyModelResponse | null>(null);
  const [scalerResult, setScalerResult] = useState<InspectScalerResponse | null>(null);
  const [rollbackBusy, setRollbackBusy] = useState<boolean>(false);

  // 70D components
  const [components70, setComponents70] = useState<any[]>([]);
  const [contractValid, setContractValid] = useState<boolean | null>(null);
  const [schemaHash, setSchemaHash] = useState<string>("");

  // Training state
  const [selectedDataset, setSelectedDataset] = useState<string>("");
  const [epochs, setEpochs] = useState<number>(3);
  const [learningRate, setLearningRate] = useState<number>(0.0005);
  const [trainStatus, setTrainStatus] = useState<string>("");
  const [trainProgress, setTrainProgress] = useState<number>(0);
  const [trainError, setTrainError] = useState<string>("");

  // Dataset download state (API-first ingestion)
  const [dlSymbol, setDlSymbol] = useState<string>("XAUUSD");
  const [dlTimeframe, setDlTimeframe] = useState<string>("M5");
  const [dlBars, setDlBars] = useState<number>(10000);
  const [dlSource, setDlSource] = useState<string>("synthetic");
  const [dlBusy, setDlBusy] = useState<boolean>(false);
  const [dlResult, setDlResult] = useState<DatasetDownloadResponse | null>(null);
  const [dlError, setDlError] = useState<string>("");

  // Feature inspection state (50D / 70D)
  const [inspectBusy, setInspectBusy] = useState<boolean>(false);
  const [inspectResult, setInspectResult] = useState<InspectFeaturesResponse | null>(null);
  const [inspectError, setInspectError] = useState<string>("");

  // Layer-2 position-manager dataset state
  const [posSource, setPosSource] = useState<string>("");
  const [posMaxHolding, setPosMaxHolding] = useState<number>(30);
  const [posTargetAtr, setPosTargetAtr] = useState<number>(2.0);
  const [posFriction, setPosFriction] = useState<number>(0.25);
  const [posBusy, setPosBusy] = useState<boolean>(false);
  const [posResult, setPosResult] = useState<PositionDatasetResponse | null>(null);
  const [posError, setPosError] = useState<string>("");

  // Stress & Benchmark
  const [stressResults, setStressResults] = useState<StressTestResultRow[]>([]);
  const [benchStats, setBenchStats] = useState<any>(null);

  useEffect(() => {
    fetchOverview();
    fetchDatasets();
    fetchModels();
    fetchActiveModel();
  }, []);

  const fetchModels = async () => {
    try {
      const res = await modelStudioApi.listModels();
      const list = res.models || [];
      setModels(list);
      if (list.length > 0 && list[0]) {
        const champ = list.find((m) => m.is_active || m.id === res.active_champion_id);
        const fallbackId = list[0].id;
        setSelectedModelId((prev) => prev || (champ ? champ.id : fallbackId));
      }
    } catch (err) {
      console.error("Failed to load models catalog:", err);
    }
  };

  const fetchActiveModel = async () => {
    try {
      const res = await modelStudioApi.activeModel();
      setActiveModel(res.active_model);
      if (res.active_model) {
        setEnableFineTune(Boolean(res.active_model.fine_tune_enabled));
      }
    } catch (err) {
      console.error("Failed to load active model state:", err);
    }
  };

  const handleHotLoad = async () => {
    if (!selectedModelId) return;
    setHotLoadBusy(true);
    setHotLoadError("");
    setHotLoadResult(null);
    try {
      const res = await modelStudioApi.hotLoad({
        model_id: selectedModelId,
        fine_tune_enabled: enableFineTune,
        attach_scaler: attachScaler,
        operator: "REACT_UI",
      });
      setHotLoadResult(res);
      await fetchModels();
      await fetchActiveModel();
      await fetchOverview();
    } catch (err) {
      setHotLoadError(err instanceof Error ? err.message : String(err));
    } finally {
      setHotLoadBusy(false);
    }
  };

  const handleVerifyModel = async () => {
    if (!selectedModelId) return;
    setVerifyBusy(true);
    setVerifyResult(null);
    try {
      const res = await modelStudioApi.verifyModel({ model_id: selectedModelId });
      setVerifyResult(res);
    } catch (err) {
      console.error("Verify failed:", err);
    } finally {
      setVerifyBusy(false);
    }
  };

  const handleRollback = async () => {
    if (!confirm("Roll back to previous active champion model from history?")) return;
    setRollbackBusy(true);
    try {
      await modelStudioApi.rollback();
      await fetchModels();
      await fetchActiveModel();
      await fetchOverview();
    } catch (err) {
      console.error("Rollback failed:", err);
      alert("Rollback failed: " + (err instanceof Error ? err.message : String(err)));
    } finally {
      setRollbackBusy(false);
    }
  };

  const handleInspectScaler = async () => {
    if (!selectedModelId) return;
    try {
      const res = await modelStudioApi.inspectScaler(selectedModelId);
      setScalerResult(res);
    } catch (err) {
      console.error("Inspect scaler failed:", err);
    }
  };

  const fetchOverview = async () => {
    try {
      setOverview(await modelStudioApi.overview());
    } catch (err) {
      console.error("Failed to load overview:", err);
    }
  };

  const fetchDatasets = async () => {
    try {
      const d = await modelStudioApi.datasets();
      const list = d.datasets || [];
      setDatasets(list);
      const first = list[0];
      if (first) {
        setSelectedDataset((prev) => prev || first.path);
        setPosSource((prev) => prev || first.path);
      }
    } catch (err) {
      console.error("Failed to load datasets:", err);
    }
  };

  const handleFetch70d = async () => {
    try {
      const d = await modelStudioApi.fetch70d();
      setComponents70(d.slots || []);
      setContractValid(d.contract_valid ?? null);
      setSchemaHash(d.schema_hash || "");
    } catch (err) {
      console.error("Failed to fetch 70D:", err);
    }
  };

  const handleInference = async () => {
    setLoading(true);
    try {
      const d = await modelStudioApi.predict({
        dimension,
        use_live_features: useLive,
        fetch_live_70d: dimension === 70,
        perturbation_sigma: noise,
        simulate_policy_threshold: threshold,
        inspect_layers: true,
        compute_saliency: true,
      });
      setPredictData(d);
    } catch (err) {
      console.error("Inference failed:", err);
    } finally {
      setLoading(false);
    }
  };

  const handleStressTest = async () => {
    try {
      const d = await modelStudioApi.stressTest(dimension);
      setStressResults(d.results || []);
    } catch (err) {
      console.error("Stress test failed:", err);
    }
  };

  const handleBenchmark = async () => {
    try {
      setBenchStats(await modelStudioApi.benchmark(dimension, 100));
    } catch (err) {
      console.error("Benchmark failed:", err);
    }
  };

  const handleStartTrain = async () => {
    setTrainError("");
    try {
      const d = await modelStudioApi.train({
        dataset_path: selectedDataset,
        dimension,
        epochs,
        batch_size: 256,
        learning_rate: learningRate,
      });
      setTrainStatus(
        `Done: ${d.epochs_completed} epochs | loss ${d.final_loss} | val ${d.final_val_loss} → ${d.checkpoint_path}`,
      );
      setTrainProgress(100);
    } catch (err) {
      const detail = err instanceof Error ? err.message : String(err);
      setTrainError(detail);
      setTrainStatus("");
      setTrainProgress(0);
    }
  };

  const handleDownloadDataset = async () => {
    setDlBusy(true);
    setDlError("");
    setDlResult(null);
    try {
      const d = await modelStudioApi.downloadDataset({
        symbol: dlSymbol.trim().toUpperCase() || "XAUUSD",
        timeframe: dlTimeframe,
        bars: dlBars,
        source: dlSource,
      });
      setDlResult(d);
      await fetchDatasets();
    } catch (err) {
      setDlError(err instanceof Error ? err.message : String(err));
    } finally {
      setDlBusy(false);
    }
  };

  const handleInspectFeatures = async () => {
    setInspectBusy(true);
    setInspectError("");
    try {
      const d = await modelStudioApi.inspectFeatures({
        dataset_path: selectedDataset,
        dimension,
        max_rows: 500,
      });
      setInspectResult(d);
    } catch (err) {
      setInspectError(err instanceof Error ? err.message : String(err));
    } finally {
      setInspectBusy(false);
    }
  };

  const handleGeneratePositionDataset = async () => {
    setPosBusy(true);
    setPosError("");
    setPosResult(null);
    try {
      const d = await modelStudioApi.generatePositionDataset({
        source_dataset_path: posSource,
        dimension,
        max_holding_bars: posMaxHolding,
        target_atr_multiplier: posTargetAtr,
        friction_pips: posFriction,
      });
      setPosResult(d);
      await fetchDatasets();
    } catch (err) {
      setPosError(err instanceof Error ? err.message : String(err));
    } finally {
      setPosBusy(false);
    }
  };

  return (
    <div className="space-y-6 p-4 max-w-7xl mx-auto">
      {/* Header & Mode Switcher */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 p-4 rounded-xl bg-slate-900 border border-slate-800 shadow-xl">
        <div>
          <div className="flex items-center gap-2">
            <span className="text-2xl">🧠</span>
            <h1 className="text-lg font-black text-white tracking-wide">NEURAL MODEL STUDIO</h1>
            <span className="px-2 py-0.5 rounded text-xs font-black bg-cyan-500/20 text-cyan-400 border border-cyan-500/30">
              {dimension}D
            </span>
          </div>
          <p className="text-xs text-slate-400 mt-1">
            Deep learning inspection, interactive forward pass, 70D live assembly, and dataset training.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-xs text-slate-400 font-semibold">Dimension:</span>
          <button
            onClick={() => setDimension(50)}
            className={`px-3 py-1.5 rounded-lg text-xs font-bold transition ${
              dimension === 50 ? "bg-cyan-400 text-black" : "bg-slate-800 text-slate-300 hover:text-white"
            }`}
          >
            50D (scalp_v1)
          </button>
          <button
            onClick={() => setDimension(70)}
            className={`px-3 py-1.5 rounded-lg text-xs font-bold transition ${
              dimension === 70 ? "bg-cyan-400 text-black" : "bg-slate-800 text-slate-300 hover:text-white"
            }`}
          >
            70D (scalp_v3)
          </button>
          <button
            onClick={() => {
              fetchOverview();
              fetchDatasets();
              fetchModels();
              fetchActiveModel();
            }}
            className="px-3 py-1.5 rounded-lg text-xs font-semibold bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-700"
          >
            ↻ Refresh
          </button>
        </div>
      </div>

      {/* Overview Stat Cards */}
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
        <div className="p-3 rounded-xl bg-slate-900 border border-slate-800">
          <div className="text-[10px] font-bold text-slate-500 uppercase">Architecture</div>
          <div className="text-sm font-extrabold text-white mt-1">{overview?.architecture || "ScalpNet"}</div>
        </div>
        <div className="p-3 rounded-xl bg-slate-900 border border-slate-800">
          <div className="text-[10px] font-bold text-slate-500 uppercase">Model Source</div>
          <div className="text-sm font-extrabold text-cyan-400 mt-1">{overview?.model_source || "ONLINE"}</div>
        </div>
        <div className="p-3 rounded-xl bg-slate-900 border border-slate-800">
          <div className="text-[10px] font-bold text-slate-500 uppercase">Parameters</div>
          <div className="text-sm font-extrabold text-white mt-1">
            {overview?.parameter_count ? overview.parameter_count.toLocaleString() : "--"}
          </div>
        </div>
        <div className="p-3 rounded-xl bg-slate-900 border border-slate-800">
          <div className="text-[10px] font-bold text-slate-500 uppercase">Weights SHA256</div>
          <div className="text-sm font-mono text-emerald-400 mt-1">
            {overview?.weights_sha256 ? overview.weights_sha256.substring(0, 12) : "--"}
          </div>
        </div>
        <div className="p-3 rounded-xl bg-slate-900 border border-slate-800">
          <div className="text-[10px] font-bold text-slate-500 uppercase">Device</div>
          <div className="text-sm font-extrabold text-white mt-1">{overview?.device || "cpu"}</div>
        </div>
        <div className="p-3 rounded-xl bg-slate-900 border border-slate-800">
          <div className="text-[10px] font-bold text-slate-500 uppercase">Scaler Status</div>
          <div className="text-sm font-bold text-emerald-400 mt-1">
            {overview?.scaler_stats?.status || "READY"}
          </div>
        </div>
      </div>

      {/* 70D Component Fetcher (Shown when 70D selected) */}
      {dimension === 70 && (
        <div className="p-4 rounded-xl bg-slate-900 border border-slate-800 shadow-xl space-y-3">
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2 border-b border-slate-800 pb-3">
            <div className="flex items-center gap-2">
              <span className="text-amber-400">⚡</span>
              <h2 className="text-sm font-bold text-white">70D LIVE MULTI-SOURCE COMPONENT ASSEMBLY</h2>
              {contractValid !== null && (
                <span
                  className={`px-2 py-0.5 rounded text-[10px] font-bold ${
                    contractValid
                      ? "bg-emerald-500/20 text-emerald-400 border border-emerald-500/30"
                      : "bg-rose-500/20 text-rose-400 border border-rose-500/30"
                  }`}
                >
                  {contractValid ? "VALID CONTRACT" : "CONTRACT INVALID"}
                </span>
              )}
            </div>
            <div className="flex items-center gap-3">
              <span className="text-[11px] text-slate-400 font-mono">
                Schema: <span className="text-white">{schemaHash.substring(0, 10) || "--"}</span>
              </span>
              <button
                onClick={handleFetch70d}
                className="px-3 py-1 rounded bg-cyan-500/20 text-cyan-400 hover:bg-cyan-500/30 border border-cyan-500/40 text-xs font-bold transition"
              >
                Fetch Live 70D
              </button>
            </div>
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-1.5 max-h-52 overflow-y-auto pr-1">
            {components70.map((slot) => (
              <div
                key={slot.index}
                className="flex items-center justify-between text-[11px] font-mono py-1 px-2 rounded bg-slate-950 border border-slate-800"
              >
                <span className="text-slate-500 w-6">{slot.index}</span>
                <span
                  className={`font-bold w-20 ${
                    slot.family === "BASE"
                      ? "text-sky-400"
                      : slot.family === "NEWS"
                      ? "text-amber-400"
                      : "text-emerald-400"
                  }`}
                >
                  [{slot.family}]
                </span>
                <span className="text-slate-300 flex-1 truncate">{slot.name}</span>
                <span className="text-white font-bold">{slot.value.toFixed(4)}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Inference Controls & Results */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Controls */}
        <div className="p-4 rounded-xl bg-slate-900 border border-slate-800 shadow-xl space-y-4">
          <h2 className="text-sm font-bold text-white flex items-center gap-2">
            <span>⚙</span> INFERENCE CONTROLS
          </h2>
          <div className="space-y-3 text-xs">
            <div className="flex items-center justify-between p-2 rounded bg-slate-950 border border-slate-800">
              <label htmlFor="live-toggle" className="text-slate-300 font-semibold cursor-pointer">
                Use Live Tick Stream
              </label>
              <input
                id="live-toggle"
                type="checkbox"
                checked={useLive}
                onChange={(e) => setUseLive(e.target.checked)}
                className="w-4 h-4 rounded text-cyan-400"
              />
            </div>
            <div>
              <label className="block text-slate-400 mb-1 font-semibold">Adversarial Noise Jitter (sigma)</label>
              <input
                type="number"
                step="0.05"
                min="0"
                max="1"
                value={noise}
                onChange={(e) => setNoise(parseFloat(e.target.value) || 0)}
                className="w-full bg-slate-950 border border-slate-800 rounded px-2.5 py-1.5 text-xs text-white"
              />
            </div>
            <div>
              <label className="block text-slate-400 mb-1 font-semibold">Signal Policy Threshold</label>
              <input
                type="number"
                step="0.05"
                min="0.1"
                max="0.9"
                value={threshold}
                onChange={(e) => setThreshold(parseFloat(e.target.value) || 0.35)}
                className="w-full bg-slate-950 border border-slate-800 rounded px-2.5 py-1.5 text-xs text-white"
              />
            </div>
            <button
              onClick={handleInference}
              disabled={loading}
              className="w-full py-2.5 rounded-lg bg-cyan-400 hover:bg-cyan-300 text-black font-extrabold text-xs transition shadow-lg flex items-center justify-center gap-2"
            >
              {loading ? "Evaluating..." : "⚡ Run Neural Inference"}
            </button>
          </div>

          {predictData && (
            <div className="pt-2 border-t border-slate-800 space-y-2 text-xs">
              <div className="flex justify-between">
                <span className="text-slate-500">Numerical Integrity:</span>
                <span
                  className={
                    predictData.numerical_validation.valid ? "text-emerald-400 font-bold" : "text-rose-400 font-bold"
                  }
                >
                  {predictData.numerical_validation.valid ? "VALID (Sum=1.0)" : "INVALID"}
                </span>
              </div>
              <div className="flex justify-between">
                <span className="text-slate-500">Distribution Health:</span>
                <span
                  className={
                    predictData.ood_metrics.is_out_of_distribution
                      ? "text-rose-400 font-bold"
                      : "text-emerald-400 font-bold"
                  }
                >
                  {predictData.ood_metrics.is_out_of_distribution ? "OOD DETECTED" : "NORMAL"} (z=
                  {predictData.ood_metrics.max_z_score})
                </span>
              </div>
              <div className="flex justify-between">
                <span className="text-slate-500">Inference Latency:</span>
                <span className="font-mono text-white">{predictData.latency_ms.total_e2e.toFixed(1)} ms</span>
              </div>
            </div>
          )}
        </div>

        {/* Prediction Results */}
        <div className="lg:col-span-2 p-4 rounded-xl bg-slate-900 border border-slate-800 shadow-xl space-y-4">
          <div className="flex items-center justify-between border-b border-slate-800 pb-2">
            <h2 className="text-sm font-bold text-white flex items-center gap-2">
              <span>📊</span> PROBABILITY DISTRIBUTION & DECISION
            </h2>
            <div
              className={`text-sm font-black px-3 py-1 rounded-lg border ${
                predictData?.predicted_label === "BUY_MARKET"
                  ? "text-emerald-400 bg-emerald-500/10 border-emerald-500/30"
                  : predictData?.predicted_label === "SELL_MARKET"
                  ? "text-rose-400 bg-rose-500/10 border-rose-500/30"
                  : "text-amber-400 bg-amber-500/10 border-amber-500/30"
              }`}
            >
              {predictData ? predictData.predicted_label : "AWAITING INFERENCE"}
            </div>
          </div>

          {predictData ? (
            <>
              {/* Bars */}
              <div className="space-y-3">
                <div>
                  <div className="flex justify-between text-xs font-semibold mb-1">
                    <span className="text-slate-400">NO_TRADE</span>
                    <span className="text-slate-300">
                      {(predictData.probabilities.no_trade * 100).toFixed(1)}%
                    </span>
                  </div>
                  <div className="w-full h-3 bg-slate-950 rounded-full overflow-hidden border border-slate-800">
                    <div
                      className="h-full bg-amber-400 transition-all duration-300"
                      style={{ width: `${predictData.probabilities.no_trade * 100}%` }}
                    />
                  </div>
                </div>
                <div>
                  <div className="flex justify-between text-xs font-semibold mb-1">
                    <span className="text-emerald-400">BUY_MARKET</span>
                    <span className="text-emerald-300">
                      {(predictData.probabilities.buy * 100).toFixed(1)}%
                    </span>
                  </div>
                  <div className="w-full h-3 bg-slate-950 rounded-full overflow-hidden border border-slate-800">
                    <div
                      className="h-full bg-emerald-400 transition-all duration-300"
                      style={{ width: `${predictData.probabilities.buy * 100}%` }}
                    />
                  </div>
                </div>
                <div>
                  <div className="flex justify-between text-xs font-semibold mb-1">
                    <span className="text-rose-400">SELL_MARKET</span>
                    <span className="text-rose-300">
                      {(predictData.probabilities.sell * 100).toFixed(1)}%
                    </span>
                  </div>
                  <div className="w-full h-3 bg-slate-950 rounded-full overflow-hidden border border-slate-800">
                    <div
                      className="h-full bg-rose-400 transition-all duration-300"
                      style={{ width: `${predictData.probabilities.sell * 100}%` }}
                    />
                  </div>
                </div>
              </div>

              {/* Calibration Stats */}
              <div className="grid grid-cols-3 gap-3 pt-2 text-center">
                <div className="p-2.5 rounded-lg bg-slate-950 border border-slate-800">
                  <div className="text-[10px] text-slate-500 uppercase font-bold">Top Confidence</div>
                  <div className="text-base font-extrabold text-white mt-1">
                    {(predictData.confidence * 100).toFixed(1)}%
                  </div>
                </div>
                <div className="p-2.5 rounded-lg bg-slate-950 border border-slate-800">
                  <div className="text-[10px] text-slate-500 uppercase font-bold">Top-2 Margin</div>
                  <div className="text-base font-extrabold text-cyan-400 mt-1">
                    {(predictData.confidence_margin * 100).toFixed(1)}%
                  </div>
                </div>
                <div className="p-2.5 rounded-lg bg-slate-950 border border-slate-800">
                  <div className="text-[10px] text-slate-500 uppercase font-bold">Shannon Entropy</div>
                  <div className="text-base font-extrabold text-amber-300 mt-1">
                    {predictData.shannon_entropy_bits.toFixed(4)} bits
                  </div>
                </div>
              </div>

              {/* Saliency */}
              <div className="pt-2 border-t border-slate-800 space-y-2">
                <div className="text-xs font-bold text-white flex items-center gap-1">
                  <span>✨</span> Top Feature Drivers (Gradient Saliency dScore/dX)
                </div>
                <div className="space-y-1 text-xs">
                  <div className="flex items-center gap-2">
                    <span className="text-[10px] font-bold text-emerald-400 w-16">Positive:</span>
                    <div className="flex flex-wrap gap-1.5">
                      {predictData.saliency?.top_positive_drivers?.map((d) => (
                        <span
                          key={d.index}
                          className="px-2 py-0.5 rounded text-[10px] font-mono bg-emerald-500/20 text-emerald-400 border border-emerald-500/30"
                        >
                          Feat[{d.index}]: +{d.gradient.toFixed(4)}
                        </span>
                      ))}
                    </div>
                  </div>
                  <div className="flex items-center gap-2">
                    <span className="text-[10px] font-bold text-rose-400 w-16">Negative:</span>
                    <div className="flex flex-wrap gap-1.5">
                      {predictData.saliency?.top_negative_drivers?.map((d) => (
                        <span
                          key={d.index}
                          className="px-2 py-0.5 rounded text-[10px] font-mono bg-rose-500/20 text-rose-400 border border-rose-500/30"
                        >
                          Feat[{d.index}]: {d.gradient.toFixed(4)}
                        </span>
                      ))}
                    </div>
                  </div>
                </div>
              </div>
            </>
          ) : (
            <div className="py-12 text-center text-xs text-slate-500">
              Click &quot;Run Neural Inference&quot; to test the model with current settings.
            </div>
          )}
        </div>
      </div>

      {/* Layer Activations Table */}
      {predictData?.layer_inspection && (
        <div className="p-4 rounded-xl bg-slate-900 border border-slate-800 shadow-xl space-y-3">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-bold text-white flex items-center gap-2">
              <span>🔬</span> LAYER-BY-LAYER NEURAL ACTIVATIONS & NORMS
            </h2>
            <span className="text-[11px] text-slate-400">Captured via PyTorch forward hooks</span>
          </div>
          <div className="overflow-x-auto border border-slate-800 rounded-lg">
            <table className="w-full text-left">
              <thead className="bg-slate-950 text-slate-400 text-[10px] uppercase border-b border-slate-800">
                <tr>
                  <th className="py-1.5 px-2">Layer Name</th>
                  <th className="py-1.5 px-2">Type</th>
                  <th className="py-1.5 px-2">Tensor Shape</th>
                  <th className="py-1.5 px-2">L2 Norm</th>
                  <th className="py-1.5 px-2">Mean</th>
                  <th className="py-1.5 px-2">Std Dev</th>
                  <th className="py-1.5 px-2">Zero Fraction</th>
                </tr>
              </thead>
              <tbody>
                {predictData.layer_inspection.map((l) => (
                  <tr
                    key={l.layer}
                    className="border-b border-slate-800/40 hover:bg-slate-800/20 text-[11px] font-mono"
                  >
                    <td className="py-1 px-2 text-white font-semibold">{l.layer}</td>
                    <td className="py-1 px-2 text-cyan-400">{l.type}</td>
                    <td className="py-1 px-2 text-slate-400">{l.shape.join("x")}</td>
                    <td className="py-1 px-2 text-emerald-300">{l.l2_norm}</td>
                    <td className="py-1 px-2 text-slate-300">{l.mean}</td>
                    <td className="py-1 px-2 text-slate-300">{l.std}</td>
                    <td className="py-1 px-2 text-amber-300">{(l.zero_fraction * 100).toFixed(1)}%</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* AI Hub: Model Registry, Hot-Loader & Runtime Lifecycle (API-First) */}
      <div className="p-4 rounded-xl bg-slate-900 border border-slate-800 shadow-xl space-y-4">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2 border-b border-slate-800 pb-3">
          <div className="flex items-center gap-2">
            <span className="text-xl text-emerald-400">⚡</span>
            <h2 className="text-sm font-bold text-white tracking-wide">
              AI HUB: MODEL REGISTRY & RUNTIME HOT-LOADER
            </h2>
            <span
              className={`px-2 py-0.5 rounded text-xs font-bold border ${
                activeModel
                  ? "bg-emerald-500/20 text-emerald-400 border-emerald-500/30"
                  : "bg-slate-800 text-slate-400 border-slate-700"
              }`}
            >
              {activeModel ? `CHAMPION: ${activeModel.model_id} (${activeModel.dimension}D)` : "CHAMPION: NONE"}
            </span>
          </div>
          <div className="flex items-center gap-2 text-xs">
            <span
              className={`px-2 py-0.5 rounded text-[10px] font-mono border ${
                activeModel?.fine_tune_enabled
                  ? "bg-purple-500/20 text-purple-300 border-purple-500/30"
                  : "bg-slate-800 text-slate-500 border-slate-700"
              }`}
            >
              {activeModel?.fine_tune_enabled ? "FINE-TUNE: ON" : "FINE-TUNE: OFF"}
            </span>
            <span
              className={`px-2 py-0.5 rounded text-[10px] font-mono border ${
                activeModel?.scaler_ready
                  ? "bg-cyan-500/20 text-cyan-400 border-cyan-500/30"
                  : "bg-amber-500/20 text-amber-300 border-amber-500/30"
              }`}
            >
              {activeModel?.scaler_ready ? "SCALER: ATTACHED" : "SCALER: STANDBY"}
            </span>
          </div>
        </div>

        <p className="text-xs text-slate-400">
          Dynamically hot-load neural model weights and calibrated scaler sidecars into live engine memory without server restart. Supports persistent SQLite tracking, pre-load verification, fine-tune gating, and 1-click rollback.
        </p>

        {/* Active Model Info Grid */}
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-2 text-center text-xs">
          <div className="p-2 rounded bg-slate-950 border border-slate-800">
            <div className="text-[10px] text-slate-500 uppercase font-bold">Active Model</div>
            <div className="font-bold text-emerald-400 text-xs mt-0.5 truncate">
              {activeModel?.model_id || "--"}
            </div>
          </div>
          <div className="p-2 rounded bg-slate-950 border border-slate-800">
            <div className="text-[10px] text-slate-500 uppercase font-bold">Dimension</div>
            <div className="font-bold text-white text-xs mt-0.5">
              {activeModel ? `${activeModel.dimension}D` : "--"}
            </div>
          </div>
          <div className="p-2 rounded bg-slate-950 border border-slate-800">
            <div className="text-[10px] text-slate-500 uppercase font-bold">Weights Hash</div>
            <div className="font-mono text-emerald-400 text-xs mt-0.5 truncate">
              {activeModel?.weights_sha256 ? activeModel.weights_sha256.substring(0, 12) : "--"}
            </div>
          </div>
          <div className="p-2 rounded bg-slate-950 border border-slate-800">
            <div className="text-[10px] text-slate-500 uppercase font-bold">Scaler Sidecar</div>
            <div className="font-mono text-cyan-400 text-xs mt-0.5 truncate">
              {activeModel?.scaler_path ? activeModel.scaler_path.split("/").pop() : "Default"}
            </div>
          </div>
          <div className="p-2 rounded bg-slate-950 border border-slate-800">
            <div className="text-[10px] text-slate-500 uppercase font-bold">Stage</div>
            <div className="font-bold text-white text-xs mt-0.5">
              {activeModel?.stage || "--"}
            </div>
          </div>
          <div className="p-2 rounded bg-slate-950 border border-slate-800">
            <div className="text-[10px] text-slate-500 uppercase font-bold">Inference Count</div>
            <div className="font-bold text-cyan-400 text-xs mt-0.5">
              {activeModel?.inference_count?.toLocaleString() ?? 0}
            </div>
          </div>
        </div>

        {/* Checkpoint Selection & Controls */}
        <div className="grid grid-cols-1 md:grid-cols-4 gap-3 text-xs items-end">
          <div className="md:col-span-2">
            <label className="block text-slate-400 mb-1 font-semibold">
              Select Model Checkpoint (SQLite Registry / Artifacts)
            </label>
            <select
              value={selectedModelId}
              onChange={(e) => setSelectedModelId(e.target.value)}
              className="w-full bg-slate-950 border border-slate-800 rounded px-2.5 py-2 text-white font-mono"
            >
              {models.length === 0 && <option value="">No registered models found</option>}
              {models.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.is_active ? "★ " : ""}{m.id} [{m.dimension}D] {m.fine_tune_enabled ? "[FT:ON]" : "[FT:OFF]"} (loss: {m.final_loss?.toFixed(4) || "0.0000"})
                </option>
              ))}
            </select>
          </div>

          <div className="flex flex-col justify-end space-y-1.5">
            <label className="flex items-center space-x-2 text-slate-300 font-semibold cursor-pointer">
              <input
                type="checkbox"
                checked={enableFineTune}
                onChange={(e) => setEnableFineTune(e.target.checked)}
                className="rounded bg-slate-950 border-slate-800 text-purple-600 focus:ring-purple-500"
              />
              <span>Enable Fine-Tune Mode</span>
            </label>
            <label className="flex items-center space-x-2 text-slate-300 font-semibold cursor-pointer">
              <input
                type="checkbox"
                checked={attachScaler}
                onChange={(e) => setAttachScaler(e.target.checked)}
                className="rounded bg-slate-950 border-slate-800 text-cyan-600 focus:ring-cyan-500"
              />
              <span>Auto-Load Scaler Sidecar</span>
            </label>
          </div>

          <div>
            <button
              onClick={handleHotLoad}
              disabled={hotLoadBusy || !selectedModelId}
              className="w-full px-4 py-2.5 rounded-lg bg-emerald-500 hover:bg-emerald-400 text-black font-extrabold text-xs transition shadow-lg flex items-center justify-center gap-1.5 disabled:opacity-50"
            >
              {hotLoadBusy ? "⚡ Hot-Loading..." : "⚡ Hot-Load Model"}
            </button>
          </div>
        </div>

        {/* Action Toolbar */}
        <div className="flex flex-wrap items-center gap-2 pt-1">
          <button
            onClick={handleVerifyModel}
            disabled={verifyBusy || !selectedModelId}
            className="px-3.5 py-2 rounded-lg bg-blue-500/20 text-blue-300 hover:bg-blue-500/30 border border-blue-500/40 font-bold text-xs transition flex items-center gap-1.5 disabled:opacity-50"
          >
            {verifyBusy ? "Verifying..." : "✓ Verify Integrity Battery"}
          </button>
          <button
            onClick={handleRollback}
            disabled={rollbackBusy}
            className="px-3.5 py-2 rounded-lg bg-amber-500/20 text-amber-300 hover:bg-amber-500/30 border border-amber-500/40 font-bold text-xs transition flex items-center gap-1.5 disabled:opacity-50"
          >
            {rollbackBusy ? "Rolling back..." : "↺ Rollback to Previous Champion"}
          </button>
          <button
            onClick={handleInspectScaler}
            disabled={!selectedModelId}
            className="px-3.5 py-2 rounded-lg bg-cyan-500/20 text-cyan-300 hover:bg-cyan-500/30 border border-cyan-500/40 font-bold text-xs transition flex items-center gap-1.5 disabled:opacity-50"
          >
            📊 Inspect Scaler Vectors
          </button>
        </div>

        {/* Hot-Load Success / Error Banner */}
        {hotLoadResult && (
          <div className="p-3 rounded-lg bg-slate-950 border border-emerald-500/40 text-xs space-y-1">
            <div className="flex items-center justify-between font-bold text-emerald-400">
              <span>✓ Model {hotLoadResult.model_id} ({hotLoadResult.dimension}D) Hot-Loaded</span>
              <span className="px-2 py-0.5 rounded text-[10px] font-mono bg-emerald-500/20">
                {hotLoadResult.warmup_latency_us} µs warmup
              </span>
            </div>
            <div className="text-[11px] text-slate-400 font-mono">
              SHA256: {hotLoadResult.weights_sha256} | Scaler: {hotLoadResult.scaler_attached ? hotLoadResult.scaler_path : "Unit"} | FT: {hotLoadResult.fine_tune_enabled ? "ON" : "OFF"}
            </div>
          </div>
        )}
        {hotLoadError && (
          <div className="p-3 rounded-lg bg-rose-500/10 border border-rose-500/30 text-rose-300 text-xs">
            Hot-load error: {hotLoadError}
          </div>
        )}

        {/* Verification Results Battery */}
        {verifyResult && (
          <div className="p-3 rounded-lg bg-slate-950 border border-slate-800 text-xs space-y-2">
            <div className="flex items-center justify-between font-bold">
              <span className="text-blue-400">Pre-Load Checkpoint Verification Results</span>
              <span
                className={`px-2 py-0.5 rounded text-[10px] font-bold border ${
                  verifyResult.all_passed
                    ? "bg-emerald-500/20 text-emerald-400 border-emerald-500/30"
                    : "bg-amber-500/20 text-amber-300 border-amber-500/30"
                }`}
              >
                {verifyResult.all_passed ? "ALL PASSED" : "WARNINGS"}
              </span>
            </div>
            <div className="overflow-x-auto border border-slate-800 rounded">
              <table className="w-full text-left font-mono text-[11px]">
                <thead className="bg-slate-900 text-slate-400 uppercase text-[10px]">
                  <tr>
                    <th className="py-1 px-2">Check Name</th>
                    <th className="py-1 px-2 text-center">Status</th>
                    <th className="py-1 px-2">Diagnostic Detail</th>
                  </tr>
                </thead>
                <tbody>
                  {verifyResult.checks.map((c, i) => (
                    <tr key={i} className="border-b border-slate-900 hover:bg-slate-900/40">
                      <td className="py-1 px-2 font-bold text-white">{c.name}</td>
                      <td className="py-1 px-2 text-center">
                        <span
                          className={`px-1.5 py-0.5 rounded text-[9px] font-bold ${
                            c.passed ? "bg-emerald-500/20 text-emerald-400" : "bg-rose-500/20 text-rose-400"
                          }`}
                        >
                          {c.passed ? "PASS" : "FAIL"}
                        </span>
                      </td>
                      <td className="py-1 px-2 text-slate-300">{c.detail}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {/* Scaler Vectors Table */}
        {scalerResult && (
          <div className="p-3 rounded-lg bg-slate-950 border border-slate-800 text-xs space-y-2">
            <div className="flex items-center justify-between font-bold border-b border-slate-800 pb-1.5">
              <span className="text-cyan-400">Attached Scaler Vectors ({scalerResult.dimension}D)</span>
              <span className="text-[10px] font-mono text-cyan-300">
                {scalerResult.features_count} features
              </span>
            </div>
            {scalerResult.features.length === 0 ? (
              <p className="text-slate-500 text-center py-2">{scalerResult.message || "No scaler data"}</p>
            ) : (
              <div className="overflow-x-auto max-h-48 overflow-y-auto border border-slate-800 rounded">
                <table className="w-full text-left font-mono text-[11px]">
                  <thead className="bg-slate-900 text-slate-400 uppercase text-[10px] sticky top-0">
                    <tr>
                      <th className="py-1 px-2"># Index</th>
                      <th className="py-1 px-2 text-right">Mean (μ)</th>
                      <th className="py-1 px-2 text-right">Std Dev (σ)</th>
                      <th className="py-1 px-2 text-center">Clamping</th>
                      <th className="py-1 px-2 text-center">Zero Variance</th>
                    </tr>
                  </thead>
                  <tbody>
                    {scalerResult.features.map((f) => (
                      <tr key={f.index} className="border-b border-slate-900 hover:bg-slate-900/40">
                        <td className="py-1 px-2 text-slate-400">feat_{f.index}</td>
                        <td className="py-1 px-2 text-right text-emerald-400">{f.mean.toFixed(4)}</td>
                        <td className="py-1 px-2 text-right text-cyan-400">{f.std.toFixed(4)}</td>
                        <td className="py-1 px-2 text-center text-slate-500">[{f.clamp_min}, {f.clamp_max}]</td>
                        <td className="py-1 px-2 text-center">
                          <span
                            className={`px-1.5 py-0.5 rounded text-[9px] font-bold ${
                              f.zero_variance ? "bg-amber-500/20 text-amber-300" : "bg-emerald-500/20 text-emerald-400"
                            }`}
                          >
                            {f.zero_variance ? "YES" : "NO"}
                          </span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}
      </div>

      {/* Dataset Download & Ingestion (API-First) */}
      <div className="p-4 rounded-xl bg-slate-900 border border-slate-800 shadow-xl space-y-3">
        <div className="flex items-center justify-between border-b border-slate-800 pb-2">
          <h2 className="text-sm font-bold text-white flex items-center gap-2">
            <span>📥</span> DATASET DOWNLOAD & HISTORICAL MARKET INGESTION
          </h2>
          <span className="text-xs text-cyan-400 font-semibold">API First</span>
        </div>
        <p className="text-xs text-slate-400">
          Download and ingest historical market candles (1m, 3m, 5m, 15m) with automatic schema normalization and UTC monotonicity verification.
        </p>

        <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-4 gap-3 text-xs">
          <div>
            <label className="block text-slate-400 mb-1 font-semibold">Symbol</label>
            <input
              type="text"
              value={dlSymbol}
              onChange={(e) => setDlSymbol(e.target.value.toUpperCase())}
              className="w-full bg-slate-950 border border-slate-800 rounded px-2.5 py-2 text-white uppercase font-mono"
            />
          </div>
          <div>
            <label className="block text-slate-400 mb-1 font-semibold">Timeframe</label>
            <select
              value={dlTimeframe}
              onChange={(e) => setDlTimeframe(e.target.value)}
              className="w-full bg-slate-950 border border-slate-800 rounded px-2.5 py-2 text-white font-mono"
            >
              <option value="M1">1 Minute (M1)</option>
              <option value="M3">3 Minutes (M3)</option>
              <option value="M5">5 Minutes (M5)</option>
              <option value="M15">15 Minutes (M15)</option>
            </select>
          </div>
          <div>
            <label className="block text-slate-400 mb-1 font-semibold">Candle Count (Bars)</label>
            <input
              type="number"
              min={500}
              max={200000}
              step={1000}
              value={dlBars}
              onChange={(e) => setDlBars(parseInt(e.target.value, 10) || 10000)}
              className="w-full bg-slate-950 border border-slate-800 rounded px-2.5 py-2 text-white font-mono"
            />
          </div>
          <div>
            <label className="block text-slate-400 mb-1 font-semibold">Source Adapter</label>
            <select
              value={dlSource}
              onChange={(e) => setDlSource(e.target.value)}
              className="w-full bg-slate-950 border border-slate-800 rounded px-2.5 py-2 text-white"
            >
              <option value="synthetic">Synthetic (Realistic GBM + Microstructure)</option>
              <option value="mt5">MetaTrader 5 (Live Terminal Feed)</option>
              <option value="csv">CSV Import</option>
            </select>
          </div>
        </div>

        <div className="flex items-center gap-3 pt-1">
          <button
            onClick={handleDownloadDataset}
            disabled={dlBusy}
            className="px-5 py-2 rounded-lg bg-cyan-500 hover:bg-cyan-400 disabled:opacity-50 text-black font-extrabold text-xs transition shadow-lg flex items-center gap-2"
          >
            <span>{dlBusy ? "⏳" : "⬇"}</span> {dlBusy ? "Downloading..." : "Download Dataset"}
          </button>
        </div>

        {dlError && (
          <div className="p-2.5 rounded bg-rose-500/10 border border-rose-500/30 text-rose-400 text-xs">
            {dlError}
          </div>
        )}

        {dlResult && (
          <div className="p-3 rounded-lg bg-slate-950 border border-slate-800 text-xs space-y-1">
            <div className="flex items-center justify-between font-bold">
              <span className="text-emerald-400">✓ {dlResult.message}</span>
              <span className="px-2 py-0.5 rounded text-[10px] bg-cyan-500/20 text-cyan-400 border border-cyan-500/30">
                {dlResult.rows.toLocaleString()} bars | {dlResult.size_display}
              </span>
            </div>
            <div className="text-[11px] text-slate-400 font-mono">
              Saved: {dlResult.dataset_path} | Throughput: {Math.round(dlResult.throughput_bars_sec)} bars/s | Elapsed: {dlResult.elapsed_sec.toFixed(2)}s
            </div>
          </div>
        )}
      </div>

      {/* Model Training Dispatch & Feature Inspection */}
      <div className="p-4 rounded-xl bg-slate-900 border border-slate-800 shadow-xl space-y-4">
        <div className="flex items-center justify-between border-b border-slate-800 pb-2">
          <h2 className="text-sm font-bold text-white flex items-center gap-2">
            <span>🎓</span> MODEL TRAINING DISPATCH & FEATURE INSPECTION
          </h2>
          <span className="text-xs text-emerald-400 font-semibold">Algorithm Cycle Ready</span>
        </div>
        <p className="text-xs text-slate-400">
          Select an ingested dataset to inspect/normalize 50D & 70D features and train a new model candidate.
        </p>
        <div className="grid grid-cols-1 md:grid-cols-4 gap-3 text-xs">
          <div className="md:col-span-2">
            <label className="block text-slate-400 mb-1 font-semibold">Select Dataset</label>
            <select
              value={selectedDataset}
              onChange={(e) => setSelectedDataset(e.target.value)}
              className="w-full bg-slate-950 border border-slate-800 rounded px-2.5 py-2 text-white font-mono"
            >
              {datasets.map((d) => (
                <option key={d.path} value={d.path}>
                  {d.name} ({d.size_display})
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="block text-slate-400 mb-1 font-semibold">Epochs</label>
            <input
              type="number"
              min="1"
              max="50"
              value={epochs}
              onChange={(e) => setEpochs(parseInt(e.target.value) || 3)}
              className="w-full bg-slate-950 border border-slate-800 rounded px-2.5 py-2 text-white"
            />
          </div>
          <div>
            <label className="block text-slate-400 mb-1 font-semibold">Learning Rate</label>
            <input
              type="number"
              step="0.0001"
              value={learningRate}
              onChange={(e) => setLearningRate(parseFloat(e.target.value) || 0.0005)}
              className="w-full bg-slate-950 border border-slate-800 rounded px-2.5 py-2 text-white"
            />
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-3 pt-1">
          <button
            onClick={handleStartTrain}
            className="px-5 py-2.5 rounded-lg bg-emerald-500 hover:bg-emerald-400 text-black font-extrabold text-xs transition shadow-lg flex items-center gap-2"
          >
            <span>▶</span> Start Model Training
          </button>
          <button
            onClick={handleInspectFeatures}
            disabled={inspectBusy}
            className="px-4 py-2.5 rounded-lg bg-blue-500/20 text-blue-300 hover:bg-blue-500/30 border border-blue-500/40 font-bold text-xs transition flex items-center gap-2"
          >
            <span>{inspectBusy ? "⏳" : "🔍"}</span> Inspect & Normalize Features (50D / 70D)
          </button>
        </div>

        {inspectError && (
          <div className="p-2.5 rounded bg-rose-500/10 border border-rose-500/30 text-rose-400 text-xs">
            {inspectError}
          </div>
        )}

        {trainError && (
          <div className="p-2.5 rounded bg-rose-500/10 border border-rose-500/30 text-rose-400 text-xs">
            {trainError}
          </div>
        )}

        {trainStatus && (
          <div className="p-3 rounded-lg bg-slate-950 border border-slate-800 space-y-2 text-xs">
            <div className="text-white font-bold">{trainStatus}</div>
            <div className="w-full h-2.5 bg-slate-800 rounded-full overflow-hidden">
              <div
                className="h-full bg-emerald-500 transition-all duration-300"
                style={{ width: `${trainProgress}%` }}
              />
            </div>
          </div>
        )}

        {/* Feature Inspection Results Panel */}
        {inspectResult && (
          <div className="p-3 rounded-lg bg-slate-950 border border-slate-800 space-y-3 text-xs">
            <div className="flex items-center justify-between border-b border-slate-800 pb-2">
              <div className="font-bold text-white flex items-center gap-2">
                <span>📊</span> Feature Normalization & Inspection Report ({inspectResult.dimension}D)
              </div>
              <div className="flex gap-2">
                <span className="px-2 py-0.5 rounded text-[10px] bg-blue-500/20 text-blue-300 border border-blue-500/30">
                  {inspectResult.rows_processed} Rows
                </span>
                <span className="px-2 py-0.5 rounded text-[10px] bg-emerald-500/20 text-emerald-400 border border-emerald-500/30">
                  {inspectResult.healthy_features} Healthy
                </span>
              </div>
            </div>

            <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 text-center">
              <div className="p-2 rounded bg-slate-900 border border-slate-800">
                <div className="text-[10px] text-slate-500 uppercase font-bold">Total Features</div>
                <div className="font-bold text-white text-sm mt-0.5">{inspectResult.total_features}</div>
              </div>
              <div className="p-2 rounded bg-slate-900 border border-slate-800">
                <div className="text-[10px] text-slate-500 uppercase font-bold">Healthy</div>
                <div className="font-bold text-emerald-400 text-sm mt-0.5">{inspectResult.healthy_features}</div>
              </div>
              <div className="p-2 rounded bg-slate-900 border border-slate-800">
                <div className="text-[10px] text-slate-500 uppercase font-bold">Clamped (0-Var)</div>
                <div className="font-bold text-amber-400 text-sm mt-0.5">{inspectResult.clamped_features}</div>
              </div>
              <div className="p-2 rounded bg-slate-900 border border-slate-800">
                <div className="text-[10px] text-slate-500 uppercase font-bold">Scaler Status</div>
                <div className="font-bold text-cyan-400 text-sm mt-0.5">READY (Z-Score)</div>
              </div>
            </div>

            <div className="overflow-x-auto max-h-56 overflow-y-auto border border-slate-800 rounded">
              <table className="w-full text-left font-mono text-[11px]">
                <thead className="bg-slate-900 text-slate-400 uppercase text-[10px] sticky top-0">
                  <tr>
                    <th className="py-1.5 px-2">#</th>
                    <th className="py-1.5 px-2">Family</th>
                    <th className="py-1.5 px-2">Feature Name</th>
                    <th className="py-1.5 px-2 text-right">Raw Mean</th>
                    <th className="py-1.5 px-2 text-right">Raw Std</th>
                    <th className="py-1.5 px-2 text-right">Norm Sample</th>
                    <th className="py-1.5 px-2 text-center">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {inspectResult.features.slice(0, 30).map((f) => (
                    <tr key={f.index} className="border-b border-slate-800/40 hover:bg-slate-900/40">
                      <td className="py-1 px-2 text-slate-500">{f.index}</td>
                      <td className={`py-1 px-2 font-bold ${f.family === "BASE" ? "text-cyan-400" : f.family === "NEWS" ? "text-amber-400" : "text-purple-400"}`}>
                        {f.family}
                      </td>
                      <td className="py-1 px-2 text-white">{f.name}</td>
                      <td className="py-1 px-2 text-right text-slate-300">{f.raw_mean.toFixed(3)}</td>
                      <td className="py-1 px-2 text-right text-slate-300">{f.raw_std.toFixed(3)}</td>
                      <td className="py-1 px-2 text-right text-emerald-400">{f.normalized_sample.toFixed(3)}</td>
                      <td className="py-1 px-2 text-center">
                        <span className={`px-1.5 py-0.5 rounded text-[10px] font-bold ${f.status === "HEALTHY" ? "text-emerald-400 bg-emerald-500/10" : "text-amber-400 bg-amber-500/10"}`}>
                          {f.status}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </div>

      {/* Layer-2 ML: Position Management Dataset Generator */}
      <div className="p-4 rounded-xl bg-slate-900 border border-slate-800 shadow-xl space-y-3">
        <div className="flex items-center justify-between border-b border-slate-800 pb-2">
          <h2 className="text-sm font-bold text-white flex items-center gap-2">
            <span>🧩</span> LAYER-2 ML: POSITION MANAGEMENT DATASET GENERATOR
          </h2>
          <span className="text-xs text-purple-400 font-semibold">Risk & Continuation Value Model</span>
        </div>
        <p className="text-xs text-slate-400">
          Simulate trade decisions across historical bars to generate position-state samples (unrealized R, position age, ATR) with anti-leakage mathematical labeling (KEEP / CLOSE / REDUCE) and chronological purge/embargo splitting.
        </p>

        <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-4 gap-3 text-xs">
          <div>
            <label className="block text-slate-400 mb-1 font-semibold">Source Market Dataset</label>
            <select
              value={posSource}
              onChange={(e) => setPosSource(e.target.value)}
              className="w-full bg-slate-950 border border-slate-800 rounded px-2.5 py-2 text-white font-mono"
            >
              {datasets.map((d) => (
                <option key={d.path} value={d.path}>
                  {d.name} ({d.size_display})
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="block text-slate-400 mb-1 font-semibold">Max Holding Horizon (Bars)</label>
            <input
              type="number"
              min={5}
              max={120}
              value={posMaxHolding}
              onChange={(e) => setPosMaxHolding(parseInt(e.target.value, 10) || 30)}
              className="w-full bg-slate-950 border border-slate-800 rounded px-2.5 py-2 text-white font-mono"
            />
          </div>
          <div>
            <label className="block text-slate-400 mb-1 font-semibold">Target ATR Multiplier</label>
            <input
              type="number"
              step="0.5"
              min="0.5"
              max="10"
              value={posTargetAtr}
              onChange={(e) => setPosTargetAtr(parseFloat(e.target.value) || 2.0)}
              className="w-full bg-slate-950 border border-slate-800 rounded px-2.5 py-2 text-white font-mono"
            />
          </div>
          <div>
            <label className="block text-slate-400 mb-1 font-semibold">Friction (Pips)</label>
            <input
              type="number"
              step="0.05"
              min="0"
              max="5"
              value={posFriction}
              onChange={(e) => setPosFriction(parseFloat(e.target.value) || 0.25)}
              className="w-full bg-slate-950 border border-slate-800 rounded px-2.5 py-2 text-white font-mono"
            />
          </div>
        </div>

        <div className="flex items-center gap-3 pt-1">
          <button
            onClick={handleGeneratePositionDataset}
            disabled={posBusy}
            className="px-5 py-2.5 rounded-lg bg-purple-600 hover:bg-purple-500 disabled:opacity-50 text-white font-extrabold text-xs transition shadow-lg flex items-center gap-2"
          >
            <span>{posBusy ? "⏳" : "⚙"}</span> {posBusy ? "Simulating & Generating..." : "Generate Position Dataset"}
          </button>
        </div>

        {posError && (
          <div className="p-2.5 rounded bg-rose-500/10 border border-rose-500/30 text-rose-400 text-xs">
            {posError}
          </div>
        )}

        {posResult && (
          <div className="p-3 rounded-lg bg-slate-950 border border-slate-800 space-y-3 text-xs">
            <div className="flex items-center justify-between font-bold">
              <span className="text-purple-400 flex items-center gap-1.5">✓ Position Dataset Generated</span>
              <span className="px-2 py-0.5 rounded text-[10px] font-mono bg-purple-500/20 text-purple-300 border border-purple-500/30">
                {(posResult.sha256 || "").substring(0, 16)}
              </span>
            </div>

            <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 text-center">
              <div className="p-2 rounded bg-slate-900 border border-slate-800">
                <div className="text-[10px] text-slate-500 uppercase font-bold">Total Samples</div>
                <div className="font-bold text-white text-sm mt-0.5">{posResult.total_samples.toLocaleString()}</div>
              </div>
              <div className="p-2 rounded bg-slate-900 border border-slate-800">
                <div className="text-[10px] text-slate-500 uppercase font-bold">Simulated Trades</div>
                <div className="font-bold text-cyan-400 text-sm mt-0.5">{posResult.simulated_trades.toLocaleString()}</div>
              </div>
              <div className="p-2 rounded bg-slate-900 border border-slate-800">
                <div className="text-[10px] text-slate-500 uppercase font-bold">Mean Cont. Value</div>
                <div className="font-bold text-emerald-400 text-sm mt-0.5">
                  {(posResult.mean_continuation_value >= 0 ? "+" : "") + posResult.mean_continuation_value.toFixed(3)} R
                </div>
              </div>
              <div className="p-2 rounded bg-slate-900 border border-slate-800">
                <div className="text-[10px] text-slate-500 uppercase font-bold">Mean Hold Bars</div>
                <div className="font-bold text-amber-300 text-sm mt-0.5">{posResult.mean_holding_bars.toFixed(1)} bars</div>
              </div>
            </div>

            <div className="flex flex-wrap items-center justify-between gap-2 pt-1 border-t border-slate-800 text-[11px] font-mono">
              <div className="flex gap-2">
                <span className="px-2 py-0.5 rounded bg-emerald-500/20 text-emerald-400 border border-emerald-500/30">
                  KEEP: {posResult.actions_distribution?.KEEP || 0}
                </span>
                <span className="px-2 py-0.5 rounded bg-rose-500/20 text-rose-400 border border-rose-500/30">
                  CLOSE: {posResult.actions_distribution?.CLOSE || 0}
                </span>
                <span className="px-2 py-0.5 rounded bg-amber-500/20 text-amber-300 border border-amber-500/30">
                  REDUCE: {posResult.actions_distribution?.REDUCE || 0}
                </span>
              </div>
              <div className="text-slate-400 truncate max-w-xs sm:max-w-md">
                Saved: {posResult.dataset_path}
              </div>
            </div>
          </div>
        )}
      </div>

      {/* Stress Test & Benchmark Suite */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        {/* Stress */}
        <div className="p-4 rounded-xl bg-slate-900 border border-slate-800 shadow-xl space-y-3">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-bold text-white flex items-center gap-2">
              <span>🛡</span> ADVERSARIAL STRESS TEST
            </h2>
            <button
              onClick={handleStressTest}
              className="px-3 py-1.5 rounded-lg bg-pink-500/20 text-pink-300 hover:bg-pink-500/30 border border-pink-500/40 text-xs font-bold transition"
            >
              Run Stress Battery
            </button>
          </div>
          <div className="overflow-x-auto border border-slate-800 rounded-lg">
            <table className="w-full text-left">
              <thead className="bg-slate-950 text-slate-400 text-[10px] uppercase border-b border-slate-800">
                <tr>
                  <th className="py-1.5 px-2">Scenario</th>
                  <th className="py-1.5 px-2">Verdict</th>
                  <th className="py-1.5 px-2">Detail</th>
                </tr>
              </thead>
              <tbody>
                {stressResults.length > 0 ? (
                  stressResults.map((r) => (
                    <tr key={r.test} className="border-b border-slate-800 text-[11px] font-mono">
                      <td className="py-1 px-2 text-white">{r.test}</td>
                      <td className={`py-1 px-2 font-bold ${r.passed ? "text-emerald-400" : "text-rose-400"}`}>
                        {r.passed ? "PASS" : "FAIL"}
                      </td>
                      <td className="py-1 px-2 text-slate-400">{r.detail}</td>
                    </tr>
                  ))
                ) : (
                  <tr>
                    <td colSpan={3} className="py-3 text-center text-xs text-slate-500">
                      Click &quot;Run Stress Battery&quot; to test adversarial scenarios.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </div>

        {/* Benchmark */}
        <div className="p-4 rounded-xl bg-slate-900 border border-slate-800 shadow-xl space-y-3">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-bold text-white flex items-center gap-2">
              <span>⏱</span> LATENCY BENCHMARK
            </h2>
            <button
              onClick={handleBenchmark}
              className="px-3 py-1.5 rounded-lg bg-cyan-500/20 text-cyan-300 hover:bg-cyan-500/30 border border-cyan-500/40 text-xs font-bold transition"
            >
              Profile 100 Passes
            </button>
          </div>
          {benchStats ? (
            <div className="grid grid-cols-4 gap-2 pt-2 text-center text-xs">
              <div className="p-2 rounded bg-slate-950 border border-slate-800">
                <div className="text-[10px] text-slate-500 uppercase font-bold">P50</div>
                <div className="font-mono text-white font-bold mt-1">{benchStats.latency_p50_ms} ms</div>
              </div>
              <div className="p-2 rounded bg-slate-950 border border-slate-800">
                <div className="text-[10px] text-slate-500 uppercase font-bold">P90</div>
                <div className="font-mono text-white font-bold mt-1">{benchStats.latency_p90_ms} ms</div>
              </div>
              <div className="p-2 rounded bg-slate-950 border border-slate-800">
                <div className="text-[10px] text-slate-500 uppercase font-bold">P99</div>
                <div className="font-mono text-emerald-400 font-bold mt-1">{benchStats.latency_p99_ms} ms</div>
              </div>
              <div className="p-2 rounded bg-slate-950 border border-slate-800">
                <div className="text-[10px] text-slate-500 uppercase font-bold">Throughput</div>
                <div className="font-mono text-cyan-400 font-bold mt-1">
                  {benchStats.throughput_inferences_per_sec} inf/s
                </div>
              </div>
            </div>
          ) : (
            <div className="py-6 text-center text-xs text-slate-500">
              Click &quot;Profile 100 Passes&quot; to measure execution speed.
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
