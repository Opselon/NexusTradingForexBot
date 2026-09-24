/**
 * TraceHeader — runtime identity strip (§01/§38, RUNTIME TOPOLOGY OBSERVABILITY).
 *
 * ENGINE · MODE · MT5 · SYMBOL · TF · MODEL · REGIME — every value is a
 * verbatim field from the shell's EngineSnapshot or the observer status.
 * Missing data renders `UNKNOWN`/`—`, never a synthesized value.
 */

import type { EngineSnapshot } from "@/types/domain";

function val(v: unknown): string {
  if (v === null || v === undefined || v === "") return "UNKNOWN";
  return String(v);
}

function RailItem({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  const unknown = value === "UNKNOWN" || value === "—";
  return (
    <div className={`dt-rail-item ${unknown ? "unknown" : ""}`} title={`${label}: ${value}`}>
      <span className="dt-rail-label">{label}</span>
      <span className={`dt-rail-value ${mono ? "mono" : ""}`}>{value}</span>
    </div>
  );
}

export function TraceHeader({
  snapshot,
  schemaVersion,
  observerStatus,
}: {
  snapshot?: EngineSnapshot;
  schemaVersion?: number;
  observerStatus?: string;
}) {
  const engine = snapshot;
  const engineTone = engine?.engine_running ? "run" : "stop";

  return (
    <header className="dt-header" role="banner">
      <div className="dt-header-left">
        <span className="dt-eyebrow">RUNTIME TOPOLOGY OBSERVABILITY</span>
        <h1 className="dt-title">Decision Trace</h1>
      </div>

      <div className="dt-header-rail" role="list" aria-label="Runtime identity">
        <div className={`dt-rail-item engine ${engineTone}`} title={engine?.engine_running ? "engine running" : "engine stopped"}>
          <span className="dt-rail-label">ENGINE</span>
          <span className="dt-rail-value">{engine ? (engine.engine_running ? "RUNNING" : "STOPPED") : "UNKNOWN"}</span>
        </div>
        <RailItem label="MODE" value={val(engine?.execution_mode)} mono />
        <RailItem label="MT5" value={val(engine?.adapter_class)} mono />
        <RailItem label="SYMBOL" value={val(engine?.symbol)} mono />
        <RailItem label="TF" value={val(engine?.data_source)} mono />
        <RailItem label="MODEL" value={val(engine?.model?.model_id)} mono />
        <RailItem label="REGIME" value={val(engine?.regime)} mono />
        <RailItem label="OBSERVER" value={val(observerStatus)} mono />
        <RailItem label="SCHEMA" value={schemaVersion ? `v${schemaVersion}` : "UNKNOWN"} mono />
      </div>

      <div className="dt-header-right">
        <span className="dt-header-note">
          Observability window into the live engine — the runtime is the only source of truth.
        </span>
      </div>
    </header>
  );
}
