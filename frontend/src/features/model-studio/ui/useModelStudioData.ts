/**
 * useModelStudioData — Model Studio data layer (TanStack Query bindings).
 *
 * PURPOSE: own the four reads the screen boots with (overview, dataset
 *   catalog, model catalog, active champion), the fine-tune flag seeded from
 *   the active model, and the selection pointers that seed themselves from
 *   the dataset list. Presentation state and action handlers stay in
 *   ModelStudioPage.
 * OWNER: lane 3 (features/model-studio/**).
 * CONSUMES: ../api (modelStudioApi GETs — all accept AbortSignal).
 * PROVIDES: useModelStudioData() — same property surface as the pre-query
 *   version (overview/datasets/models/activeModel + selection state +
 *   fetchX()/refreshAll), plus boot-state flags: isLoading, loadError,
 *   loadErrorSource, refetchAll.
 * INVARANTS: identical request set and payload handling to the pre-query
 *   hook; selection seeding keeps the "first value wins" (prev || …)
 *   semantics the fetch callbacks had; the backend stays authoritative.
 * EXTEND: new reads go through modelStudioKeys + their own useQuery.
 *
 * Fetch logic: no refetchInterval (the studio is operator-refreshed),
 * AbortSignal passthrough on every GET, retry:1, staleTime 15s so the boot
 * reads and post-action refetches dedupe instead of stacking concurrent
 * identical requests, and no fetch-in-render (queries start in hooks only).
 */

import { useCallback, useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import { modelStudioApi } from "../api";
import type {
  ActiveModelResponse,
  ModelRecordDto,
  ModelStudioDatasetItem,
  ModelStudioOverviewDto,
} from "../model";

/** Namespaced query keys — the seam every refetch/invalidation targets. */
export const modelStudioKeys = {
  all: ["model-studio"] as const,
  overview: () => ["model-studio", "overview"] as const,
  datasets: () => ["model-studio", "datasets"] as const,
  models: () => ["model-studio", "models"] as const,
  activeModel: () => ["model-studio", "active-model"] as const,
};

/** Shared read policy: bounded retries, short staleness, no polling. */
const READ = { retry: 1, staleTime: 15_000 } as const;

/* Stable empty identities — `?? []` inline would re-render memoized panels
 * on every hook call while a catalog is still loading. */
const EMPTY_DATASETS: ModelStudioDatasetItem[] = [];
const EMPTY_MODELS: ModelRecordDto[] = [];

export function useModelStudioData() {
  const queryClient = useQueryClient();

  const overviewQ = useQuery({
    queryKey: modelStudioKeys.overview(),
    queryFn: ({ signal }) => modelStudioApi.overview(signal),
    ...READ,
  });
  const datasetsQ = useQuery({
    queryKey: modelStudioKeys.datasets(),
    queryFn: ({ signal }) => modelStudioApi.datasets(signal),
    ...READ,
  });
  const modelsQ = useQuery({
    queryKey: modelStudioKeys.models(),
    queryFn: ({ signal }) => modelStudioApi.listModels(signal),
    ...READ,
  });
  const activeQ = useQuery({
    queryKey: modelStudioKeys.activeModel(),
    queryFn: ({ signal }) => modelStudioApi.activeModel(signal),
    ...READ,
  });

  const [selectedModelId, setSelectedModelId] = useState<string>("");
  const [selectedDataset, setSelectedDataset] = useState<string>("");
  const [posSource, setPosSource] = useState<string>("");
  const [enableFineTune, setEnableFineTune] = useState<boolean>(false);

  const overview: ModelStudioOverviewDto | null = overviewQ.data ?? null;
  const datasets: ModelStudioDatasetItem[] = datasetsQ.data?.datasets ?? EMPTY_DATASETS;
  const models: ModelRecordDto[] = modelsQ.data?.models ?? EMPTY_MODELS;
  const activeModel: ActiveModelResponse["active_model"] = activeQ.data?.active_model ?? null;

  /* Seed selection pointers from resolved data (first value wins — exactly
   * the guard the old fetch callbacks used, now reactive to refetches). */
  const modelsPayload = modelsQ.data;
  useEffect(() => {
    const list = modelsPayload?.models ?? [];
    if (list.length > 0 && list[0]) {
      const champ = list.find((m) => m.is_active || m.id === modelsPayload?.active_champion_id);
      const fallbackId = list[0].id;
      setSelectedModelId((prev) => prev || (champ ? champ.id : fallbackId));
    }
  }, [modelsPayload]);

  const datasetsPayload = datasetsQ.data;
  useEffect(() => {
    const first = (datasetsPayload?.datasets ?? [])[0];
    if (first) {
      setSelectedDataset((prev) => prev || first.path);
      setPosSource((prev) => prev || first.path);
    }
  }, [datasetsPayload]);

  /* Fine-tune flag mirrors the backend's active model on every fresh read
   * (same as before: a hot-load/refresh re-read overwrites the toggle). */
  const activePayload = activeQ.data;
  useEffect(() => {
    const am = activePayload?.active_model;
    if (am) setEnableFineTune(Boolean(am.fine_tune_enabled));
  }, [activePayload]);

  /* Manual refetch wrappers keep the page's `await fetchX()` call sites and
   * behavior (post-action refresh), but run through the cache: identical
   * concurrent calls dedupe instead of stacking. QueryClient identity is
   * stable, so these callbacks are stable props for memoized panels. */
  const fetchOverview = useCallback(
    () => queryClient.refetchQueries({ queryKey: modelStudioKeys.overview(), exact: true }),
    [queryClient],
  );
  const fetchDatasets = useCallback(
    () => queryClient.refetchQueries({ queryKey: modelStudioKeys.datasets(), exact: true }),
    [queryClient],
  );
  const fetchModels = useCallback(
    () => queryClient.refetchQueries({ queryKey: modelStudioKeys.models(), exact: true }),
    [queryClient],
  );
  const fetchActiveModel = useCallback(
    () => queryClient.refetchQueries({ queryKey: modelStudioKeys.activeModel(), exact: true }),
    [queryClient],
  );

  /** Header refresh action — same four reads as the initial load. */
  const refreshAll = useCallback(() => {
    void fetchOverview();
    void fetchDatasets();
    void fetchModels();
    void fetchActiveModel();
  }, [fetchOverview, fetchDatasets, fetchModels, fetchActiveModel]);

  const isLoading = overviewQ.isPending || datasetsQ.isPending || modelsQ.isPending || activeQ.isPending;
  /* First failing boot read + its endpoint name (honest error surface — the
   * old layer only console.error'd, leaving the UI silently blank). */
  const bootReads = [
    { source: "GET /api/model-studio/overview", error: overviewQ.error },
    { source: "GET /api/model-studio/datasets", error: datasetsQ.error },
    { source: "GET /api/model-studio/models", error: modelsQ.error },
    { source: "GET /api/model-studio/models/active", error: activeQ.error },
  ];
  const failedRead = bootReads.find((r) => r.error !== null && r.error !== undefined);
  const loadError: unknown = failedRead ? failedRead.error : null;
  const loadErrorSource: string | null = failedRead ? failedRead.source : null;
  /** Retry every failing boot read (ErrorState action). */
  const refetchAll = refreshAll;

  return {
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
  };
}
