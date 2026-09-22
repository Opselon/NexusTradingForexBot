/**
 * Shadow 70D deep telemetry — legacy "70D SHADOW MODEL PANEL" parity.
 *
 * The v1 route `/api/v1/shadow/70d` (used by the Shadow tab) carries the
 * store summary; the legacy tab ALSO renders two deeper read paths that
 * the v1 envelope does not surface. This module wires them through the
 * legacy transport so the React Shadow panel can show everything the
 * legacy panel showed (TASK-05-70D-SHADOW).
 *
 *  - GET /api/models/shadow70/health        — feature health + drift summary
 *                                             + persisted alerts/health
 *  - GET /api/models/shadow70/disagreements — recent Champion-vs-Shadow
 *                                             disagreement rows (spec 30)
 *
 * Server: src/nexus_scalp/web/model_governance_routes.py (router, no prefix).
 * Both answer `{available: false}` when the 70D shadow runtime is not
 * attached — that is rendered as unavailable, never as healthy zeroes.
 */

import { getLegacy, toQuery } from "@/core";

/** Per-feature health row from Shadow70FeatureHealthMonitor. */
export interface ShadowFeatureHealth {
  name?: string;
  mean?: number;
  std?: number;
  missing_rate?: number;
  zero_rate?: number;
  samples?: number;
  [k: string]: unknown;
}

export interface ShadowDriftSummary {
  available?: boolean;
  severity?: string;
  [k: string]: unknown;
}

export interface PersistedDriftAlert {
  feature?: string;
  kind?: string;
  alert_type?: string;
  value?: number;
  score?: number;
  created_at?: string;
  detected_at?: string;
  [k: string]: unknown;
}

export interface Shadow70HealthResponse {
  available: boolean;
  feature_health?: ShadowFeatureHealth[];
  drift?: ShadowDriftSummary;
  persisted_alerts?: PersistedDriftAlert[];
  persisted_health?: ShadowFeatureHealth[];
  error?: unknown;
}

/** One Champion-vs-Shadow disagreement observation (spec 30). */
export interface ShadowDisagreementRow {
  timestamp?: string;
  champion_action?: string;
  shadow_action?: string;
  disagreement?: string;
  outcome?: string;
  [k: string]: unknown;
}

export interface Shadow70DisagreementsResponse {
  available: boolean;
  rows?: ShadowDisagreementRow[];
  error?: unknown;
}

export const shadow70Api = {
  /** GET /api/models/shadow70/health — feature stats + drift severity. */
  health: (signal?: AbortSignal): Promise<Shadow70HealthResponse> =>
    getLegacy<Shadow70HealthResponse>("/api/models/shadow70/health", signal),

  /** GET /api/models/shadow70/disagreements?limit= — recent disagreements. */
  disagreements: (limit = 30, signal?: AbortSignal): Promise<Shadow70DisagreementsResponse> =>
    getLegacy<Shadow70DisagreementsResponse>(
      `/api/models/shadow70/disagreements${toQuery({ limit })}`,
      signal,
    ),
};
