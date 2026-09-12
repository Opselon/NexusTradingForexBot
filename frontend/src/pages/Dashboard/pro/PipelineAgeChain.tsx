/**
 * PipelineAgeChain — the data-lineage freshness chain.
 *
 * tick → features → inference → decision as one horizontal chain. Ages come
 * from `snapshot.diagnostics` (backend-reported seconds), the per-node STATE
 * WORD comes from `snapshot.live_freshness` (the backend's own FRESH/STALE/
 * UNKNOWN verdict). When live_freshness is absent, the node shows UNKNOWN —
 * never an inferred "FRESH". Reuses the existing FreshnessMeter primitive for
 * the bar; the chain layout is inline-styled so this lane adds no CSS.
 */

import type { EngineSnapshot, FreshnessStage } from "@/types/domain";
import { FreshnessMeter } from "@/components/ConnectionIndicator";
import { formatAgeMs } from "@/lib/format";

interface Stage {
  key: "market" | "features" | "inference" | "decision";
  label: string;
  diagAgeSec: number | null | undefined;
}

/** backend age-seconds -> ms for the meter (null stays null — no default). */
function ageSecToMs(sec: number | null | undefined): number | null {
  return sec === null || sec === undefined || !Number.isFinite(sec) ? null : sec * 1000;
}

function nodeTitle(label: string, stage: FreshnessStage | undefined, ageMs: number | null): string {
  const state = (stage?.state ?? "UNKNOWN").toUpperCase();
  const age = ageMs === null ? "—" : formatAgeMs(ageMs);
  return `${label}: ${state} · age ${age}`;
}

export function PipelineAgeChain({ snapshot }: { snapshot: EngineSnapshot }) {
  const lf = snapshot.live_freshness;
  const stages: Stage[] = [
    { key: "market", label: "TICK", diagAgeSec: snapshot.diagnostics.tick_age_sec },
    { key: "features", label: "FEATURES", diagAgeSec: snapshot.diagnostics.features_age_sec },
    { key: "inference", label: "INFERENCE", diagAgeSec: snapshot.diagnostics.inference_age_sec },
    { key: "decision", label: "DECISION", diagAgeSec: snapshot.diagnostics.proposal_age_sec },
  ];

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      <div role="group" aria-label="Pipeline freshness chain" style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
        {stages.map((s, i) => {
          const stage = lf?.[s.key];
          // Backend live_freshness age_ms wins; diagnostics sec fills the gap.
          const ageMs = stage?.age_ms ?? ageSecToMs(s.diagAgeSec);
          const state = (stage?.state ?? "UNKNOWN").toUpperCase();
          const dot = state === "FRESH" ? "var(--green)" : state === "STALE" ? "var(--red)" : "var(--text-faint)";
          return (
            <div key={s.key} style={{ display: "contents" }}>
              <span
                className="conn-chip"
                style={{ gap: 8, borderColor: state === "FRESH" ? undefined : "var(--border-strong)" }}
                title={nodeTitle(s.label, stage, ageMs)}
              >
                <span aria-hidden="true" style={{ width: 7, height: 7, borderRadius: "50%", background: dot, flexShrink: 0 }} />
                <FreshnessMeter label={s.label} state={stage?.state} ageMs={ageMs} />
                <span className="tiny inline-mono faint">{ageMs === null || ageMs === undefined ? "—" : formatAgeMs(ageMs)}</span>
              </span>
              {i < stages.length - 1 && (
                <span aria-hidden="true" className="faint" style={{ fontFamily: "var(--mono)" }}>
                  →
                </span>
              )}
            </div>
          );
        })}
      </div>
      <div className="tiny faint">
        backend overall freshness:{" "}
        <span className="inline-mono">{lf?.overall ? String(lf.overall).toUpperCase() : "UNKNOWN"}</span>
        <span style={{ marginLeft: 10 }}>
          state age: {snapshot.diagnostics.state_age_sec === null ? "—" : formatAgeMs(snapshot.diagnostics.state_age_sec * 1000)}
        </span>
      </div>
    </div>
  );
}
