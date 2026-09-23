/**
 * ModeIndicator — LIVE / PAPER / SHADOW mode display.
 *
 * Safety contract (unchanged):
 *  - The mode shown is ALWAYS the backend's `runtime_mode` / `execution_mode`.
 *  - A backend-reported `mode_source_mismatch` (BUG-232) renders as a hard
 *    error state — a LIVE badge is never trusted without a matching
 *    MT5_LIVE data source.
 *  - This component NEVER infers or defaults the mode client-side.
 *
 * Wave-2 pass (Lane P): token-driven depth, LIVE hazard frame, and an honest
 * source sub-chip built only from fields the snapshot itself carries.
 */

import type { EngineSnapshot } from "@/types/domain";
import { useI18n } from "@/stores/i18nStore";
import "./shell.css";

interface Props {
  snapshot: EngineSnapshot | undefined;
}

export function ModeIndicator({ snapshot }: Props) {
  const t = useI18n((s) => s.t);
  if (!snapshot) {
    return <span className="mode-badge unknown">{t("ui.mode.none", "MODE —")}</span>;
  }
  const mode = (snapshot.runtime_mode ?? snapshot.execution_mode ?? "UNKNOWN").toUpperCase();

  if (snapshot.mode_source_mismatch) {
    return (
      <span
        className="mode-badge live mismatch"
        title={t(
          "ui.mode.mismatch_title",
          "MODE-SOURCE MISMATCH: runtime_mode={m} but data_source={d} (BUG-232 guard). Do not trust this as real broker state.",
          { m: mode, d: snapshot.data_source ?? "UNKNOWN" },
        )}
      >
        ⚠ {mode} {t("ui.mode.mismatch", "(SOURCE MISMATCH)")}
      </span>
    );
  }

  const cls = mode.startsWith("LIVE") ? "live" : mode === "PAPER" ? "paper" : mode === "SHADOW" ? "shadow" : "unknown";
  return (
    <span
      className={`mode-badge ${cls}`}
      title={`execution_mode=${snapshot.execution_mode ?? "—"} · data_source=${snapshot.data_source ?? "—"} · adapter=${snapshot.adapter_class ?? "—"}`}
    >
      {mode === "UNKNOWN" ? t("ui.mode.none", "MODE —") : mode}
      {mode.startsWith("LIVE") && (
        <span className="mode-src tiny" aria-hidden="true">
          {t("ui.mode.real_orders", "REAL ORDERS")}
        </span>
      )}
    </span>
  );
}
