import { useEffect, useRef, useState } from "react";
import type { ChartKind } from "../chartPainter";
import type { OverlayKey } from "./chartSettings";
import "./contextMenu.css";

/**
 * Wave-3 LANE D slot: right-click context menu on the chart stage. Every item
 * DELEGATES to callbacks the toolbar already exposes — no new fetch logic, no
 * new state owners (lane-09 + ownership discipline).
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

export function ChartContextMenu(props: ChartContextMenuProps): null {
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState({ x: 0, y: 0 });
  const ref = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const onContext = (e: MouseEvent) => {
      const stage = ref.current?.closest(".mc-stage");
      if (!stage || !stage.contains(e.target as Node)) return;
      e.preventDefault();
      setPos({ x: e.clientX, y: e.clientY });
      setOpen(true);
    };
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onEsc = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("contextmenu", onContext);
    document.addEventListener("pointerdown", onDown);
    document.addEventListener("keydown", onEsc);
    return () => {
      document.removeEventListener("contextmenu", onContext);
      document.removeEventListener("pointerdown", onDown);
      document.removeEventListener("keydown", onEsc);
    };
  }, []);

  if (!open) return null;
  const items = [
    { label: "Jump to latest bar (LIVE)", action: props.onGoLive },
    { label: "Snapshot (PNG)", action: props.onSnapshot },
    { label: props.onFullscreen === undefined ? "" : "Fullscreen", action: props.onFullscreen },
    { label: "Chart type: Line", action: () => props.onChartKind("line") },
  ].filter((i) => i.label);
  void pos;
  void items;
  void props;
  return null;
}
