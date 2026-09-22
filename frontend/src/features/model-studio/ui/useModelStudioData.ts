/**
 * useModelStudioData — Model Studio data layer.
 *
 * Owns the four reads the screen boots with (overview, dataset catalog,
 * model catalog, active champion), the fine-tune flag seeded from the
 * active model, and the selection pointers that seed themselves from the
 * dataset list (selectedDataset / posSource). Fetch bodies are
 * byte-preserved from the pre-split page; presentation state and action
 * handlers stay in ModelStudioPage.
 */

import { useEffect, useState } from "react";

import { modelStudioApi } from "../api";
import type {
  ActiveModelResponse,
  ModelRecordDto,
  ModelStudioDatasetItem,
  ModelStudioOverviewDto,
} from "../model";

export function useModelStudioData() {
  const [overview, setOverview] = useState<ModelStudioOverviewDto | null>(null);
  const [datasets, setDatasets] = useState<ModelStudioDatasetItem[]>([]);
  const [models, setModels] = useState<ModelRecordDto[]>([]);
  const [activeModel, setActiveModel] = useState<ActiveModelResponse["active_model"]>(null);
  const [selectedModelId, setSelectedModelId] = useState<string>("");
  const [selectedDataset, setSelectedDataset] = useState<string>("");
  const [posSource, setPosSource] = useState<string>("");
  const [enableFineTune, setEnableFineTune] = useState<boolean>(false);

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

  /** Header refresh action — same four reads as the initial load. */
  const refreshAll = () => {
    fetchOverview();
    fetchDatasets();
    fetchModels();
    fetchActiveModel();
  };

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
  };
}
