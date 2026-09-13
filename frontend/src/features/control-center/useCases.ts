/**
 * Control Center: application use cases.
 *
 * Kill-switch style actions (stop engine / mode switch) are confirm-guarded
 * in the UI and normalized from the raw legacy body into the shared
 * LegacyMutationResult contract — the backend's {success:false} is a refusal,
 * never a local state change. The next authoritative snapshot carries truth.
 */

import type { LegacyMutationResult } from "@/types/api";
import { controlCenterApi, type EngineModeDto, type EngineToggleDto } from "./api";
import type { OperatorDecisionRow } from "./model";

function toMutationToggle(res: EngineToggleDto, status = 200): LegacyMutationResult {
  return {
    ok: true,
    success: res.success !== false,
    message: `engine ${res.engine_running ? "RUNNING" : "STOPPED"}${res.message ? ` — ${res.message}` : ""}`,
    status,
  };
}

function toMutationMode(res: EngineModeDto, status = 200): LegacyMutationResult {
  return {
    ok: true,
    success: res.success !== false,
    message: `mode ${res.mode ?? "?"} · runtime ${res.runtime_mode ?? "—"} · persisted ${res.persisted ? "yes" : "no"}`,
    status,
  };
}

export const controlCenterQueries = {
  summary: (signal?: AbortSignal) => controlCenterApi.summary(signal),
  decisions: (f: { hours?: number; action?: string; gate?: string; search?: string; limit?: number }, signal?: AbortSignal) =>
    controlCenterApi.decisions(f, signal),
  decisionDetail: (id: number, signal?: AbortSignal) => controlCenterApi.decisionDetail(id, signal),
  funnel: (hours: number | undefined, signal?: AbortSignal) => controlCenterApi.funnel(hours, signal),
  noTrade: (hours: number | undefined, limit: number, signal?: AbortSignal) => controlCenterApi.noTrade(hours, limit, signal),
  orders: (signal?: AbortSignal) => controlCenterApi.orders(50, signal),
  calibration: (signal?: AbortSignal) => controlCenterApi.calibration(signal),
};

export const controlCenterUseCases = {
  toggleEngine: async (active: boolean): Promise<LegacyMutationResult> => toMutationToggle(await controlCenterApi.toggleEngine(active)),
  setMode: async (mode: string): Promise<LegacyMutationResult> => toMutationMode(await controlCenterApi.setMode(mode)),

  /** Decisions with unparseable payload are KEPT and flagged (never dropped). */
  decisionKey: (r: OperatorDecisionRow): string => String(r.id ?? r.request_id ?? Math.random()),
};
