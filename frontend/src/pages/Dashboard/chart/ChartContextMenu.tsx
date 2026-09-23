import { useEffect, useLayoutEffect, useRef, useState } from "react";
import type { ChartKind } from "../chartPainter";
import type { OverlayKey } from "./chartSettings";
import "./contextMenu.css";

/**
 * Wave-3 LANE D: right-click context menu on the chart stage. Every item
 * DELEGATES to callbacks the toolbar already exposes — no new fetch logic, no
 * new state owners, no derived math (lane-09 + ownership discipline).
 *
 * Overlay rows only flip VISIBILITY of engine values via onOverlayToggle; they
 * never mutate or recompute what the engine produced.
 */
export interface ChartContextMenuProps {
  chartKind: ChartKind;
  onChartKind: (k: ChartKind) => void;
  onSnapshot: () => void;
  onFullscreen: () => void;
  onGoLive: () => void;
  /** SMC overlay visibility (engine values — visibility toggles only). */
  overlayVisible: Record<OverlayKey, boolean>;
  onOverlayToggle: (key: OverlayKey, visible: boolean) => void;
}

const CHART_KINDS: ReadonlyArray<{ kind: ChartKind; label: string }> = [
  { kind: "line", label: "Line" },
  { kind: "area", label: "Area" },
  { kind: "hollow", label: "Hollow" },
  { kind: "candles", label: "Candles" },
];

/** Labels mirror toolbar/ChartOverlaysButton so both entry points read alike. */
const OVERLAY_ROWS: ReadonlyArray<{ key: OverlayKey; label: string }> = [
  { key: "zones", label: "Zones (FVG / OB / stop-hunt)" },
  { key: "bos", label: "BOS lines" },
  { key: "midlines", label: "50% equilibrium" },
  { key: "liq", label: "Liquidity sweeps" },
  { key: "orderLines", label: "Order lines (entry/SL/TP)" },
];

/** Viewport edge guard for the fixed-position menu (px). */
const EDGE = 6;

export function ChartContextMenu(props: ChartContextMenuProps) {
  const [open, setOpen] = useState(false);
  /** Raw cursor position from the contextmenu event. */
  const [pos, setPos] = useState({ x: 0, y: 0 });
  /** Viewport-clamped position applied before paint (layout effect). */
  const [box, setBox] = useState({ x: 0, y: 0 });
  const ref = useRef<HTMLDivElement | null>(null);
  /** Focused element before the menu opened — restored on close. */
  const prevFocusRef = useRef<HTMLElement | null>(null);
  /** The .mc-stage that opened the menu — keyboard fallback focus target. */
  const stageRef = useRef<HTMLElement | null>(null);
  const wasOpenRef = useRef(false);

  /* Open / close plumbing: contextmenu inside .mc-stage opens at the cursor,
     outside pointerdown and Escape close. Registered once; state setters bail
     out when the value is unchanged. */
  useEffect(() => {
    const onContext = (e: MouseEvent) => {
      const target = e.target instanceof Element ? e.target : null;
      // Right-click on the menu itself: suppress the native menu, keep it open.
      if (target && ref.current?.contains(target)) {
        e.preventDefault();
        return;
      }
      const stage = target?.closest?.(".mc-stage");
      if (!stage) return;
      e.preventDefault();
      stageRef.current = stage as HTMLElement;
      prevFocusRef.current =
        document.activeElement instanceof HTMLElement ? document.activeElement : null;
      setPos({ x: e.clientX, y: e.clientY });
      setOpen(true);
    };
    const onDown = (e: PointerEvent) => {
      const target = e.target instanceof Node ? e.target : null;
      if (target && ref.current?.contains(target)) return;
      setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    const onResize = () => setOpen(false);
    document.addEventListener("contextmenu", onContext);
    document.addEventListener("pointerdown", onDown);
    document.addEventListener("keydown", onKey);
    window.addEventListener("resize", onResize);
    return () => {
      document.removeEventListener("contextmenu", onContext);
      document.removeEventListener("pointerdown", onDown);
      document.removeEventListener("keydown", onKey);
      window.removeEventListener("resize", onResize);
    };
  }, []);

  /* Clamp to the viewport, then move focus to the first item — both happen in
     a layout effect, so the raw cursor position never paints unclamped. */
  useLayoutEffect(() => {
    if (!open) return;
    const el = ref.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    setBox({
      x: Math.max(EDGE, Math.min(pos.x, window.innerWidth - r.width - EDGE)),
      y: Math.max(EDGE, Math.min(pos.y, window.innerHeight - r.height - EDGE)),
    });
    el.querySelector<HTMLElement>('[role="menuitem"]')?.focus();
  }, [open, pos]);

  /* Focus management on close: previous focus if it is still in the document,
     otherwise the stage (tabIndex 0) so chart keyboard nav keeps working. */
  useEffect(() => {
    if (open) {
      wasOpenRef.current = true;
      return;
    }
    if (!wasOpenRef.current) return;
    wasOpenRef.current = false;
    const prev = prevFocusRef.current;
    prevFocusRef.current = null;
    const stage = stageRef.current;
    if (prev && prev !== document.body && document.contains(prev)) {
      prev.focus();
      return;
    }
    if (stage && document.contains(stage)) stage.focus();
  }, [open]);

  const close = () => setOpen(false);
  /** Action item: delegate then close (checkable rows keep the menu open). */
  const run = (fn: () => void) => {
    fn();
    close();
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    const el = ref.current;
    if (!el) return;
    const items = Array.from(el.querySelectorAll<HTMLElement>('[role="menuitem"]'));
    if (items.length === 0) return;
    const idx = items.indexOf(document.activeElement as HTMLElement);
    switch (e.key) {
      case "ArrowDown":
        e.preventDefault();
        items[(idx < 0 ? 0 : idx + 1) % items.length]?.focus();
        break;
      case "ArrowUp":
        e.preventDefault();
        items[idx < 0 ? items.length - 1 : (idx - 1 + items.length) % items.length]?.focus();
        break;
      case "Home":
        e.preventDefault();
        items[0]?.focus();
        break;
      case "End":
        e.preventDefault();
        items[items.length - 1]?.focus();
        break;
      case "Enter": {
        // Explicit activation: preventDefault stops the native synthesize-click,
        // then a single manual click runs the row handler.
        e.preventDefault();
        const item = items[idx];
        if (item) item.click();
        break;
      }
      case "Tab":
        e.preventDefault();
        close();
        break;
      default:
        break;
    }
  };

  if (!open) return null;

  return (
    <div
      ref={ref}
      className="cx-menu"
      role="menu"
      aria-label="Chart"
      style={{ left: box.x, top: box.y }}
      onKeyDown={onKeyDown}
      onContextMenu={(e) => e.preventDefault()}
    >
      <button type="button" role="menuitem" className="cx-menu__item" onClick={() => run(props.onGoLive)}>
        Jump to latest bar
      </button>
      <button type="button" role="menuitem" className="cx-menu__item" onClick={() => run(props.onSnapshot)}>
        Snapshot PNG
      </button>
      <button type="button" role="menuitem" className="cx-menu__item" onClick={() => run(props.onFullscreen)}>
        Fullscreen
      </button>

      <div role="group" aria-label="Chart type">
        <div className="cx-menu__label" aria-hidden="true">
          Chart type
        </div>
        {CHART_KINDS.map((k) => (
          <button
            key={k.kind}
            type="button"
            role="menuitem"
            className="cx-menu__item"
            aria-checked={props.chartKind === k.kind}
            onClick={() => run(() => props.onChartKind(k.kind))}
          >
            {k.label}
          </button>
        ))}
      </div>

      <div className="cx-menu__sep" role="separator" />

      <div role="group" aria-label="SMC overlay visibility">
        <div className="cx-menu__label" aria-hidden="true">
          SMC overlays
        </div>
        {OVERLAY_ROWS.map((row) => (
          <button
            key={row.key}
            type="button"
            role="menuitem"
            className="cx-menu__item"
            aria-checked={Boolean(props.overlayVisible[row.key])}
            onClick={() => props.onOverlayToggle(row.key, !props.overlayVisible[row.key])}
          >
            {row.label}
          </button>
        ))}
      </div>
    </div>
  );
}
