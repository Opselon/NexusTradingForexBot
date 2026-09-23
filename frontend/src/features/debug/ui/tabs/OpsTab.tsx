/**
 * Ops tab — the two legacy-parity panels the debug hub owns but the old
 * page never mounted: the interactive simulation injector and the live
 * Telegram notifier worker telemetry.
 *
 * SAFETY: POST /api/simulation/tick is server-enforced to NO-OP unless the
 * execution mode is explicitly SIMULATION/PAPER (server.py); this UI only
 * offers the button and renders the backend's refusal verbatim. The
 * notifier panel is a pure read of /api/observability/stats.
 */

import { SimulationPanel } from "../SimulationPanel";
import { TelegramWorkerPanel } from "../TelegramWorkerPanel";
import { useI18n } from "@/stores/i18nStore";

export function OpsTab() {
  const t = useI18n((s) => s.t);
  return (
    <div className="dbg-sec dbg-ops">
      <div className="l3-note warn dbg-safety">
        <b>{t("debug.ops.safety_b", "Side-effect surface.")}</b> {t("debug.ops.safety_pre", "The simulation buttons below POST")} <span className="inline-mono">/api/simulation/tick</span>. {t("debug.ops.safety_post", "The backend refuses the injection unless execution mode is SIMULATION/PAPER, and its refusal message is rendered verbatim — on a LIVE engine every button answers \"refused\", which is the correct, honest outcome.")}
      </div>
      <SimulationPanel />
      <TelegramWorkerPanel />
    </div>
  );
}
