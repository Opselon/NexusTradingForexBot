/**
 * PURPOSE:  Control Center — operator console shell (hero strip → action rack →
 *           evidence panels → event tape, plus the five evidence tabs).
 * OWNER:    uiux-wave5-control
 * CONSUMES: @tanstack/react-query (summary snapshot), ../model (VO helpers),
 *           @/components/primitives (Segmented), lane5Kit (FreshnessCaption),
 *           ./tones + ./HeroStrip + ./ActionRack + ./OverviewPanels + ./EventTape
 *           + ./DecisionInspector + ./tabs/*, ./control-center.css
 * PROVIDES: default ControlCenterPage (route /control-center, legacy parity)
 * INVARIANTS: truth rules — a value the backend did not supply renders NOT
 *             RECORDED / UNKNOWN (never 0, never invented); engine commands never
 *             change local state (next authoritative snapshot decides); LIVE mode
 *             switch keeps its typed confirmation; every legacy control and string
 *             keeps its meaning; no new endpoints, no new mutations.
 * EXTEND:   new tab = one component in ./tabs wired into the Tab union + Segmented
 *           options; new hero cell = HeroStrip RailCell fed by a model.ts field.
 */
import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import type { ShellPageProps } from "@/app/featureModule";
import { Segmented } from "@/components/primitives";
import { FreshnessCaption } from "../../research/ui/lane5Kit";
import { bool, obj, str } from "../model";
import { controlCenterQueries } from "../useCases";
import { useI18n } from "@/stores/i18nStore";
import { ActionRack } from "./ActionRack";
import { DecisionInspector } from "./DecisionInspector";
import { EventTape } from "./EventTape";

import { HeroStrip } from "./HeroStrip";
import { OverviewPanels } from "./OverviewPanels";
import { CalibrationTab } from "./tabs/CalibrationTab";
import { DecisionsTab } from "./tabs/DecisionsTab";
import { FunnelTab } from "./tabs/FunnelTab";
import { NoTradeTab } from "./tabs/NoTradeTab";
import { OrdersTab } from "./tabs/OrdersTab";
import { modeTone } from "./tones";
import "./control-center.css";

type Tab = "overview" | "decisions" | "funnel" | "no-trade" | "orders" | "calibration";

/** Endpoint provenance chips for the hero — verbatim transport paths this page
 *  already prints elsewhere in its own UI (api.ts constants, no new call). */
const PROVENANCE: ReadonlyArray<string> = [
  "/api/operator/summary",
  "/api/operator/decisions",
  "/api/operator/calibration",
  "/api/engine/toggle",
  "/api/engine/mode",
];

export default function ControlCenterPage(props: ShellPageProps) {
  void props;
  const t = useI18n((s) => s.t);
  const [tab, setTab] = useState<Tab>("overview");
  const [hours, setHours] = useState<number | undefined>(72);
  const [actionFilter, setActionFilter] = useState("");
  const [search, setSearch] = useState("");
  const [detailId, setDetailId] = useState<number | null>(null);
  const qc = useQueryClient();

  const summaryQ = useQuery({
    queryKey: ["control-center", "summary"],
    queryFn: ({ signal }) => controlCenterQueries.summary(signal),
    refetchInterval: 15_000,
    retry: false,
  });

  const s = summaryQ.data;
  const rt = obj(s?.runtime);
  // Legacy control semantics unchanged (missing field → false); chips use the
  // honest tone helpers instead, so unknown state is never rendered as a verdict.
  const engineField = bool(rt.engine_running);
  const running = engineField ?? false;
  const mode = String(rt.runtime_mode ?? rt.execution_mode ?? "UNKNOWN").toUpperCase();
  const isLive = mode.startsWith("LIVE");
  const engineKnown = engineField !== null && !summaryQ.isPending && !summaryQ.isError && s?.available !== false;
  const summaryError =
    summaryQ.error instanceof Error ? summaryQ.error.message : t("control-center.err.summary", "operator/summary request failed");

  const refreshAll = () => {
    void qc.invalidateQueries({ queryKey: ["control-center"] });
    void qc.invalidateQueries({ queryKey: ["engine-snapshot"] });
  };

  return (
    <div className="ctl-page">
      <header className="ctl-herohead">
        <div className="ctl-herohead-main">
          <div className="ctl-kicker">
            <span className="ctl-kicker-bar" aria-hidden="true" />
            {t("control-center.page.eyebrow", "OPERATOR CONSOLE")}
          </div>
          <div className="ctl-title-row">
            <span className="ctl-title-glyph" aria-hidden="true">
              ✜
            </span>
            <h2 className="ctl-title">{t("nav.feature.control-center", "Control Center")}</h2>
          </div>
          <p className="ctl-desc">{t("control-center.page.subtitle", "operator evidence console (read-only ledger views + guarded engine control)")}</p>
          <div className="ctl-provenance">
            {PROVENANCE.map((ep) => (
              <span className="ctl-prov" key={ep}>
                {ep}
              </span>
            ))}
          </div>
        </div>
        <div className="ctl-herohead-status">
          <span className={`badge ${isLive ? "bad" : "good"}`} title={t("control-center.a11y.mode_banner", "mode banner")}>
            {mode}
          </span>
          <FreshnessCaption
            timestamp={str(rt.snapshot_timestamp)}
            source="operator/summary"
            isFetching={summaryQ.isFetching}
            error={summaryQ.isError}
          />
        </div>
      </header>

      {isLive && (
        <div className="banner down" role="alert">
          {t("control-center.banner.live", "LIVE mode dispatches real orders to the broker. Verify risk state before any operator action.")}
        </div>
      )}

      <Segmented
        options={[
          { id: "overview" as const, label: t("control-center.tab.overview", "Overview") },
          { id: "decisions" as const, label: t("control-center.tab.decisions", "Decisions") },
          { id: "funnel" as const, label: t("control-center.tab.funnel", "Funnel") },
          { id: "no-trade" as const, label: t("control-center.tab.no_trade", "NO_TRADE") },
          { id: "orders" as const, label: t("control-center.tab.orders", "Orders") },
          { id: "calibration" as const, label: t("control-center.tab.calibration", "Calibration") },
        ]}
        value={tab}
        onChange={setTab}
      />

      <div className="ctl-body">
        {tab === "overview" && (
          <>
            <HeroStrip
              summary={s}
              pending={summaryQ.isPending}
              error={summaryQ.isError}
              errorMessage={summaryError}
              onRetry={() => void summaryQ.refetch()}
            />
            <ActionRack
              running={running}
              mode={mode}
              engineKnown={engineKnown}
              modeTone={modeTone(mode).tone}
              onSettled={refreshAll}
            />
            <OverviewPanels
              summary={s}
              pending={summaryQ.isPending}
              error={summaryQ.isError}
              errorMessage={summaryError}
              onRetry={() => void summaryQ.refetch()}
            />
            <EventTape onInspect={setDetailId} />
          </>
        )}

        {tab === "decisions" && (
          <DecisionsTab
            hours={hours}
            actionFilter={actionFilter}
            search={search}
            onHours={setHours}
            onActionFilter={setActionFilter}
            onSearch={setSearch}
            onInspect={setDetailId}
          />
        )}
        {tab === "funnel" && <FunnelTab hours={hours} />}
        {tab === "no-trade" && <NoTradeTab hours={hours} />}
        {tab === "orders" && <OrdersTab />}
        {tab === "calibration" && <CalibrationTab />}
      </div>

      {detailId !== null && <DecisionInspector id={detailId} onClose={() => setDetailId(null)} />}
    </div>
  );
}
