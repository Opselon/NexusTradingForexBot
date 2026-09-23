/**
 * ModelStudioPage — thin orchestrator for the Neural Model Studio.
 *
 * All owned state + backend effects live here; rendering is delegated to the
 * stateless pro panels in ./ (ModelStudioHeader, ModelInferencePanel,
 * ModelRegistryPanel, DatasetPipelinePanel, PositionDatasetPanel,
 * ModelStressBenchPanel) styled by ./model-studio.css.
 *
 * Behavior contract preserved from the pre-swap monolith: same initial fetch
 * set, same request payloads, same result/error surfacing, same fallback
 * parsing of form input. Additions are presentation-only busy flags
 * (train/stress/benchmark) and a poll of the existing
 * GET /api/model-studio/train/progress endpoint so the pipeline panel can
 * render live epoch progress while the blocking train POST is in flight.
 *
 * Render cost (perf lane): every prop handed to a panel is stable — handlers
 * are useCallback'd over their real deps and the six panels are memo-wrapped
 * at the import site — so the 1.2s training-progress tick re-renders only
 * DatasetPipelinePanel instead of the whole screen.
 */

import { memo, useCallback, useEffect, useRef, useState } from "react";

import "./model-studio.css";

import { modelStudioApi } from "../api";
import type {
  BenchmarkResponse,
  DatasetDownloadResponse,
  Fetch70dResponse,
  HotLoadResponse,
  InspectFeaturesResponse,
  InspectScalerResponse,
  ModelStudioTrainProgress,
  PositionDatasetResponse,
  PredictResponse,
  StressTestResultRow,
  VerifyModelResponse,
} from "../model";
import { ModelStudioHeader as ModelStudioHeaderBase } from "./ModelStudioHeader";
import { ModelInferencePanel as ModelInferencePanelBase } from "./ModelInferencePanel";
import { ModelRegistryPanel as ModelRegistryPanelBase } from "./ModelRegistryPanel";
import { DatasetPipelinePanel as DatasetPipelinePanelBase } from "./DatasetPipelinePanel";
import { PositionDatasetPanel as PositionDatasetPanelBase } from "./PositionDatasetPanel";
import { ModelStressBenchPanel as ModelStressBenchPanelBase } from "./ModelStressBenchPanel";
import { useModelStudioData } from "./useModelStudioData";

/* memo at the call site: identical components, but they skip re-renders when
 * their props are unchanged (stable callbacks make that effective). */
const ModelStudioHeader = memo(ModelStudioHeaderBase);
const ModelInferencePanel = memo(ModelInferencePanelBase);
const ModelRegistryPanel = memo(ModelRegistryPanelBase);
const DatasetPipelinePanel = memo(DatasetPipelinePanelBase);
const PositionDatasetPanel = memo(PositionDatasetPanelBase);
const ModelStressBenchPanel = memo(ModelStressBenchPanelBase);

/** Poll cadence for the live training-progress endpoint (visual only). */
const TRAIN_POLL_MS = 1200;

export default function ModelStudioPage() {
  const {
    overview,
    datasets,
    models,
    activeModel,
    selectedModelId,
    setSelectedModelId,
    selectedDataset,
    setSelectedDataset,
    posSource,
    setPosSource,
    enableFineTune,
    setEnableFineTune,
    fetchOverview,
    fetchDatasets,
    fetchModels,
    fetchActiveModel,
    refreshAll,
    isLoading,
    loadError,
    loadErrorSource,
    refetchAll,
  } = useModelStudioData();
  const [dimension, setDimension] = useState<number>(50);
  const [loading, setLoading] = useState<boolean>(false);
  const [useLive, setUseLive] = useState<boolean>(true);
  const [noise, setNoise] = useState<number>(0);
  const [threshold, setThreshold] = useState<number>(0.35);
  const [predictData, setPredictData] = useState<PredictResponse | null>(null);

  // AI Hub / Model Registry & Hot-Loader state
  const [attachScaler, setAttachScaler] = useState<boolean>(true);
  const [hotLoadBusy, setHotLoadBusy] = useState<boolean>(false);
  const [hotLoadResult, setHotLoadResult] = useState<HotLoadResponse | null>(null);
  const [hotLoadError, setHotLoadError] = useState<string>("");
  const [verifyBusy, setVerifyBusy] = useState<boolean>(false);
  const [verifyResult, setVerifyResult] = useState<VerifyModelResponse | null>(null);
  const [scalerResult, setScalerResult] = useState<InspectScalerResponse | null>(null);
  const [rollbackBusy, setRollbackBusy] = useState<boolean>(false);

  // 70D components
  const [components70, setComponents70] = useState<NonNullable<Fetch70dResponse["slots"]>>([]);
  const [contractValid, setContractValid] = useState<boolean | null>(null);
  const [schemaHash, setSchemaHash] = useState<string>("");

  // Training state
  const [epochs, setEpochs] = useState<number>(3);
  const [learningRate, setLearningRate] = useState<number>(0.0005);
  const [trainBusy, setTrainBusy] = useState<boolean>(false);
  const [trainProgress, setTrainProgress] = useState<ModelStudioTrainProgress | null>(null);
  const [trainStatus, setTrainStatus] = useState<string>("");
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
  const [posMaxHolding, setPosMaxHolding] = useState<number>(30);
  const [posTargetAtr, setPosTargetAtr] = useState<number>(2.0);
  const [posFriction, setPosFriction] = useState<number>(0.25);
  const [posBusy, setPosBusy] = useState<boolean>(false);
  const [posResult, setPosResult] = useState<PositionDatasetResponse | null>(null);
  const [posError, setPosError] = useState<string>("");

  // Stress & Benchmark
  const [stressBusy, setStressBusy] = useState<boolean>(false);
  const [stressResults, setStressResults] = useState<StressTestResultRow[]>([]);
  const [benchBusy, setBenchBusy] = useState<boolean>(false);
  const [benchStats, setBenchStats] = useState<BenchmarkResponse | null>(null);

  // Live training-progress poll handle (cleared on completion/unmount).
  const trainPollRef = useRef<number | null>(null);
  const stopTrainPolling = useCallback(() => {
    if (trainPollRef.current !== null) {
      window.clearInterval(trainPollRef.current);
      trainPollRef.current = null;
    }
  }, []);
  useEffect(() => stopTrainPolling, [stopTrainPolling]);

  const handleHotLoad = useCallback(async (): Promise<void> => {
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
  }, [selectedModelId, enableFineTune, attachScaler, fetchModels, fetchActiveModel, fetchOverview]);

  const handleVerifyModel = useCallback(async (): Promise<void> => {
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
  }, [selectedModelId]);

  const handleRollback = useCallback(async (): Promise<void> => {
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
  }, [fetchModels, fetchActiveModel, fetchOverview]);

  const handleInspectScaler = useCallback(async (): Promise<void> => {
    if (!selectedModelId) return;
    try {
      const res = await modelStudioApi.inspectScaler(selectedModelId);
      setScalerResult(res);
    } catch (err) {
      console.error("Inspect scaler failed:", err);
    }
  }, [selectedModelId]);

  const handleFetch70d = useCallback(async (): Promise<void> => {
    try {
      const d = await modelStudioApi.fetch70d();
      setComponents70(d.slots || []);
      setContractValid(d.contract_valid ?? null);
      setSchemaHash(d.schema_hash || "");
    } catch (err) {
      console.error("Failed to fetch 70D:", err);
    }
  }, []);

  const handleInference = useCallback(async (): Promise<void> => {
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
  }, [dimension, useLive, noise, threshold]);

  const handleStressTest = useCallback(async (): Promise<void> => {
    setStressBusy(true);
    try {
      const d = await modelStudioApi.stressTest(dimension);
      setStressResults(d.results || []);
    } catch (err) {
      console.error("Stress test failed:", err);
    } finally {
      setStressBusy(false);
    }
  }, [dimension]);

  const handleBenchmark = useCallback(async (): Promise<void> => {
    setBenchBusy(true);
    try {
      setBenchStats(await modelStudioApi.benchmark(dimension, 100));
    } catch (err) {
      console.error("Benchmark failed:", err);
    } finally {
      setBenchBusy(false);
    }
  }, [dimension]);

  const handleStartTrain = useCallback(async (): Promise<void> => {
    setTrainError("");
    setTrainStatus("");
    setTrainProgress(null);
    setTrainBusy(true);
    // Stream epoch progress from the existing GET /train/progress endpoint
    // while the blocking POST /train is in flight (presentation only).
    stopTrainPolling();
    trainPollRef.current = window.setInterval(() => {
      modelStudioApi
        .trainProgress()
        .then((r) => setTrainProgress(r.progress))
        .catch(() => {
          /* progress endpoint unavailable — final state still lands on POST resolve */
        });
    }, TRAIN_POLL_MS);
    try {
      const d = await modelStudioApi.train({
        dataset_path: selectedDataset,
        dimension,
        epochs,
        batch_size: 256,
        learning_rate: learningRate,
      });
      setTrainProgress(d.state);
      setTrainStatus(
        `Done: ${d.epochs_completed} epochs | loss ${d.final_loss} | val ${d.final_val_loss} → ${d.checkpoint_path}`,
      );
    } catch (err) {
      const detail = err instanceof Error ? err.message : String(err);
      setTrainError(detail);
      setTrainStatus("");
      setTrainProgress(null);
    } finally {
      stopTrainPolling();
      setTrainBusy(false);
    }
  }, [selectedDataset, dimension, epochs, learningRate, stopTrainPolling]);

  const handleDownloadDataset = useCallback(async (): Promise<void> => {
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
  }, [dlSymbol, dlTimeframe, dlBars, dlSource, fetchDatasets]);

  const handleInspectFeatures = useCallback(async (): Promise<void> => {
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
  }, [selectedDataset, dimension]);

  const handleGeneratePositionDataset = useCallback(async (): Promise<void> => {
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
  }, [posSource, dimension, posMaxHolding, posTargetAtr, posFriction, fetchDatasets]);

  /* Stable adapters for value-coercing panel inputs (setState is stable, the
   * `n || fallback` guard is not — one useCallback each, no per-render arrow). */
  const handleNoiseChange = useCallback((n: number) => setNoise(n || 0), []);
  const handleThresholdChange = useCallback((t: number) => setThreshold(t || 0.35), []);
  const handleDlSymbolChange = useCallback((v: string) => setDlSymbol(v.toUpperCase()), []);
  const handleDlBarsChange = useCallback((n: number) => setDlBars(n || 10000), []);
  const handleEpochsChange = useCallback((n: number) => setEpochs(n || 3), []);
  const handleLearningRateChange = useCallback((n: number) => setLearningRate(n || 0.0005), []);
  const handlePosMaxHoldingChange = useCallback((n: number) => setPosMaxHolding(n || 30), []);
  const handlePosTargetAtrChange = useCallback((n: number) => setPosTargetAtr(n || 2.0), []);
  const handlePosFrictionChange = useCallback((n: number) => setPosFriction(n || 0.25), []);

  return (
    <div className="ms-container">
      <ModelStudioHeader
        dimension={dimension}
        onDimensionChange={setDimension}
        overview={overview}
        activeModel={activeModel}
        onRefresh={refreshAll}
        loading={isLoading}
        loadError={loadError}
        loadErrorSource={loadErrorSource}
        onRetry={refetchAll}
      />

      <ModelInferencePanel
        dimension={dimension}
        useLive={useLive}
        onToggleUseLive={setUseLive}
        noise={noise}
        onNoiseChange={handleNoiseChange}
        threshold={threshold}
        onThresholdChange={handleThresholdChange}
        loading={loading}
        onRunInference={handleInference}
        predictData={predictData}
        components70={components70}
        contractValid={contractValid}
        schemaHash={schemaHash}
        onFetch70d={handleFetch70d}
      />

      <ModelRegistryPanel
        models={models}
        catalogLoading={isLoading}
        activeModel={activeModel}
        selectedModelId={selectedModelId}
        onSelectModelId={setSelectedModelId}
        enableFineTune={enableFineTune}
        onToggleFineTune={setEnableFineTune}
        attachScaler={attachScaler}
        onToggleAttachScaler={setAttachScaler}
        hotLoadBusy={hotLoadBusy}
        onHotLoad={handleHotLoad}
        hotLoadResult={hotLoadResult}
        hotLoadError={hotLoadError}
        verifyBusy={verifyBusy}
        onVerify={handleVerifyModel}
        verifyResult={verifyResult}
        rollbackBusy={rollbackBusy}
        onRollback={handleRollback}
        scalerResult={scalerResult}
        onInspectScaler={handleInspectScaler}
      />

      <DatasetPipelinePanel
        dimension={dimension}
        datasets={datasets}
        catalogLoading={isLoading}
        selectedDataset={selectedDataset}
        onSelectDataset={setSelectedDataset}
        dlSymbol={dlSymbol}
        onDlSymbolChange={handleDlSymbolChange}
        dlTimeframe={dlTimeframe}
        onDlTimeframeChange={setDlTimeframe}
        dlBars={dlBars}
        onDlBarsChange={handleDlBarsChange}
        dlSource={dlSource}
        onDlSourceChange={setDlSource}
        dlBusy={dlBusy}
        onDownload={handleDownloadDataset}
        dlResult={dlResult}
        dlError={dlError}
        inspectBusy={inspectBusy}
        onInspect={handleInspectFeatures}
        inspectResult={inspectResult}
        inspectError={inspectError}
        epochs={epochs}
        onEpochsChange={handleEpochsChange}
        learningRate={learningRate}
        onLearningRateChange={handleLearningRateChange}
        trainBusy={trainBusy}
        trainProgress={trainProgress}
        trainStatus={trainStatus}
        trainError={trainError}
        onTrain={handleStartTrain}
      />

      <PositionDatasetPanel
        dimension={dimension}
        datasets={datasets}
        catalogLoading={isLoading}
        posSource={posSource}
        onPosSourceChange={setPosSource}
        posMaxHolding={posMaxHolding}
        onPosMaxHoldingChange={handlePosMaxHoldingChange}
        posTargetAtr={posTargetAtr}
        onPosTargetAtrChange={handlePosTargetAtrChange}
        posFriction={posFriction}
        onPosFrictionChange={handlePosFrictionChange}
        posBusy={posBusy}
        onGenerate={handleGeneratePositionDataset}
        posResult={posResult}
        posError={posError}
      />

      <ModelStressBenchPanel
        dimension={dimension}
        stressBusy={stressBusy}
        stressResults={stressResults}
        onRunStress={handleStressTest}
        benchBusy={benchBusy}
        benchStats={benchStats}
        onRunBenchmark={handleBenchmark}
      />
    </div>
  );
}
