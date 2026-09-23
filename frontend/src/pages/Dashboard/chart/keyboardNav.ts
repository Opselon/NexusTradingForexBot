import { type RefObject } from "react";

/**
 * Wave-3 LANE B seam: keyboard chart navigation (a11y + power-user speed).
 *
 * ArrowLeft/Right step the view; PageUp/PageDown jump a viewport;
 * Home/End snap to the oldest/newest available bar; "L" re-pins to live.
 * The hook NEVER acts while an input/textarea/select or a contentEditable
 * element has focus (so typing in any page control is unaffected) and it
 * ignores events with ctrl/meta/alt modifiers.
 */
export interface KeyboardNavView {
  visible: number;
  left: number;
  followLive: boolean;
}

export function useChartKeyboardNav(_opts: {
  stageRef: RefObject<HTMLDivElement | null>;
  view: KeyboardNavView;
  setView: (updater: (v: KeyboardNavView) => KeyboardNavView) => void;
  /** Absolute bar count (window model right bound). */
  barCount: number;
}): void {
  /* LANE B implements. */
}
