/**
 * Command Center: typed transport surface over @/api/client.
 *
 * Routes verified in src/nexus_scalp/web/command_center_integration.py:
 *   GET /api/command-center/overview
 *   GET /api/command-center/fleet?lifecycle=&execution_filter=&limit=
 *   GET /api/command-center/spatial?max_columns=&limit=
 *   GET /api/command-center/inspector/{id}
 *   GET /api/command-center/execution-safety/{id}
 *   GET /api/command-center/timeline/{id}?limit=
 *   GET /api/command-center/timemachine/bounds?limit=
 *   GET /api/command-center/timemachine/frame?at=<ISO>&limit=
 * All legacy raw-JSON envelopes (serialize_enums; {available:false, reason}
 * when the research engine is detached).
 */

import { getLegacy } from "@/api/client";
import type {
  CcExecutionSafetyDto,
  CcFleetDto,
  CcInspectorDto,
  CcOverviewDto,
  CcSpatialDto,
  CcTimelineDto,
  TimeMachineBoundsDto,
  TimeMachineFrameDto,
} from "./model";

const qs = (params: Record<string, string | number | boolean | undefined>): string => {
  const usp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== "") usp.set(k, String(v));
  const s = usp.toString();
  return s ? `?${s}` : "";
};

export const commandCenterApi = {
  overview: (signal?: AbortSignal): Promise<CcOverviewDto> => getLegacy<CcOverviewDto>("/api/command-center/overview", signal),

  fleet: (lifecycle: string | undefined, executionFilter: string | undefined, signal?: AbortSignal): Promise<CcFleetDto> =>
    getLegacy<CcFleetDto>(`/api/command-center/fleet${qs({ lifecycle, execution_filter: executionFilter, limit: 2000 })}`, signal),

  /** Backend-computed 2.5D layout (research/spatial_layout.SpatialLayout). */
  spatial: (signal?: AbortSignal): Promise<CcSpatialDto> =>
    getLegacy<CcSpatialDto>(`/api/command-center/spatial${qs({ max_columns: 6, limit: 2000 })}`, signal),

  inspector: (strategyId: string, signal?: AbortSignal): Promise<CcInspectorDto> =>
    getLegacy<CcInspectorDto>(`/api/command-center/inspector/${encodeURIComponent(strategyId)}`, signal),

  executionSafety: (strategyId: string, signal?: AbortSignal): Promise<CcExecutionSafetyDto> =>
    getLegacy<CcExecutionSafetyDto>(`/api/command-center/execution-safety/${encodeURIComponent(strategyId)}`, signal),

  timeline: (strategyId: string, signal?: AbortSignal): Promise<CcTimelineDto> =>
    getLegacy<CcTimelineDto>(`/api/command-center/timeline/${encodeURIComponent(strategyId)}${qs({ limit: 200 })}`, signal),

  tmBounds: (signal?: AbortSignal): Promise<TimeMachineBoundsDto> =>
    getLegacy<TimeMachineBoundsDto>("/api/command-center/timemachine/bounds", signal),

  tmFrame: (at: string, signal?: AbortSignal): Promise<TimeMachineFrameDto> =>
    getLegacy<TimeMachineFrameDto>(`/api/command-center/timemachine/frame${qs({ at, limit: 2000 })}`, signal),
};
