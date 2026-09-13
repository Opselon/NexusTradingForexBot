/**
 * Lane-4 typed call surface for backend routes that have no `src/api/*Api`
 * module yet (chart history, replay pipeline, liquidity/mSLIE, operator order
 * flow, calibration, v1 shadow, regime).
 *
 * Layering: this is presentation-side glue that lives with the pages because
 * `src/api/` is another lane's ownership this wave. It follows the same rules
 * as every api module — zero UI, zero fetch literals, everything through
 * `@/api/client` (getV1 / getLegacy / send), which owns auth, the BUG-267
 * cookie heal, request ids and the ApiError envelope. When these contexts get
 * their own api modules, this file collapses into them.
 */

import { getLegacy, getV1, send } from "@/api/client";
import type {
  CalibrationResponse,
  ChartHistoryResponse,
  LiquidityState,
  MslieStatus,
  OperatorOrdersResponse,
  RegimePayload,
  ReplayControlResponse,
  ReplayDecision,
  ReplayReport,
  ReplayRuntimeState,
  ReplaySessionResponse,
  ReplayState,
  ReplayToggleResponse,
  Shadow70Block,
  ShadowRunRow,
  ShadowStatus,
} from "./contracts";
import type { V1Page } from "@/types/domain";

export const chartApi = {
  /** Authoritative broker history + SMC overlays (explicit provenance). */
  history: (count = 900, signal?: AbortSignal): Promise<ChartHistoryResponse> =>
    getLegacy<ChartHistoryResponse>(`/api/chart/history?count=${count}`, signal),
};

export const replayApi = {
  /** Replay-on-chart session creation (REPLAY_API v1 contract). */
  createSession: (body: {
    dataset_id: string;
    dataset_fingerprint: string;
    symbol?: string;
    replay_mode?: "BAR_REPLAY" | "TICK_REPLAY";
    timeframe?: string;
    start_time: string;
    end_time: string;
    git_commit?: string;
    confidence_threshold?: number;
    regime_enabled?: boolean;
    checkpoint_every_bars?: number;
  }): Promise<ReplaySessionResponse> => send<ReplaySessionResponse>("/api/replay/session", body),

  control: (body: {
    action: "step_tick" | "step_bar" | "play" | "pause" | "reset" | "seek" | "checkpoint";
    replay_id?: string;
    n?: number;
    seek_time?: string;
  }): Promise<ReplayControlResponse> => send<ReplayControlResponse>("/api/replay/control", body),

  state: (replayId?: string | null, signal?: AbortSignal): Promise<ReplayState> =>
    getLegacy<ReplayState>(
      `/api/replay/state${replayId ? `?replay_id=${encodeURIComponent(replayId)}` : ""}`,
      signal,
    ),

  decision: (seq: number, replayId?: string | null, signal?: AbortSignal): Promise<{ ok: boolean; decision: ReplayDecision }> =>
    getLegacy<{ ok: boolean; decision: ReplayDecision }>(
      `/api/replay/decision?seq=${seq}${replayId ? `&replay_id=${encodeURIComponent(replayId)}` : ""}`,
      signal,
    ),

  report: (replayId?: string | null, signal?: AbortSignal): Promise<{ ok: boolean; report: ReplayReport }> =>
    getLegacy<{ ok: boolean; report: ReplayReport }>(
      `/api/replay/report${replayId ? `?replay_id=${encodeURIComponent(replayId)}` : ""}`,
      signal,
    ),

  /** Historical-replay flag (independent of execution mode since SEC-AUDIT-9). */
  toggle: (active: boolean, speed = 1): Promise<ReplayToggleResponse> =>
    send<ReplayToggleResponse>("/api/replay/toggle", { active, speed }),
};

export const runtimeApi = {
  mode: (signal?: AbortSignal): Promise<ReplayRuntimeState> =>
    getV1<ReplayRuntimeState>("/api/v1/runtime/mode", signal),
};

export const marketApi2 = {
  regime: (signal?: AbortSignal): Promise<RegimePayload> =>
    getV1<RegimePayload>("/api/v1/market/regime", signal),
};

/** READ-ONLY liquidity/mSLIE views (the liquidity TAB belongs to lane 5). */
export const contextApi = {
  liquidityState: (signal?: AbortSignal): Promise<LiquidityState> =>
    getLegacy<LiquidityState>("/api/liquidity/state", signal),

  msilieStatus: (signal?: AbortSignal): Promise<MslieStatus> =>
    getLegacy<MslieStatus>("/api/mslie/status", signal),
};

/** Control-center-grade operator reads used by Trading / ML pages. */
export const operatorApi = {
  orders: (limit = 50, signal?: AbortSignal): Promise<OperatorOrdersResponse> =>
    getLegacy<OperatorOrdersResponse>(`/api/operator/orders?limit=${limit}`, signal),

  calibration: (signal?: AbortSignal): Promise<CalibrationResponse> =>
    getLegacy<CalibrationResponse>("/api/operator/calibration", signal),
};

export const shadowApi = {
  status: (signal?: AbortSignal): Promise<ShadowStatus> =>
    getV1<ShadowStatus>("/api/v1/shadow/status", signal),

  runs: (page = 1, pageSize = 25, signal?: AbortSignal): Promise<V1Page<ShadowRunRow>> =>
    getV1<V1Page<ShadowRunRow>>(`/api/v1/shadow/runs?page=${page}&page_size=${pageSize}`, signal),

  shadow70: (signal?: AbortSignal): Promise<Shadow70Block> =>
    getV1<Shadow70Block>("/api/v1/shadow/70d", signal),
};
