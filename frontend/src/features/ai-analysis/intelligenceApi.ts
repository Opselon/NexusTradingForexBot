/**
 * Intelligence telemetry (behavior / anomalies / evolution) — legacy parity.
 *
 * These three legacy panels lived on the Web/index.html "AI INTELLIGENCE
 * CENTER" tab and on the /intelligence page's trade-intelligence block:
 *  - GET /api/intelligence/behavior      (behavioral-pattern detections)
 *  - GET /api/intelligence/anomalies     (evidence-based anomaly events)
 *  - GET /api/intelligence/evolution     (discovered, unvalidated candidates)
 *  - POST /api/intelligence/evolution/scan
 *  - GET /api/intelligence/positions/{ticket}/timeline
 *
 * Server: src/nexus_scalp/web/intelligence_routes.py (router, no prefix).
 * Responses are raw legacy JSON `{available, ...}`; a subsystem that has
 * never recorded anything answers `available: true` with an empty list —
 * that is rendered as an explicit empty state, never zero-filled counters.
 * `available: false` means the intelligence subsystem is not attached, and
 * is rendered as unavailable (never as "0 detections").
 */

import { getLegacy, send, toQuery } from "@/core";

/** One behavioral-pattern detection row (list_behavior_detections). */
export interface BehaviorDetection {
  pattern?: string;
  behavior_type?: string;
  behavior?: string;
  symbol?: string;
  detected_at?: string;
  summary?: string;
  detail?: string;
  [k: string]: unknown;
}

export interface BehaviorResponse {
  available: boolean;
  detections?: BehaviorDetection[];
  error?: unknown;
}

/** One evidence-backed anomaly event (list_anomaly_events). */
export interface AnomalyEvent {
  anomaly_type?: string;
  category?: string;
  severity?: string;
  detected_at?: string;
  first_seen?: string;
  last_seen?: string;
  observation_count?: number;
  evidence?:
    | {
        explanation?: string;
        [k: string]: unknown;
      }
    | string
    | null;
  [k: string]: unknown;
}

export interface AnomalyResponse {
  available: boolean;
  anomalies?: AnomalyEvent[];
  error?: unknown;
}

/** One discovered-but-unvalidated strategy evolution candidate. */
export interface EvolutionCandidate {
  candidate_id?: string;
  status?: string;
  hypothesis?: string;
  [k: string]: unknown;
}

export interface EvolutionResponse {
  available: boolean;
  candidates?: EvolutionCandidate[];
  error?: unknown;
}

/** One immutable lifecycle event for a position timeline. */
export interface PositionLifecycleEvent {
  event_type?: string;
  detail?: string;
  performance?: {
    mfe?: number;
    mae?: number;
    [k: string]: unknown;
  } | null;
  [k: string]: unknown;
}

export interface PositionTimelineResponse {
  available: boolean;
  events?: PositionLifecycleEvent[];
  error?: unknown;
}

export const intelligenceTelemetryApi = {
  behavior: (limit = 8, signal?: AbortSignal): Promise<BehaviorResponse> =>
    getLegacy<BehaviorResponse>(`/api/intelligence/behavior${toQuery({ limit })}`, signal),

  anomalies: (limit = 8, signal?: AbortSignal): Promise<AnomalyResponse> =>
    getLegacy<AnomalyResponse>(`/api/intelligence/anomalies${toQuery({ limit })}`, signal),

  evolution: (limit = 8, signal?: AbortSignal): Promise<EvolutionResponse> =>
    getLegacy<EvolutionResponse>(`/api/intelligence/evolution${toQuery({ limit })}`, signal),

  /** POST /api/intelligence/evolution/scan — bounded discovery pass; the
   *  returned candidates are never live. Legacy NX.api parity. */
  scanEvolution: (): Promise<EvolutionResponse> =>
    send<EvolutionResponse>("/api/intelligence/evolution/scan", {}),

  /** GET /api/intelligence/positions/{ticket}/timeline — immutable audit join. */
  positionTimeline: (ticket: string | number, signal?: AbortSignal): Promise<PositionTimelineResponse> =>
    getLegacy<PositionTimelineResponse>(
      `/api/intelligence/positions/${encodeURIComponent(String(ticket))}/timeline`,
      signal,
    ),
};
