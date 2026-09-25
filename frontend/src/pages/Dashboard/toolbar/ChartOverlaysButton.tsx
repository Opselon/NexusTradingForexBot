/** Wave-2 LANE B: SMC overlay VISIBILITY popover. Checkbox rows only —
 *  values stay engine-computed (lane-09: no client-side indicator math);
 *  PriceChart/scene assembly does the actual filtering from this state. */
import { useEffect, useRef, useState, type ChangeEvent } from "react";
import { useChartSettings, type OverlayKey } from "../chart/chartSettings";
import { useI18n } from "@/stores/i18nStore";
import "./overlays.css";

/** Rows in menu order: key must match OverlayKey in chart/chartSettings.tsx. */
const OVERLAY_KEYS: ReadonlyArray<OverlayKey> = ["zones", "bos", "midlines", "liq", "orderLines"];

export function ChartOverlaysButton() {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLSpanElement>(null);
  const { overlayVisible, setOverlayVisible } = useChartSettings();
  const t = useI18n((s) => s.t);

  /* Row labels resolve at render (literal keys — dynamic t() keys are banned)
     so a language switch re-translates the menu. */
  const rowLabels: Record<OverlayKey, string> = {
    zones: t("dash.tools.ov_zones", "Zones (FVG / OB / stop-hunt)"),
    bos: t("dash.tools.ov_bos", "BOS lines"),
    midlines: t("dash.tools.ov_mid", "50% equilibrium"),
    liq: t("dash.tools.ov_liq", "Liquidity sweeps"),
    orderLines: t("dash.tools.ov_orders", "Order lines (entry/SL/TP)"),
  };

  // Close on outside pointerdown / Escape while the popover is mounted.
  useEffect(() => {
    if (!open) return;
    const onPointerDown = (e: PointerEvent) => {
      const root = rootRef.current;
      if (root && e.target instanceof Node && !root.contains(e.target)) setOpen(false);
    };
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  return (
    <span className="smc-anchor" ref={rootRef}>
      <button
        className="btn small ghost"
        aria-haspopup="dialog"
        aria-expanded={open}
        title={t("dash.tools.overlays_title", "engine-computed overlays — show/hide only")}
        onClick={() => setOpen((o) => !o)}
      >
        {t("dash.tools.smc_btn", "◫ SMC")}
      </button>
      {open && (
        <div className="smc-pop" role="dialog" aria-label={t("dash.tools.overlays_aria", "SMC overlay visibility")}>
          {OVERLAY_KEYS.map((key) => (
            <label className="smc-row" key={key}>
              <input
                type="checkbox"
                checked={overlayVisible[key]}
                onChange={(e: ChangeEvent<HTMLInputElement>) => setOverlayVisible(key, e.target.checked)}
              />
              <span>{rowLabels[key]}</span>
            </label>
          ))}
        </div>
      )}
    </span>
  );
}
