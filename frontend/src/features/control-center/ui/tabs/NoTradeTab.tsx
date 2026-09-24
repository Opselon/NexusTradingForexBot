/**
 * PURPOSE:  NO_TRADE tab — blocking gates, regimes, hourly trend, recent examples.
 * OWNER:    uiux-wave5-control
 * CONSUMES: ../useCases (controlCenterQueries.noTrade — existing GET), ../model,
 *           @/components/primitives, @/lib/format,
 *           ../../../../research/ui/lane5Kit (DistBars, StatusPill)
 * PROVIDES: NoTradeTab
 * INVARIANTS: every bucket label/count is a backend value; model_direction_unresolved
 *             renders as returned (never defaulted); skeleton + failed states visible.
 * EXTEND:   new breakdown = a field OperatorNoTradeDto already declares.
 */
import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { DataTable, EmptyState, Panel, Skeleton } from "@/components/primitives";
import { formatDateTime } from "@/lib/format";
import { useI18n } from "@/stores/i18nStore";
import { DistBars } from "../../../research/ui/lane5Kit";
import { arr, str } from "../../model";
import { controlCenterQueries } from "../../useCases";

export function NoTradeTab({ hours }: { hours: number | undefined }) {
  const t = useI18n((s) => s.t);
  const noTradeQ = useQuery({
    queryKey: ["control-center", "no-trade", hours],
    queryFn: ({ signal }) => controlCenterQueries.noTrade(hours, 8, signal),
    retry: false,
  });

  // perf: four derivations over the no-trade payload memoized per identity —
  // re-used on the parent's 15s summary re-renders without re-mapping.
  const data = noTradeQ.data;
  const gateRows = useMemo(
    () => (data?.gates ?? []).map((g) => ({ label: g.gate ?? "—", count: g.count ?? 0 })),
    [data],
  );
  const regimeRows = useMemo(
    () => (data?.regimes ?? []).map((g) => ({ label: g.regime ?? "—", count: g.count ?? 0 })),
    [data],
  );
  const trendRows = useMemo(
    () => (data?.hourly_trend ?? []).map((g) => ({ label: (g.hour ?? "—").slice(5, 13), count: g.count ?? 0 })),
    [data],
  );
  const recentRows = useMemo(
    () => arr(data?.recent).map((r) => ({
      key: `${str(r.generated_at) ?? ""}-${str(r.reason_code) ?? ""}-${str(r.blocked_by) ?? ""}`,
      at: formatDateTime(str(r.generated_at)),
      reason: str(r.reason_code) ?? "—",
      gate: str(r.blocked_by) ?? "",
    })),
    [data],
  );

  return (
    <Panel title={t("control-center.panel.no_trade", "NO_TRADE forensics")} tight>
      {noTradeQ.isPending ? (
        <Skeleton count={4} />
      ) : noTradeQ.isError ? (
        <EmptyState message={t("control-center.err.no_trade", "no-trade endpoint failed")} />
      ) : (
        <div className="grid cols-2">
          <div>
            <div className="section-title">{t("control-center.section.blocking_gates", "blocking gates")}</div>
            <DistBars rows={gateRows} tone="var(--red)" />
            <div className="section-title" style={{ marginTop: 8 }}>
                          {t("control-center.section.regimes", "regimes")}
                        </div>
            <DistBars rows={regimeRows} />
          </div>
          <div>
            <div className="section-title">{t("control-center.section.hourly_trend", "hourly trend")}</div>
            <DistBars rows={trendRows} tone="var(--violet)" />
            <div className="section-title" style={{ marginTop: 8 }}>
                          {t("control-center.section.recent_examples", "recent examples")}
                        </div>
            <DataTable headers={[{ label: t("control-center.th.at", "at") }, { label: t("control-center.th.reason", "reason") }, { label: t("control-center.th.gate", "gate") }]}>
              {recentRows.map((r, i) => (
                <tr key={r.key ?? i}>
                  <td className="tiny">{r.at}</td>
                  <td className="tiny">{r.reason}</td>
                  <td className="tiny">{r.gate}</td>
                </tr>
              ))}
            </DataTable>
            <div className="tiny faint" style={{ marginTop: 4 }}>
              {t("control-center.nt.unresolved", "model direction unresolved: {n}", { n: String(noTradeQ.data?.model_direction_unresolved ?? "—") })}
            </div>
          </div>
        </div>
      )}
    </Panel>
  );
}
