/**
 * Research tab — read-only research forensics
 * (/api/research/diagnostics|events|evidence|gates|history|trace).
 *
 * Mutations (discover/validate/promote/retry-gate/self-heal) stay in the
 * Research feature — nothing rendered here can change research state.
 */

import { useState } from "react";
import { FreshnessCaption, JsonView, PollControl, QuerySection, usePolling } from "@/features/config/ui/kit";
import { useI18n } from "@/stores/i18nStore";
import { useResearchRead } from "../../hooks";

const KINDS = ["diagnostics", "events", "evidence", "gates", "history", "trace"] as const;
type Kind = (typeof KINDS)[number];

export function ResearchTab() {
  const t = useI18n((s) => s.t);
  const poll = usePolling(30_000);
  const [kind, setKind] = useState<Kind>("diagnostics");
  const [strategyId, setStrategyId] = useState("");
  const params: Record<string, string> = strategyId.trim() ? { strategy_id: strategyId.trim() } : {};
  const needsId = kind === "trace";
  const read = useResearchRead<Record<string, unknown>>(kind, params, !needsId || Object.keys(params).length > 0, poll.paused);

  return (
    <QuerySection<Record<string, unknown>>
      title={t("debug.research.title", "Research forensics (read-only)")}
      accent
      query={read}
      skeletonRows={4}
      emptyMessage={t("debug.research.empty", "Research engine unavailable on this process.")}
      right={
        <>
          <FreshnessCaption fetchedAtMs={read.dataUpdatedAt || null} intervalMs={30_000} stale={poll.paused} />
          <PollControl paused={poll.paused} onToggle={poll.togglePaused} intervalMs={30_000} busy={read.isFetching} />
        </>
      }
    >
      {(data) => (
        <div className="dbg-sec">
          <div className="l3-toolbar">
            <span className="segmented" role="tablist" aria-label={t("debug.research.aria", "research read")}>
              {KINDS.map((k) => (
                <button key={k} role="tab" aria-selected={kind === k} className={kind === k ? "active" : ""} onClick={() => setKind(k)}>
                  {k === "diagnostics"
                    ? t("debug.research.kind_diagnostics", "diagnostics")
                    : k === "events"
                      ? t("debug.research.kind_events", "events")
                      : k === "evidence"
                        ? t("debug.research.kind_evidence", "evidence")
                        : t("debug.research.kind_trace", "trace")}
                </button>
              ))}
            </span>
            <input className="input" placeholder={t("debug.research.strategy_ph", "strategy_id (filters events/evidence/gates/trace)")} value={strategyId} onChange={(e) => setStrategyId(e.target.value)} style={{ minWidth: 260 }} aria-label={t("debug.research.strategy_aria", "strategy id")} />
            {needsId && Object.keys(params).length === 0 && <span className="timestamp-note">{t("debug.research.waits_id", "trace read waits for a strategy_id")}</span>}
          </div>
          {data.available === false && <div className="l3-note warn">{t("debug.research.unavailable", "available=false — research engine is not attached (engine offline or module absent).")}</div>}
          {typeof data.error !== "undefined" && data.error !== null && (
            <div className="l3-note bad">{t("debug.research.err_envelope", "backend error envelope:")} {JSON.stringify(data.error)}</div>
          )}
          <div tabIndex={0} className="l3-scroll dbg-json">
            <JsonView value={data} name={kind} />
          </div>
          <div className="tiny faint" style={{ marginTop: 6 }}>
            {t("debug.research.footer", "read-only panels: mutations (discover/validate/promote/retry-gate/self-heal) stay in the Research feature (lane 5) — nothing here can change research state.")}
          </div>
        </div>
      )}
    </QuerySection>
  );
}
