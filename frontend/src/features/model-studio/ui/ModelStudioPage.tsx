import React, { useState, useEffect } from "react";

interface StudioOverview {
  architecture?: string;
  effective_dimension?: number;
  model_source?: string;
  parameter_count?: number;
  trainable_parameters?: number;
  weights_sha256?: string;
  device?: string;
  scaler_stats?: {
    status?: string;
    mean_min?: number;
    mean_max?: number;
    std_min?: number;
    std_max?: number;
    clamped_cols?: number;
  };
}

interface DatasetItem {
  name: string;
  path: string;
  size_display: string;
  format: string;
}

interface LayerStat {
  layer: string;
  type: string;
  shape: number[];
  l2_norm: number;
  mean: number;
  std: number;
  zero_fraction: number;
}

interface SaliencyDriver {
  index: number;
  gradient: number;
}

interface PredictResponse {
  status: string;
  dimension: number;
  confidence: number;
  confidence_margin: number;
  shannon_entropy_bits: number;
  predicted_label: string;
  probabilities: {
    no_trade: number;
    buy: number;
    sell: number;
  };
  numerical_validation: {
    valid: boolean;
    sum: number;
    all_positive: boolean;
  };
  ood_metrics: {
    max_z_score: number;
    is_out_of_distribution: boolean;
  };
  latency_ms: {
    total_e2e: number;
  };
  layer_inspection?: LayerStat[];
  saliency?: {
    top_positive_drivers?: SaliencyDriver[];
    top_negative_drivers?: SaliencyDriver[];
  };
}

interface StressTestResult {
  test: string;
  passed: boolean;
  detail: string;
}

export default function ModelStudioPage() {
  const [dimension, setDimension] = useState<number>(50);
  const [overview, setOverview] = useState<StudioOverview | null>(null);
  const [datasets, setDatasets] = useState<DatasetItem[]>([]);
  const [loading, setLoading] = useState<boolean>(false);
  const [useLive, setUseLive] = useState<boolean>(true);
  const [noise, setNoise] = useState<number>(0);
  const [threshold, setThreshold] = useState<number>(0.35);
  const [predictData, setPredictData] = useState<PredictResponse | null>(null);

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

  // Stress & Benchmark
  const [stressResults, setStressResults] = useState<StressTestResult[]>([]);
  const [benchStats, setBenchStats] = useState<any>(null);

  useEffect(() => {
    fetchOverview();
    fetchDatasets();
  }, []);

  const fetchOverview = async () => {
    try {
      const res = await fetch("/api/model-studio/overview");
      if (res.ok) {
        const d = await res.json();
        setOverview(d);
      }
    } catch (err) {
      console.error("Failed to load overview:", err);
    }
  };

  const fetchDatasets = async () => {
    try {
      const res = await fetch("/api/model-studio/datasets");
      if (res.ok) {
        const d = await res.json();
        setDatasets(d.datasets || []);
        if (d.datasets && d.datasets.length > 0) {
          setSelectedDataset(d.datasets[0].path);
        }
      }
    } catch (err) {
      console.error("Failed to load datasets:", err);
    }
  };

  const handleFetch70d = async () => {
    try {
      const res = await fetch("/api/model-studio/fetch-70d");
      if (res.ok) {
        const d = await res.json();
        setComponents70(d.slots || []);
        setContractValid(d.contract_valid);
        setSchemaHash(d.schema_hash || "");
      }
    } catch (err) {
      console.error("Failed to fetch 70D:", err);
    }
  };

  const handleInference = async () => {
    setLoading(true);
    try {
      const res = await fetch("/api/model-studio/predict", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          dimension,
          use_live_features: useLive,
          fetch_live_70d: dimension === 70,
          perturbation_sigma: noise,
          simulate_policy_threshold: threshold,
          inspect_layers: true,
          compute_saliency: true,
        }),
      });
      if (res.ok) {
        const d = await res.json();
        setPredictData(d);
      }
    } catch (err) {
      console.error("Inference failed:", err);
    } finally {
      setLoading(false);
    }
  };

  const handleStressTest = async () => {
    try {
      const res = await fetch("/api/model-studio/stress-test", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ dimension }),
      });
      if (res.ok) {
        const d = await res.json();
        setStressResults(d.results || []);
      }
    } catch (err) {
      console.error("Stress test failed:", err);
    }
  };

  const handleBenchmark = async () => {
    try {
      const res = await fetch("/api/model-studio/benchmark", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ dimension, iterations: 100 }),
      });
      if (res.ok) {
        const d = await res.json();
        setBenchStats(d);
      }
    } catch (err) {
      console.error("Benchmark failed:", err);
    }
  };

  const handleStartTrain = async () => {
    try {
      const res = await fetch("/api/model-studio/train", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          dataset_path: selectedDataset,
          dimension,
          epochs,
          batch_size: 256,
          learning_rate: learningRate,
        }),
      });
      if (res.ok) {
        const d = await res.json();
        setTrainStatus(`Dispatched: ${d.message}`);
        setTrainProgress(33);
      }
    } catch (err) {
      console.error("Training trigger failed:", err);
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

      {/* Model Training Dispatch */}
      <div className="p-4 rounded-xl bg-slate-900 border border-slate-800 shadow-xl space-y-4">
        <div className="flex items-center justify-between border-b border-slate-800 pb-2">
          <h2 className="text-sm font-bold text-white flex items-center gap-2">
            <span>🎓</span> MODEL TRAINING DISPATCH & DATASET SELECTION
          </h2>
          <span className="text-xs text-emerald-400 font-semibold">Algorithm Cycle Ready</span>
        </div>
        <p className="text-xs text-slate-400">
          Select an ingested dataset to train a new model candidate directly into the trading algorithm cycle.
        </p>
        <div className="grid grid-cols-1 md:grid-cols-4 gap-3 text-xs">
          <div className="md:col-span-2">
            <label className="block text-slate-400 mb-1 font-semibold">Select Dataset</label>
            <select
              value={selectedDataset}
              onChange={(e) => setSelectedDataset(e.target.value)}
              className="w-full bg-slate-950 border border-slate-800 rounded px-2.5 py-2 text-white"
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
        <button
          onClick={handleStartTrain}
          className="px-5 py-2.5 rounded-lg bg-emerald-500 hover:bg-emerald-400 text-black font-extrabold text-xs transition shadow-lg flex items-center gap-2"
        >
          <span>▶</span> Start Model Training
        </button>
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
