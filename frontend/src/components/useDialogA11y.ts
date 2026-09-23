import { useEffect, useRef, type RefObject } from "react";

/**
 * Shared dialog keyboard contract (single source of truth — every overlay in
 * the console adopts it so behavior is identical everywhere):
 *
 *  1. focus moves INTO the dialog when it opens (container, or an explicit
 *     initialFocusRef such as a typed-confirmation input);
 *  2. focus RETURNS to the element that opened it when it closes;
 *  3. Tab / Shift+Tab cycle only within the dialog (trap);
 *  4. Escape closes — but ONLY while focus is inside this dialog, so a stacked
 *     dialog never closes the one beneath it (the guard proven in ConfirmModal
 *     and the research Drawer, wave 3).
 *
 * `escEnabled` lets a caller preserve a domain Esc contract (busy gates,
 * legacy escalation semantics) while still getting focus-in/trap/restore.
 */
export function useDialogA11y(
  containerRef: RefObject<HTMLElement | null>,
  onEscape: () => void,
  opts: {
    escEnabled?: boolean;
    initialFocus?: boolean;
    initialFocusRef?: RefObject<HTMLElement | null>;
  } = {},
): void {
  const { escEnabled = true, initialFocus = true, initialFocusRef } = opts;
  const escapeRef = useRef(onEscape);
  escapeRef.current = onEscape;
  const escRef = useRef(escEnabled);
  escRef.current = escEnabled;

  // Focus enters on open, returns to the trigger on close.
  useEffect(() => {
    const prev = document.activeElement as HTMLElement | null;
    const target = initialFocusRef?.current ?? containerRef.current;
    if (initialFocus && target) {
      if (target === containerRef.current && !target.hasAttribute("tabindex")) {
        target.setAttribute("tabindex", "-1");
      }
      target.focus({ preventScroll: true });
    }
    return () => prev?.focus?.();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Escape (guarded) + Tab trap.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const root = containerRef.current;
      // Stacked dialogs: only the dialog holding focus owns the keyboard.
      if (!root || !root.contains(document.activeElement)) return;
      if (e.key === "Escape") {
        if (escRef.current) escapeRef.current();
        return;
      }
      if (e.key === "Tab") {
        const focusables = Array.from(
          root.querySelectorAll<HTMLElement>(
            'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
          ),
        ).filter((el) => !el.hasAttribute("disabled"));
        if (focusables.length === 0) return;
        const first = focusables[0] as HTMLElement;
        const last = focusables[focusables.length - 1] as HTMLElement;
        const active = document.activeElement;
        if (e.shiftKey && (active === first || !root.contains(active))) {
          e.preventDefault();
          last.focus();
        } else if (!e.shiftKey && (active === last || !root.contains(active))) {
          e.preventDefault();
          first.focus();
        }
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
}
