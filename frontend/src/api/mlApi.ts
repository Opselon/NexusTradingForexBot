/**
 * mlApi — model / 70D state.
 *
 * The UI must never claim the 70D model is healthy merely because an artifact
 * exists. All health claims come from:
 *  GET /api/models/integrity      — semantic model state (backend-decided)
 *  GET /api/models/shadow70/summary — 70D shadow runtime + worker + store
 *  GET /api/v1/model/status       — serving bundle + inference enablement
 *  GET /api/v1/model/identity     — artifact identity / schema fingerprint
 *  GET /api/v1/features/status    — warmup + last vector + missing features
 */

import { getV1, getLegacy } from "./client";
import type { V1ModelStatus, V1ModelIdentity, V1FeaturesStatus, Shadow70State, ModelIntegrity } from "@/types/domain";

export const mlApi = {
  integrity: (signal?: AbortSignal): Promise<ModelIntegrity> =>
    getLegacy<ModelIntegrity>("/api/models/integrity", signal),

  shadow70: (signal?: AbortSignal): Promise<Shadow70State> =>
    getLegacy<Shadow70State>("/api/models/shadow70/summary", signal),

  modelStatus: (signal?: AbortSignal): Promise<V1ModelStatus> => getV1<V1ModelStatus>("/api/v1/model/status", signal),

  modelIdentity: (signal?: AbortSignal): Promise<V1ModelIdentity> =>
    getV1<V1ModelIdentity>("/api/v1/model/identity", signal),

  featuresStatus: (signal?: AbortSignal): Promise<V1FeaturesStatus> =>
    getV1<V1FeaturesStatus>("/api/v1/features/status", signal),
};
