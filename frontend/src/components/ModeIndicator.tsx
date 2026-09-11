/**
 * ModeIndicator — LIVE / PAPER / SHADOW mode display.
 *
 * Safety contract:
 *  - The mode shown is ALWAYS the backend's `runtime_mode` / `execution_mode`.
 *  - A backend-reported `mode_source_mismatch` (BUG-232) renders as a hard
 *    error state — a LIVE badge is never trusted without a matching
 *    MT5_LIVE data source.
 *  - This component NEVER infers or defaults the mode client-side.
 */

import type { EngineSnapshot } from "@/types/domain";

interface Props {
  snapshot: EngineSnapshot | undefined;
}

export function ModeIndicator({ snapshot }: Props) {
  if (!snapshot) {
    return <span className="mode-badge unknown">MODE —</span>;
  }
  const mode = (snapshot.runtime_mode ?? snapshot.execution_mode ?? "UNKNOWN").toUpperCase();

  if (snapshot.mode_source_mismatch) {
    return (
      <span
        className="mode-badge live"
        title={`MODE-SOURCE MISMATCH: runtime_mode=${mode} but data_source=${snapshot.data_source ?? "UNKNOWN"} (BUG-232 guard). Do not trust this as real broker state.`}
      >
        ⚠ {mode} (SOURCE MISMATCH)
      </span>
    );
  }

  const cls = mode.startsWith("LIVE") ? "live" : mode === "PAPER" ? "paper" : mode === "SHADOW" ? "shadow" : "unknown";
  return (
    <span className={`mode-badge ${cls}`} title={`execution_mode=${snapshot.execution_mode ?? "—"} · data_source=${snapshot.data_source ?? "—"} · adapter=${snapshot.adapter_class ?? "—"}`}>
      {mode === "UNKNOWN" ? "MODE —" : mode}
    </span>
  );
}
