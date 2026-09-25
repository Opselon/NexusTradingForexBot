/**
 * replayApi — paper REPLAY mode (legacy raw JSON).
 *
 * Routes: replay_routes.py GET /api/replay/{state,report,decision},
 * POST /api/replay/{session,control}; server.py POST /api/replay/toggle.
 * Backend wiring is BUG-266 (paper adapter); the UI mirrors state only —
 * the engine remains authoritative over what replay does.
 */

import { getLegacy, send } from "@/core/transport";
import { toQuery } from "@/core/config";
import type { ReplayReport, ReplayState } from "@/types/features";
import type { LegacyMutationResult } from "@/types/api";

export const replayApi = {
  state: (signal?: AbortSignal): Promise<ReplayState> =>
    getLegacy<ReplayState>("/api/replay/state", signal),

  report: (params: { session_id?: string } = {}, signal?: AbortSignal): Promise<ReplayReport> =>
    getLegacy<ReplayReport>(`/api/replay/report${toQuery({ ...params })}`, signal),

  decision: (params: { t?: string | number; ticket?: string | number } = {}, signal?: AbortSignal): Promise<Record<string, unknown>> =>
    getLegacy<Record<string, unknown>>(`/api/replay/decision${toQuery({ ...params })}`, signal),

  /** Start/attach a replay session (BUG-266 paper adapter path). */
  openSession: (payload: Record<string, unknown>): Promise<LegacyMutationResult> =>
    send<LegacyMutationResult>("/api/replay/session", payload),

  /** Play/pause/step/speed control verbs (backend-validated). */
  control: (payload: Record<string, unknown>): Promise<LegacyMutationResult> =>
    send<LegacyMutationResult>("/api/replay/control", payload),

  /** server.py quick toggle (legacy dashboard parity). */
  toggle: (payload: Record<string, unknown> = {}): Promise<LegacyMutationResult> =>
    send<LegacyMutationResult>("/api/replay/toggle", payload),
};
