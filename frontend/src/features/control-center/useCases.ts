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
import { useI18n } from "@/stores/i18nStore";

function toMutationToggle(res: EngineToggleDto, status = 200): LegacyMutationResult {
  return {
    ok: true,
    success: res.success !== false,
    message: (() => {
      const t = useI18n.getState().t;
      const state = res.engine_running
        ? t("control-center.engine.running", "RUNNING")
        : t("control-center.engine.stopped", "STOPPED");
      return t("control-center.engine.toggle_message", "engine {state}{detail}", {
        state,
        detail: res.message ? ` — ${res.message}` : "",
      });
    })(),
    status,
  };
}

function toMutationMode(res: EngineModeDto, status = 200): LegacyMutationResult {
  return {
    ok: true,
    success: res.success !== false,
    message: (() => {
      const t = useI18n.getState().t;
      return t("control-center.engine.mode_message", "mode {mode} · runtime {runtime} · persisted {persisted}", {
        mode: res.mode ?? "?",
        runtime: res.runtime_mode ?? "—",
        persisted: res.persisted ? t("control-center.engine.yes", "yes") : t("control-center.engine.no", "no"),
      });
    })(),
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

  /** Decisions with unparseable payload are KEPT and flagged (never dropped).
   *  Deterministic key: a Math.random() fallback remounted those rows on every
   *  render (the tape re-renders each second) — identity now comes from the
   *  ledger's own fields only, never from a random token. */
  decisionKey: (r: OperatorDecisionRow): string =>
    String(r.id ?? r.request_id ?? `${r.generated_at ?? ""}|${r.action ?? ""}|${r.decision_stage ?? ""}|${r.reason_code ?? ""}`),
};
