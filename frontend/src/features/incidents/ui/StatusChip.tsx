/**
 * PURPOSE:  Status state pill — open/ack/resolved/other as a chip with a
 *           subtle glow on the active (open) state, pulse wrapped in
 *           prefers-reduced-motion (see incidents.css).
 * OWNER:    uiux-w6-incidents  (future edits belong to this lane)
 * CONSUMES: features/incidents/ui/incidentLook (statusClass — the backend
 *           status vocabulary already used by model.ts), styles/theme.css
 *           tokens via ./incidents.css.
 * PROVIDES: default export StatusChip — display only, no behavior.
 * INVARIANTS: the label is the backend status verbatim (underscores kept as
 *             spaces, never reworded); the glow/pulse only restates a state
 *             the backend already sent; an unknown status renders muted,
 *             never as a guessed open/resolved color.
 * EXTEND:   add a state mapping in incidentLook.statusClass — this component
 *           stays a pure class picker.
 */

import { statusClass } from "./incidentLook";
import "./incidents-command.css";

export default function StatusChip({ status, label }: { status: string | null | undefined; label?: string }) {
  const state = statusClass(status);
  const text = status ? status.replace(/_/g, " ") : "UNKNOWN";
  const glyph = state === "open" ? "●" : state === "resolved" ? "✓" : "○";
  return (
    <span className={`inc-status inc-status--${state}`} title={label ?? text}>
      <span className="inc-status__glyph" aria-hidden="true">
        {glyph}
      </span>
      {text}
    </span>
  );
}
