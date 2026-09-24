/**
 * PURPOSE:  FUNNEL tab — terminal-stage action/stage/gate distributions.
 * OWNER:    uiux-wave5-control
 * CONSUMES: ../useCases (controlCenterQueries.funnel — existing GET), ../model,
 *           @/components/primitives, ../../../../research/ui/lane5Kit (DistBars)
 * PROVIDES: FunnelTab
 * INVARIANTS: the backend's TERMINAL-distribution note renders verbatim; scanned /
 *             total are the backend's own numbers; skeleton + endpoint-failed
 *             states stay visible (no fabricated bars).
 * EXTEND:   new distribution column = a field OperatorFunnelDto already declares.
 */
import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { EmptyState, Panel, Skeleton } from "@/components/primitives";
import { useI18n } from "@/stores/i18nStore";
import { DistBars } from "../../../research/ui/lane5Kit";
import { controlCenterQueries } from "../../useCases";
import { actionLabel } from "../labels";

export function FunnelTab({ hours }: { hours: number | undefined }) {
  const t = useI18n((s) => s.t);
  const funnelQ = useQuery({
    queryKey: ["control-center", "funnel", hours],
    queryFn: ({ signal }) => controlCenterQueries.funnel(hours, signal),
    retry: false,
  });

  // perf: the three DistBars row maps derive once per payload identity — the
  // parent page re-renders on every 15s summary tick with this query idle.
  const data = funnelQ.data;
  const actionRows = useMemo(
    () => (data?.actions ?? []).map((a) => ({ label: a.action ?? "—", count: a.count ?? 0 })),
    [data],
  );
  const stageRows = useMemo(
    () => (data?.stages ?? []).map((a) => ({ label: a.stage ?? "—", count: a.count ?? 0 })),
    [data],
  );
  const gateRows = useMemo(
    () => (data?.gates ?? []).map((a) => ({ label: a.gate ?? "—", count: a.count ?? 0 })),
    [data],
  );

  return (
    <Panel title={t("control-center.panel.funnel", "Terminal-stage funnel")}>
      {funnelQ.isPending ? (
        <Skeleton count={4} />
      ) : funnelQ.isError ? (
        <EmptyState message={t("control-center.err.funnel", "funnel endpoint failed")} />
      ) : (
        <>
          <div className="tiny muted" style={{ marginBottom: 8 }}>
            {funnelQ.data?.note ?? "TERMINAL distributions — the ledger records the final blocking stage per decision."}
          </div>
          <div className="grid cols-3">
            <div>
              <div className="section-title">{t("control-center.section.actions", "actions")}</div>
                            <DistBars rows={actionRows.map((r) => ({ ...r, label: actionLabel(t, r.label) }))} tone="var(--green)" />
            </div>
            <div>
              <div className="section-title">{t("control-center.section.stages", "stages")}</div>
              <DistBars rows={stageRows} tone="var(--amber)" />
            </div>
            <div>
              <div className="section-title">{t("control-center.section.gates", "gates")}</div>
              <DistBars rows={gateRows} tone="var(--red)" />
            </div>
          </div>
          <div className="tiny faint" style={{ marginTop: 6 }}>
            {t("control-center.funnel.footnote", "scanned {scanned} rows / total {total} — summary numbers reconcile against the same window", { scanned: String(funnelQ.data?.scanned_rows ?? "—"), total: String(funnelQ.data?.total ?? "—") })}
          </div>
        </>
      )}
    </Panel>
  );
}
