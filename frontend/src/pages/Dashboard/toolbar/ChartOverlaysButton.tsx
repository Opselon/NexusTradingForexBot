/** Wave-2 LANE B: SMC overlay VISIBILITY popover. Checkbox rows only —
 *  values stay engine-computed (lane-09: no client-side indicator math);
 *  PriceChart/scene assembly does the actual filtering from this state. */
import { useEffect, useRef, useState, type ChangeEvent } from "react";
import { useChartSettings, type OverlayKey } from "../chart/chartSettings";
import "./overlays.css";

/** Rows in menu order: key must match OverlayKey in chart/chartSettings.tsx. */
const OVERLAY_ROWS: ReadonlyArray<{ key: OverlayKey; label: string }> = [
  { key: "zones", label: "Zones (FVG / OB / stop-hunt)" },
  { key: "bos", label: "BOS lines" },
  { key: "midlines", label: "50% equilibrium" },
  { key: "liq", label: "Liquidity sweeps" },
  { key: "orderLines", label: "Order lines (entry/SL/TP)" },
];

export function ChartOverlaysButton() {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLSpanElement>(null);
  const { overlayVisible, setOverlayVisible } = useChartSettings();

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
        title="engine-computed overlays — show/hide only"
        onClick={() => setOpen((o) => !o)}
      >
        ◫ SMC
      </button>
      {open && (
        <div className="smc-pop" role="dialog" aria-label="SMC overlay visibility">
          {OVERLAY_ROWS.map(({ key, label }) => (
            <label className="smc-row" key={key}>
              <input
                type="checkbox"
                checked={overlayVisible[key]}
                onChange={(e: ChangeEvent<HTMLInputElement>) => setOverlayVisible(key, e.target.checked)}
              />
              <span>{label}</span>
            </label>
          ))}
        </div>
      )}
    </span>
  );
}
