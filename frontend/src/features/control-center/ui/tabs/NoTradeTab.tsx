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
import { useQuery } from "@tanstack/react-query";
import { DataTable, EmptyState, Panel, Skeleton } from "@/components/primitives";
import { formatDateTime } from "@/lib/format";
import { DistBars } from "../../../research/ui/lane5Kit";
import { arr, str } from "../../model";
import { controlCenterQueries } from "../../useCases";
import { useI18n } from "@/stores/i18nStore";

export function NoTradeTab({ hours }: { hours: number | undefined }) {
  const t = useI18n((s) => s.t);
  const noTradeQ = useQuery({
    queryKey: ["control-center", "no-trade", hours],
    queryFn: ({ signal }) => controlCenterQueries.noTrade(hours, 8, signal),
    retry: false,
  });

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
            <DistBars rows={(noTradeQ.data?.gates ?? []).map((g) => ({ label: g.gate ?? "—", count: g.count ?? 0 }))} tone="var(--red)" />
            <div className="section-title" style={{ marginTop: 8 }}>
              {t("control-center.section.regimes", "regimes")}
            </div>
            <DistBars rows={(noTradeQ.data?.regimes ?? []).map((g) => ({ label: g.regime ?? "—", count: g.count ?? 0 }))} />
          </div>
          <div>
            <div className="section-title">{t("control-center.section.hourly_trend", "hourly trend")}</div>
            <DistBars rows={(noTradeQ.data?.hourly_trend ?? []).map((g) => ({ label: (g.hour ?? "—").slice(5, 13), count: g.count ?? 0 }))} tone="var(--violet)" />
            <div className="section-title" style={{ marginTop: 8 }}>
              {t("control-center.section.recent_examples", "recent examples")}
            </div>
            <DataTable headers={[{ label: t("control-center.th.at", "at") }, { label: t("control-center.th.reason", "reason") }, { label: t("control-center.th.gate", "gate") }]}>
              {arr(noTradeQ.data?.recent).map((r, i) => (
                <tr key={i}>
                  <td className="tiny">{formatDateTime(str(r.generated_at))}</td>
                  <td className="tiny">{str(r.reason_code) ?? "—"}</td>
                  <td className="tiny">{str(r.blocked_by) ?? ""}</td>
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
