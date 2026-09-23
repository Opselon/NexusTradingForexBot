/**
 * Wave-3 LANE B: keyboard chart navigation (a11y + power-user speed).
 *
 * ArrowLeft/Right pan one bar (Shift = 10); PageUp/PageDown jump a viewport;
 * Home/End snap to the oldest/newest available bar; "L" re-pins to live.
 * The hook NEVER acts while an input/textarea/select or a contentEditable
 * element has focus (so typing in any page control is unaffected) and it
 * ignores events carrying ctrl/meta/alt modifiers (those belong to the
 * browser/OS).
 *
 * Binding: a DOCUMENT-level keydown listener — the repo convention
 * (chart/ChartContextMenu.tsx, toolbar/ChartOverlaysButton.tsx). The stage
 * carries tabIndex=0, so clicking the chart focuses it and the keystroke
 * bubbles to the document; navForKey() then decides whether it is ours and
 * preventDefault()s it when handled (PageUp/PageDown/Home/End would scroll
 * the page otherwise) — the same contract the wheel handler enforces.
 *
 * The view math mirrors PriceChart's applyLeft()/effLeft exactly
 * (PriceChart.tsx L184-208), so a keyboard pan lands where a drag/wheel pan
 * would, including the followLive re-pin at the live edge.
 *
 * lane-09: DOM events + navigation only. No indicator math.
 */
import { useEffect, useRef, type RefObject } from "react";

export interface KeyboardNavView {
  /** Slots spanned by the plot (the zoom level). */
  visible: number;
  /** Absolute index of the leftmost slot (meaningful when not followLive). */
  left: number;
  /** Pinned at the right edge: new bars keep sliding the window in. */
  followLive: boolean;
}

export interface KeyboardNavOpts {
  stageRef: RefObject<HTMLDivElement | null>;
  view: KeyboardNavView;
  setView: (updater: (v: KeyboardNavView) => KeyboardNavView) => void;
  /** Absolute bar count (window model right bound, incl. future space). */
  barCount: number;
}

/** Input-ish element types that must keep their own keystrokes. */
const TYPING_TAGS = new Set(["INPUT", "TEXTAREA", "SELECT"]);

/** Shift multiplies the arrow-key pan step (contract: Shift = 10 bars). */
export const NAV_SHIFT_STEP = 10;

export interface NavKeyEventLike {
  key: string;
  shiftKey?: boolean;
  ctrlKey?: boolean;
  metaKey?: boolean;
  altKey?: boolean;
}

/**
 * True when the keystroke belongs to the focused control, not the chart:
 * an input/textarea/select is focused, or the active element lives inside
 * (or is) a contentEditable region. `null` (nothing focused) is safe.
 */
export function isTypingFocus(active: Element | null): boolean {
  if (!active) return false;
  if (TYPING_TAGS.has(active.tagName)) return true;
  // isContentEditable is inherited by children of an editable region, so
  // checking the active element alone covers "[contenteditable]" anywhere.
  // The DOM types put isContentEditable on HTMLElement, not Element.
  return (active as HTMLElement | null)?.isContentEditable === true;
}

/** EffLeft / clamp / followLive formulas, transcribed from PriceChart
 *  (L184-208): barCount = len + maxFutureFor(visible). */
function viewMath(visible: number, barCount: number) {
  const future = Math.floor(visible * 0.4); // maxFutureFor (PriceChart L80)
  const len = Math.max(0, barCount - future);
  return {
    /** left of a window pinned to the newest bar */
    liveLeft: Math.max(0, len - visible),
    /** leftmost slot that still keeps the plot full (incl. future space) */
    leftMax: Math.max(0, len + future - visible),
  };
}

/**
 * Map one keydown to a view updater, or null when the chart ignores the key
 * (unhandled key, ctrl/meta/alt held, no window to navigate, or the view
 * would not change — so a held key at the boundary does not thrash state).
 *
 * Pure: no DOM, no React — this is what the tests pin.
 */
export function navForKey(
  e: NavKeyEventLike,
  view: KeyboardNavView,
  barCount: number,
): ((v: KeyboardNavView) => KeyboardNavView) | null {
  if (e.ctrlKey || e.metaKey || e.altKey) return null;
  const { visible, left, followLive } = view;
  if (!(visible > 0) || !(barCount > 0)) return null;
  const { liveLeft, leftMax } = viewMath(visible, barCount);
  /** Current effective left: followLive windows live at the live edge. */
  const effLeft = followLive ? liveLeft : Math.min(Math.max(left, 0), leftMax);

  /** Clamp like applyLeft() and re-derive followLive at the boundary. */
  const withLeft = (next: number): KeyboardNavView => {
    const c = Math.min(Math.max(Math.round(next), 0), leftMax);
    return { visible, left: c, followLive: c === liveLeft };
  };

  const step = e.shiftKey ? NAV_SHIFT_STEP : 1;
  let next: KeyboardNavView;
  switch (e.key) {
    case "ArrowLeft":
      next = withLeft(effLeft - step);
      break;
    case "ArrowRight":
      next = withLeft(effLeft + step);
      break;
    case "PageUp":
      next = withLeft(effLeft - visible);
      break;
    case "PageDown":
      next = withLeft(effLeft + visible);
      break;
    case "Home":
      next = withLeft(0);
      break;
    case "End":
      // newest bar = live edge; landing there re-pins followLive (the same
      // rule applyLeft() applies when a drag ends on the live edge).
      next = withLeft(liveLeft);
      break;
    case "l":
    case "L":
      next = withLeft(liveLeft);
      break;
    default:
      return null;
  }
  if (next.left === left && next.followLive === followLive && next.visible === visible) {
    return null; // nothing would change — do not steal the keystroke
  }
  return () => next;
}

export function useChartKeyboardNav({
  stageRef,
  view,
  setView,
  barCount,
}: KeyboardNavOpts): void {
  // Render-time mirrors for the imperative listener (bound once; must never
  // read stale state) — the same pattern PriceChart uses for drag/wheel.
  const viewRef = useRef(view);
  viewRef.current = view;
  const barCountRef = useRef(barCount);
  barCountRef.current = barCount;

  useEffect(() => {
    const onKey = (e: KeyboardEvent): void => {
      // Typing wins: never steal a key from a form control / editable region.
      if (isTypingFocus(document.activeElement)) return;
      // No chart stage mounted (state block / no bars): nothing to navigate.
      if (!stageRef.current) return;
      const apply = navForKey(e, viewRef.current, barCountRef.current);
      if (!apply) return;
      e.preventDefault(); // keep PageUp/PageDown/Home/End from scrolling
      setView(apply);
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
    };
    // Stable deps: stageRef and setView keep identity for the component's
    // lifetime; view/barCount flow through the render-time refs above, so
    // pans never re-subscribe the listener.
  }, [stageRef, setView]);
}
